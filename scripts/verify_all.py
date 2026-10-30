"""一条命令跑完项目的全部可复跑检查，并打印 PASS/FAIL 表格。

用法：
    python scripts/verify_all.py              # 后端 + 前端静态检查
    python scripts/verify_all.py --smoke      # 额外跑 HTTP 冒烟（需后端已在 8000/指定端口运行）
    python scripts/verify_all.py --smoke --base http://127.0.0.1:8021
    python scripts/verify_all.py --fast       # 跳过最慢的两项（compare_verdicts / audit_data）
    python scripts/verify_all.py --python D:\\python\\Python\\Python313\\python.exe   # 指定解释器

设计原则：
- 只调用仓库里已有的脚本，不重复实现检查逻辑，避免"两套口径"。
- 任何一步失败 → 退出码 1，并在末尾列出失败项与原始输出片段。
- 前端检查在缺 node_modules 时标记 SKIP（不报失败），便于纯后端环境使用。
- **自动挑选合适的解释器**：项目需要 Python ≥3.10 且装了 pandas。
  如果当前 `python` 是 conda 3.9 / 没装 pandas，本脚本会自动换用可用的解释器，
  并用 UTF-8 环境变量调用子进程（否则中文日志在 GBK 控制台会变乱码）。
  可用环境变量 NETTUNE_PYTHON 固定指定。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

# GBK 控制台无法编码 '✗'（独立审计 P3：任一步失败时脚本自己先崩，
# 失败清单和汇总都看不到）。统一把输出切到 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _run(cmd: list[str], cwd: Path, timeout: int = 1800) -> tuple[int, str]:
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=ENV,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"超时（>{timeout}s）：{' '.join(cmd)}"
    except FileNotFoundError as exc:
        return 127, f"找不到可执行文件：{exc}"
    finally:
        _run.last_elapsed = time.time() - started  # type: ignore[attr-defined]


def _suitable(python: str) -> tuple[bool, str]:
    """该解释器能否跑后端检查：≥3.10 且能 import pandas（跑真实数据用例的前提）。"""
    probe = ("import sys; "
             "ok = sys.version_info >= (3, 10); "
             "print('py=%d.%d' % sys.version_info[:2]); "
             "import importlib.util as u; "
             "print('pandas=%s' % bool(u.find_spec('pandas')))")
    try:
        proc = subprocess.run([python, "-c", probe], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60, env=ENV)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    version_ok = "py=3.1" in out or "py=3.2" in out or "py=4." in out
    pandas_ok = "pandas=True" in out
    detail = " ".join(out.split())[:120]
    return (version_ok and pandas_ok), detail


def pick_python(explicit: str | None) -> tuple[str, str]:
    """挑一个能跑后端检查的解释器，返回 (路径, 说明)。

    优先级：--python 参数 > NETTUNE_PYTHON 环境变量 > 当前解释器 > 常见安装位置。
    找不到时返回当前解释器并给出原因（由调用方打印提示），而不是静默失败。
    """
    tried: list[str] = []
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env_py = os.getenv("NETTUNE_PYTHON")
    if env_py:
        candidates.append(env_py)
    candidates.append(sys.executable)
    candidates += [
        r"D:\python\Python\Python313\python.exe",
        r"C:\Python313\python.exe",
        r"C:\Python312\python.exe",
    ]
    for name in ("python3.13", "python3.12", "python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    for candidate in dict.fromkeys(candidates):
        if not candidate or not Path(candidate).exists():
            continue
        ok, detail = _suitable(candidate)
        tried.append(f"{candidate}（{detail}）")
        if ok:
            return candidate, ""
    return sys.executable, "没找到同时满足「Python ≥3.10」且「已安装 pandas」的解释器；" \
                            "已尝试：\n    " + "\n    ".join(tried)


def _tail(text: str, limit: int = 320) -> str:
    text = " ".join(text.split())
    return text[-limit:] if len(text) > limit else text


def main() -> int:
    ap = argparse.ArgumentParser(description="AI-NetTune 全量检查")
    ap.add_argument("--smoke", action="store_true", help="额外跑 HTTP 冒烟（需后端运行）")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="冒烟用的后端地址")
    ap.add_argument("--fast", action="store_true", help="跳过 compare_verdicts / audit_data")
    ap.add_argument("--python", default=None, help="指定后端检查用的解释器（默认自动挑选 ≥3.10 且装了 pandas 的）")
    args = ap.parse_args()

    PY, why = pick_python(args.python)
    if why:
        print("⚠ " + why)
        print("  仍会用当前解释器继续尝试；也可显式指定：--python <解释器路径>\n")

    steps: list[tuple[str, list[str], Path, int]] = [
        ("后端测试 tests/run_tests.py", [PY, "tests/run_tests.py"], ROOT, 1800),
    ]
    if not args.fast:
        steps += [
            ("口径一致率 compare_verdicts", [PY, "scripts/compare_verdicts.py"], ROOT, 1800),
            ("数据对账 audit_data", [PY, "scripts/audit_data.py"], ROOT, 1200),
        ]
    steps.append(("死代码扫描 find_dead_code", [PY, "scripts/find_dead_code.py"], ROOT, 600))

    frontend = ROOT / "frontend"
    node = shutil.which("node")
    has_modules = (frontend / "node_modules" / "typescript").exists()
    if node and has_modules:
        steps += [
            ("前端类型检查 tsc", [node, r".\node_modules\typescript\bin\tsc", "-p", "tsconfig.app.json", "--noEmit"], frontend, 1800),
            ("前端 lint eslint", [node, r".\node_modules\eslint\bin\eslint.js", "."], frontend, 1200),
        ]
    if args.smoke:
        steps.append(("HTTP 冒烟 smoke_endpoints", [PY, "scripts/smoke_endpoints.py", "--base", args.base], ROOT, 900))

    results: list[tuple[str, str, float, str]] = []
    print(f"工作目录：{ROOT}")
    print(f"后端解释器：{PY}" + ("（已自动挑选；可用 --python 覆盖）" if PY != sys.executable else ""))
    if args.smoke:
        print(f"冒烟地址：{args.base}")
    print()
    for name, cmd, cwd, timeout in steps:
        code, out = _run(cmd, cwd, timeout)
        elapsed = getattr(_run, "last_elapsed", 0.0)
        status = "PASS" if code == 0 else "FAIL"
        results.append((name, status, elapsed, _tail(out) if code else ""))
        print(f"  [{status}] {name}  ({elapsed:.1f}s)")

    if not (node and has_modules):
        results.append(("前端静态检查", "SKIP", 0.0, "缺 node 或 frontend/node_modules"))

    print("\n" + "-" * 78)
    failed = [r for r in results if r[1] == "FAIL"]
    skipped = [r for r in results if r[1] == "SKIP"]
    print(f"通过 {len([r for r in results if r[1] == 'PASS'])} / "
          f"失败 {len(failed)} / 跳过 {len(skipped)}")
    for name, _status, _elapsed, tail in failed:
        print(f"\n✗ {name}\n  {tail}")
    if skipped:
        print("\n跳过：")
        for name, _s, _e, why in skipped:
            print(f"  - {name}：{why}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
