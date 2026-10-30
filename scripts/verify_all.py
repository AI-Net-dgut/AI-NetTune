"""一条命令跑完项目的全部可复跑检查，并打印 PASS/FAIL 表格。

用法：
    python scripts/verify_all.py              # 后端 + 前端静态检查
    python scripts/verify_all.py --smoke      # 额外跑 HTTP 冒烟（需后端已在 8000/指定端口运行）
    python scripts/verify_all.py --smoke --base http://127.0.0.1:8021
    python scripts/verify_all.py --fast       # 跳过最慢的两项（compare_verdicts / audit_data）

设计原则：
- 只调用仓库里已有的脚本，不重复实现检查逻辑，避免"两套口径"。
- 任何一步失败 → 退出码 1，并在末尾列出失败项与原始输出片段。
- 前端检查在缺 node_modules 时标记 SKIP（不报失败），便于纯后端环境使用。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def _run(cmd: list[str], cwd: Path, timeout: int = 1800) -> tuple[int, str]:
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"超时（>{timeout}s）：{' '.join(cmd)}"
    except FileNotFoundError as exc:
        return 127, f"找不到可执行文件：{exc}"
    finally:
        _run.last_elapsed = time.time() - started  # type: ignore[attr-defined]


def _tail(text: str, limit: int = 320) -> str:
    text = " ".join(text.split())
    return text[-limit:] if len(text) > limit else text


def main() -> int:
    ap = argparse.ArgumentParser(description="AI-NetTune 全量检查")
    ap.add_argument("--smoke", action="store_true", help="额外跑 HTTP 冒烟（需后端运行）")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="冒烟用的后端地址")
    ap.add_argument("--fast", action="store_true", help="跳过 compare_verdicts / audit_data")
    args = ap.parse_args()

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
    print(f"工作目录：{ROOT}\nPython：{PY}\n")
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
