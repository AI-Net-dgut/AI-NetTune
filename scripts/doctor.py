#!/usr/bin/env python
"""部署自检：一条命令告诉你"这台机器能不能跑起来"。

检查项：
  1) Python 版本
  2) 必装/选装依赖（fastapi、uvicorn、pandas、numpy、requests、prophet、pykrige）
  3) 数据文件是否到位、行数、列名、时间范围、场景与状态分布
  4) 网络表与频谱表的时间轴是否重叠（不重叠会导致预测预警大面积 insufficient_data）
  5) LLM 是否配置

用法（在项目根目录执行）：
    python scripts/doctor.py
    python scripts/doctor.py --quick     # 跳过 108MB 频谱表的深度统计

退出码：0 = 全部就绪；1 = 存在会阻断演示的问题。
"""

from __future__ import annotations

import argparse
import csv
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_layout import (  # noqa: E402
    DATA_DIR, NETWORK_CANDIDATES, NETWORK_COLUMNS,
    SPECTRUM_CANDIDATES, SPECTRUM_COLUMNS, resolve_data_file,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FAIL: list[str] = []
WARN: list[str] = []


def ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def warn(msg: str) -> None:
    WARN.append(msg)
    print(f"  [WARN] {msg}")


def fail(msg: str) -> None:
    FAIL.append(msg)
    print(f"  [FAIL] {msg}")


def check_python() -> None:
    print("\n[1/5] Python 版本")
    v = sys.version_info
    if v >= (3, 10):
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        fail(f"Python {v.major}.{v.minor} 过低，本项目需要 3.10+（推荐 3.13）")


def check_deps() -> None:
    print("\n[2/5] 依赖")
    required = ("fastapi", "uvicorn", "pandas", "numpy", "pydantic", "requests")
    optional = {"prophet": "接口10 将降级为 moving_average",
                "pykrige": "接口5 将降级为 grid_mean（网格均值）"}
    for name in required:
        try:
            importlib.import_module(name)
            ok(f"{name} 已安装")
        except Exception as exc:
            fail(f"{name} 未安装或无法导入（{exc}） → 请执行 pip install -r requirements.txt")
    for name, downgrade in optional.items():
        try:
            importlib.import_module(name)
            ok(f"{name} 已安装（选装）")
        except Exception as exc:
            warn(f"{name} 未安装或无法导入（{exc}） → {downgrade}；如不需要可忽略")


def check_network_file(path: Path) -> dict:
    print("\n[3/5] 网络表")
    if path is None:
        fail(f"未找到网络表。{DATA_DIR} 下的候选名：{' / '.join(NETWORK_CANDIDATES)}")
        return {}
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"  文件：{path}")
    print(f"  大小：{size_mb:.1f} MB")
    first = last = None
    n = 0
    counts: dict[str, int] = {}
    scenarios: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            missing = [c for c in NETWORK_COLUMNS if c not in header]
            for row in reader:
                ts = row.get("timestamp", "")
                if first is None:
                    first = ts
                last = ts
                n += 1
                status = row.get("network_status", "")
                counts[status] = counts.get(status, 0) + 1
                scene = row.get("scenario", "")
                scenarios[scene] = scenarios.get(scene, 0) + 1
    except Exception as exc:
        fail(f"读取网络表失败：{exc}")
        return {}

    ok(f"{n:,} 行 × {len(header)} 列")
    if missing:
        fail(f"缺少字段：{', '.join(missing)}（见 docs/data_dict.md）")
    else:
        ok("18 列字段齐全")

    print(f"  时间范围：{first} ~ {last}")
    print(f"  场景：{scenarios}")
    print(f"  状态：{counts}")

    if set(scenarios) == {"校园"}:
        warn("数据只有「校园」一个场景；前端若切换到商场/体育馆会查不到数据（VALID_SCENARIOS 允许三个）")
    return {"path": path, "rows": n, "first": first, "last": last, "status": counts}


