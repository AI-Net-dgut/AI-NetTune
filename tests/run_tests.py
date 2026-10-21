#!/usr/bin/env python
"""零依赖测试运行器：发现 tests/test_*.py 里的 test_* 函数并逐个执行。

用法：
    python tests/run_tests.py            # 全部
    python tests/run_tests.py anomaly    # 只跑名字含 "anomaly" 的

为什么不用 pytest：演示机没装 pytest，也不想为了跑个冒烟测试去装依赖。
"""
from __future__ import annotations

import importlib.util
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 版本守卫：main.py / llm_agent.py 用了 `dict | None` 这类写法，需要 Python 3.10+。
# 本机 PATH 上的 `python` 是 conda 的 3.9，直接跑会报
#   TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
# 与其让使用者去猜，这里直接说清楚该用哪个解释器。
if sys.version_info < (3, 10):
    print(f"[错误] 当前解释器是 Python {sys.version_info.major}.{sys.version_info.minor}"
          f"（{sys.executable}），项目需要 3.10 及以上（推荐 3.13）。")
    print("       本机可用：D:\\python\\Python\\Python313\\python.exe")
    print("       请改用：D:\\python\\Python\\Python313\\python.exe tests\\run_tests.py")
    raise SystemExit(2)


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"_t_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    keyword = argv[1] if len(argv) > 1 else ""
    files = sorted(HERE.glob("test_*.py"))
    if not files:
        print("没有找到 tests/test_*.py")
        return 1

    passed, failed = [], []
    started = time.time()
    for path in files:
        print(f"\n=== {path.name} ===")
        try:
            mod = _load(path)
        except Exception:
            print(f"  [FAIL] 模块导入失败")
            traceback.print_exc()
            failed.append(f"{path.name}:import")
            continue
        names = sorted(n for n in dir(mod) if n.startswith("test_") and callable(getattr(mod, n)))
        for name in names:
            if keyword and keyword not in name:
                continue
            t0 = time.time()
            try:
                getattr(mod, name)()
                print(f"  [PASS] {name}  ({(time.time() - t0) * 1000:.0f} ms)")
                passed.append(name)
            except AssertionError as exc:
                print(f"  [FAIL] {name}: {exc}")
                failed.append(name)
            except Exception as exc:
                print(f"  [ERR ] {name}: {type(exc).__name__}: {exc}")
                traceback.print_exc()
                failed.append(name)

    total = len(passed) + len(failed)
    print("\n" + "=" * 60)
    print(f"通过 {len(passed)} / {total}，失败 {len(failed)}，耗时 {time.time() - started:.1f}s")
    if failed:
        print("失败用例：")
        for name in failed:
            print(f"  - {name}")
        print("=" * 60)
        return 1
    print("全部通过")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
