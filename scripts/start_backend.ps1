# Start the AI-NetTune backend with a Python that actually works.
#
# Why this exists:
#   The default `python` on this machine is D:\miniconda (Python 3.9). The project
#   uses PEP 604 annotations (``dict | None``), which need Python 3.10+.
#   Running `python -m uvicorn main:app` under 3.9 dies with:
#       TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File F:\AI-NetTune\AI-NetTune\scripts\start_backend.ps1
#   powershell -ExecutionPolicy Bypass -File ...\start_backend.ps1 -Port 8001
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 decodes .ps1 files
#       without a BOM as ANSI, so non-ASCII text can break parsing.

param(
    [int]$Port = 8000,
    [string]$HostAddress = '127.0.0.1'
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# Candidate interpreters, most specific first.
$candidates = @(
    'D:\python\Python313\python.exe',
    'D:\python\Python\Python313\python.exe'
)
foreach ($name in @('python3.13', 'python3.12', 'python3.11', 'python3.10')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
}

$python = $null
foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    $version = & $candidate -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if (-not $version) { continue }
    $parts = $version.Split('.')
    if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10) {
        $python = $candidate
        break
    }
}

if (-not $python) {
    Write-Host "[ERROR] No Python 3.10+ found. The project uses 'dict | None' annotations" -ForegroundColor Red
    Write-Host "        which fail on Python 3.9. Install 3.13 or pass a path manually:" -ForegroundColor Red
    Write-Host "        D:\python\Python313\python.exe -m uvicorn main:app --port $Port" -ForegroundColor Yellow
    exit 1
}

Write-Host "Using interpreter: $python" -ForegroundColor Green
Write-Host ("Starting uvicorn on http://{0}:{1}  (Ctrl+C to stop)" -f $HostAddress, $Port) -ForegroundColor Green
Write-Host ("Health check:  http://{0}:{1}/api/health" -f $HostAddress, $Port) -ForegroundColor Cyan
Write-Host ""

Push-Location $root
try {
    & $python -m uvicorn main:app --host $HostAddress --port $Port
} finally {
    Pop-Location
}