def check_spectrum_file(path: Path, quick: bool) -> dict:
    print("\n[4/5] 频谱表")
    if path is None:
        fail(f"未找到频谱表。{DATA_DIR} 下的候选名：{' / '.join(SPECTRUM_CANDIDATES)}")
        return {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            header = next(csv.reader(f), [])
    except Exception as exc:
        fail(f"读取频谱表表头失败：{exc}")
        return {}
    missing = [c for c in SPECTRUM_COLUMNS if c not in header]
    if missing:
        fail(f"频谱表缺少字段：{', '.join(missing)}")
    else:
        ok("9 列字段齐全")

    if quick:
        warn("--quick 模式：跳过频谱表行数与时间范围统计")
        return {"path": path, "rows": None, "first": None, "last": None}

    try:
        import pandas as pd
    except Exception as exc:
        fail(f"pandas 不可用，无法统计频谱表：{exc}")
        return {}

    try:
        df = pd.read_csv(path, encoding="utf-8-sig",
                         usecols=["timestamp", "observation_id", "band_id", "network_status"])
    except Exception as exc:
        fail(f"读取频谱表失败：{exc}")
        return {}

    ts = pd.to_datetime(df["timestamp"], format="%Y-%m-%d %H:%M:%S")
    ok(f"{len(df):,} 行")
    print(f"  时间范围：{ts.min()} ~ {ts.max()}")
    print(f"  AP 数：{df['observation_id'].nunique()}  频段数：{df['band_id'].nunique()}")
    print(f"  状态：{df['network_status'].value_counts().to_dict()}")
    return {"path": path, "rows": int(len(df)), "first": str(ts.min()), "last": str(ts.max())}


def check_overlap(net: dict, spec: dict) -> None:
    print("\n[5/5] 时间轴与配置")
    if not net or not spec or not net.get("last") or not spec.get("last"):
        warn("缺少时间范围信息，跳过重叠检查")
    else:
        net_last, spec_last = net["last"], spec["last"]
        if spec_last < net_last:
            warn(f"频谱表只到 {spec_last}，网络表到 {net_last} → "
                 f"{spec_last} 之后的时刻调用 /api/forecast/preview 会返回 insufficient_data。"
                 f"演示范围请控制在频谱覆盖区间内（前端 replay/config.ts 默认 01-01~01-07，与频谱一致）。")
        else:
            ok(f"两表时间轴重叠：频谱至 {spec_last}，网络至 {net_last}")

    key = os.getenv("LLM_API_KEY", "")
    if key:
        ok(f"LLM_API_KEY 已配置（模型 {os.getenv('LLM_MODEL', 'deepseek-chat')}）")
    else:
        warn("LLM_API_KEY 未配置 → 对话/研判会降级为规则模板（功能不中断）。"
             "需要 LLM 时：setx LLM_API_KEY \"sk-...\"，然后重开终端")

    if os.getenv("NETTUNE_DATA_DIR"):
        ok(f"NETTUNE_DATA_DIR = {os.getenv('NETTUNE_DATA_DIR')}")
    for env_name in ("NETWORK_DATA_PATH", "SPECTRUM_DATA_PATH"):
        if os.getenv(env_name):
            ok(f"{env_name} = {os.getenv(env_name)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="AI-NetTune 部署自检")
    ap.add_argument("--quick", action="store_true", help="跳过 108MB 频谱表的深度统计（更快）")
    args = ap.parse_args()

    print("=" * 72)
    print("AI-NetTune 部署自检")
    print(f"项目根目录：{ROOT}")
    print(f"数据目录  ：{DATA_DIR}")
    print("=" * 72)

    check_python()
    check_deps()

    issues: list[str] = []
    net_path = resolve_data_file("NETWORK_DATA_PATH", NETWORK_CANDIDATES, "网络表", issues)
    spec_path = resolve_data_file("SPECTRUM_DATA_PATH", SPECTRUM_CANDIDATES, "频谱表", issues)
    for issue in issues:
        print(f"  [提示] {issue}")

    net = check_network_file(net_path)
    spec = check_spectrum_file(spec_path, args.quick)
    check_overlap(net, spec)

    print("\n" + "=" * 72)
    if FAIL:
        print(f"结论：{len(FAIL)} 项阻断问题，需要处理")
        for item in FAIL:
            print(f"  - {item}")
        print("\n下一步：按 DEPLOY.md 第 2 节准备 data/ 目录，或 pip install -r requirements.txt")
        print("=" * 72)
        return 1
    print(f"结论：可以启动。警告 {len(WARN)} 条（不阻断）。")
    print("  启动后端：python -m uvicorn main:app --host 0.0.0.0 --port 8000")
    print("  复查自检：浏览器或 curl 访问 http://127.0.0.1:8000/api/health")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
