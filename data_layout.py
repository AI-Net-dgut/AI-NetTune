"""数据文件定位规则（单一定义源）。

main.py 与 scripts/doctor.py 都从这里取候选文件名与解析逻辑，
避免"两处各写一份、改一处漏一处"的漂移。

解析优先级：
  1) 环境变量（NETWORK_DATA_PATH / SPECTRUM_DATA_PATH），相对路径按项目根目录解析；
  2) NETTUNE_DATA_DIR（默认 <项目根>/data）下按候选名顺序探测；
  3) 都没有 → 返回 None，并把原因写进调用方传入的 issues 列表（不静默）。
"""

from __future__ import annotations

import os
from pathlib import Path

# 项目根目录：所有运行时路径（数据、报告）都以它为准，不依赖启动时的 cwd。
BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = Path(os.getenv("NETTUNE_DATA_DIR") or (BASE_DIR / "data")).expanduser()
if not DATA_DIR.is_absolute():
    DATA_DIR = BASE_DIR / DATA_DIR

# 候选顺序：规范名优先，其次兼容浏览器重复下载产生的 "(1)" 副本。
NETWORK_CANDIDATES = (
    "network_data_30d.csv", "network_data_30d(1).csv",
    "network_data_7d.csv", "network_data_7d(1).csv",
    "network_data.csv",
)
SPECTRUM_CANDIDATES = (
    "spectrum_7d.csv", "spectrum_7d(1).csv", "spectrum_30d.csv",
)

NETWORK_COLUMNS = (
    "timestamp", "ap_id", "ap_x", "ap_y", "user_id", "ue_x", "ue_y",
    "rsrp", "sinr", "throughput", "latency", "packet_loss", "prb_usage",
    "scenario", "network_status",
    "retransmission_count", "traffic_demand", "active_users",
)
SPECTRUM_COLUMNS = (
    "timestamp", "observation_id", "band_id", "lower_mhz", "upper_mhz",
    "power_dbm", "occupancy_pct", "network_status", "scenario",
)


def resolve_data_file(env_name: str, candidates: tuple[str, ...], label: str,
                      issues: list[str]) -> Path | None:
    """定位一个数据文件；找不到返回 None，并把可读原因追加到 issues。"""
    override = os.getenv(env_name)
    if override:
        p = Path(override).expanduser()
        if not p.is_absolute():
            p = BASE_DIR / p
        if p.is_file():
            return p
        issues.append(f"{label}：环境变量 {env_name}={override} 指向的文件不存在（解析为 {p}）")
        return None

    for name in candidates:
        p = DATA_DIR / name
        if p.is_file():
            return p

    issues.append(f"{label}：{DATA_DIR} 下未找到任何候选文件（{' / '.join(candidates)}）")
    return None
