# 验证实际后端和线上前端均已加载本次修复；不修改服务和文件。
param(
    [string]$BackendUrl = 'http://127.0.0.1:8000',
    [string]$FrontendUrl = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$index = Get-Content -LiteralPath (Join-Path $root 'frontend/index.html') -Raw -Encoding UTF8
$marker = [regex]::Match($index, '<meta name="nettune-release" content="([^"]+)"')
if (-not $marker.Success) { throw '本地源码缺少版本标识，请确认合并到了原项目根目录。' }
$expected = $marker.Groups[1].Value
$health = Invoke-RestMethod -Uri ($BackendUrl.TrimEnd('/') + '/api/health') -TimeoutSec 15
if ($health.code -ne 0 -or $health.data.release -ne $expected) {
    throw ('后端版本不匹配。期望 {0}；收到 {1}。请确认后端目录并停止、重新启动原进程。' -f $expected, $health.data.release)
}
Write-Host ('[PASS] 后端已更新：' + $expected) -ForegroundColor Green
if ($FrontendUrl) {
    # 无缓存查询只检查已发布页面，不发布、不提交代码。
    $separator = if ($FrontendUrl.Contains('?')) { '&' } else { '?' }
    $url = $FrontendUrl.Split('#')[0] + $separator + 'nettune_check=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $page = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 15 -Headers @{ 'Cache-Control'='no-cache' }
    $remote = [regex]::Match($page.Content, '<meta name="nettune-release" content="([^"]+)"')
    if (-not $remote.Success -or $remote.Groups[1].Value -ne $expected) {
        throw '线上前端版本不匹配。请发布新构建的 frontend/dist；仅推送 main 或刷新页面不足以确认已部署。'
    }
    Write-Host ('[PASS] 线上前端已更新：' + $expected) -ForegroundColor Green
}
Write-Host '版本一致后，再发送 AP-03 / UE-03 问题检查图表；本脚本不等于功能验收。' -ForegroundColor Cyan
