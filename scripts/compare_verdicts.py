"""两套口径一致性对比：现存研判（网络表）vs 预测预警（频谱表）。

用途：`docs/fault_definition.md` 第 7 节的"两套口径一致率"指标。
在故障边界窗口上逐窗口对比两者结论，暴露
「研判报但频谱未越阈」与「研判未报但频谱越阈」的反例。

用法：
    python scripts/compare_verdicts.py                 # 事件边界窗口（默认）
    python scripts/compare_verdicts.py --all-windows   # 再把事件整体窗口也算上
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main  # noqa: E402

ISO = "%Y-%m-%d %H:%M:%S"
MIN_BANDS, MIN_MINUTES = 40, 3      # 与 main.py 的预警判据保持一致


def _load_spectrum():
    path = ROOT / "data" / "spectrum_7d.csv"
    if not path.exists():
        return None
    return pd.read_csv(path, encoding="utf-8-sig",
                       usecols=["timestamp", "observation_id", "power_dbm", "occupancy_pct"])


def spectrum_hit(spec, ap: str, start: str, end: str) -> bool | None:
    """该 AP 在窗口内是否出现"≥40/50 频段越阈且连续≥3分钟"。

    审计 10-29 六-5：原实现把"功率越阈"与"占用率越阈"按分钟做"或"再求连续，
    于是**弱覆盖 1 分钟 + 拥塞 1 分钟交替**会被算成连续命中，
    与生产判据（两类各自连续 3 分钟）不一致，影响验收统计。
    现在两类分别求连续长度，任一达标才算命中。
    """
    seg = spec[(spec.observation_id == ap) & (spec.timestamp >= start) & (spec.timestamp <= end)]
    if seg.empty:
        return None
    per = seg.groupby("timestamp").agg(
        p=("power_dbm", lambda x: int((x < main.ALERT_WEAK_POWER_DBM).sum())),
        o=("occupancy_pct", lambda x: int((x > main.ALERT_CONGESTION_OCC_PCT).sum())),
    )

    def longest_run(flags) -> int:
        run = best = 0
        for hit in flags:
            run = run + 1 if hit else 0
            best = max(best, run)
        return best

    weak_type = longest_run((per.p >= MIN_BANDS).astype(int))
    cong_type = longest_run((per.o >= MIN_BANDS).astype(int))
    return max(weak_type, cong_type) >= MIN_MINUTES


def judge_anomalous(start: str, end: str) -> str:
    d = main.analyze_anomaly(main.AnomalyRequest(
        scenario="校园", window_start=start, window_end=end))["data"]
    return d["anomaly_type"]


def main_cli() -> int:
    ap = argparse.ArgumentParser(description="研判 vs 预警 口径一致性")
    ap.add_argument("--all-windows", action="store_true", help="额外包含事件整体窗口")
    args = ap.parse_args()

    spec = _load_spectrum()
    if spec is None:
        print("缺少 data/spectrum_7d.csv，无法比较")
        return 2
    events = list(csv.DictReader(open(ROOT / "data" / "fault_events.csv", encoding="utf-8-sig")))

    same = diff = 0
    mismatches = []
    print("窗口  |  研判结论  |  频谱越阈  |  一致")
    print("-" * 72)
    for e in events:
        ap_id = e["ap_id"]
        start = datetime.strptime(e["start_time"], ISO)
        end = datetime.strptime(e["end_time"], ISO)
        windows = [(start, start + timedelta(minutes=n)) for n in (5, 10, 15, 20, 30)]
        windows += [(end - timedelta(minutes=n), end) for n in (5, 10)]
        if args.all_windows:
            windows.append((start, end))
        for s, t in windows:
            ss, ee = s.strftime(ISO), t.strftime(ISO)
            jt = judge_anomalous(ss, ee)
            judge_anom = jt not in ("正常", "性能提示")
            sh = spectrum_hit(spec, ap_id, ss, ee)
            if sh is None:
                continue
            ok = judge_anom == sh
            same += ok
            diff += (not ok)
            if not ok:
                side = ("研判报 / 频谱未越阈" if judge_anom else "研判未报 / 频谱越阈")
                mismatches.append((f"{e['event_id']} {ap_id} {ss[5:16]}~{ee[11:16]}", jt, sh, side))

    total = same + diff
    print(f"\n一致 {same} / {total} = {same / total * 100:.1f}%" if total else "无有效窗口")
    if mismatches:
        print("\n不一致明细：")
        for label, jt, sh, side in mismatches:
            print(f"  {label:<30} 研判={jt:<10} 频谱={'越阈' if sh else '未越阈'}  ← {side}")
    else:
        print("无不一致")
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
