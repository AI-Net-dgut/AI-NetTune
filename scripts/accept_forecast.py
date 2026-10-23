"""预测预警的能力验收（可复跑）。

回答三个问题：
  1. 命中 / 漏报：8 个真值故障，预警有没有报出来？
  2. 提前量：首次预警比"首个异常标签"和"实际越阈"分别早多少？
  3. 误报：非事件时段会不会乱报？
  4. 持续风险：故障中后期再预测，是否给出"已处于…预计持续"？

用法：
    python scripts/accept_forecast.py                # 全部（约 4~6 分钟）
    python scripts/accept_forecast.py --fp-step 30   # 误报抽样间隔调大（更快）
    python scripts/accept_forecast.py --no-fp        # 跳过误报扫描（最快）

结果同时打印为 Markdown 表格，可直接贴进文档。
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main  # noqa: E402

ISO = "%Y-%m-%d %H:%M:%S"
DATA = ROOT / "data"


def _load_events():
    path = DATA / "fault_events.csv"
    if not path.exists():
        print(f"缺少 {path}，无法验收")
        return []
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))


def _first_label(ap: str, lo: str, hi: str) -> datetime | None:
    """该 AP 在 [lo, hi] 内第一个非 normal 标签时刻。"""
    rows = main.filter_rows("校园", lo, hi)
    hits = [r["timestamp"] for r in rows
            if r["ap_id"] == ap and r.get("network_status") not in (None, "normal")]
    return datetime.strptime(min(hits), ISO) if hits else None


def _preview(base: datetime, ap: str | None = None) -> list[dict]:
    d = main.forecast_preview(main.ForecastPreviewRequest(
        scenario="校园", baseTime=base.strftime(ISO),
        history=main._TimeRange(start=(base - timedelta(minutes=60)).strftime(ISO),
                                end=base.strftime(ISO)),
        horizonMinutes=30))["data"]
    alerts = d.get("alerts") or []
    return [a for a in alerts if ap is None or a.get("target") == ap]


def _scan_first_alert(ap: str, lo: datetime, hi: datetime) -> tuple[datetime | None, dict | None]:
    """逐分钟推进基准，返回该 AP 第一次出现预警的基准时刻与预警内容。"""
    t = lo
    while t <= hi:
        alerts = _preview(t, ap)
        if alerts:
            return t, alerts[0]
        t += timedelta(minutes=1)
    return None, None


def accept_events(events) -> list[dict]:
    rows = []
    for e in events:
        ap, ftype = e["ap_id"], e["fault_type"]
        start = datetime.strptime(e["start_time"], ISO)
        end = datetime.strptime(e["end_time"], ISO)
        label = _first_label(ap, (start - timedelta(minutes=60)).strftime(ISO), end.strftime(ISO))
        first_alert, alert = _scan_first_alert(ap, start - timedelta(minutes=60),
                                               end + timedelta(minutes=10))
        rows.append({"event": e["event_id"], "ap": ap, "type": ftype,
                     "label": label, "alert_at": first_alert, "alert": alert})
    return rows


def accept_lead(rows) -> None:
    print("\n### 1) 命中与提前量（相对首个异常标签）\n")
    print("| 事件 | AP | 类型 | 首个异常标签 | 首条预警 | 相对标签 | 预警给出的越阈预测 |")
    print("|---|---|---|---|---|---|---|")
    early = late = miss = 0
    for r in rows:
        label = r["label"].strftime("%m-%d %H:%M") if r["label"] else "-"
        if r["alert_at"] is None:
            miss += 1
            print(f"| {r['event']} | {r['ap']} | {r['type']} | {label} | — | **漏报** | — |")
            continue
        at = r["alert_at"].strftime("%m-%d %H:%M")
        title = (r["alert"] or {}).get("title", "")
        if r["label"]:
            delta = (r["label"] - r["alert_at"]).total_seconds() / 60
            rel = f"提前 {abs(delta):.0f} 分" if delta > 0 else f"晚 {abs(delta):.0f} 分"
            early += delta > 0
            late += delta <= 0
        else:
            rel = "无标签"
        print(f"| {r['event']} | {r['ap']} | {r['type']} | {label} | {at} | {rel} | {title[:28]} |")
    print(f"\n汇总：命中 {len(rows) - miss} / {len(rows)}，漏报 {miss}，"
          f"相对标签提前 {early}、晚 {late}。")


def accept_persist(events) -> None:
    """故障中后期（起始+20 分钟起），预测应给出"已处于…预计持续"。"""
    print("\n### 2) 故障中后期的持续风险提示\n")
    print("| 事件 | AP | 检查基准 | 提示类型 | 是否持续提示 |")
    print("|---|---|---|---|---|")
    ok = 0
    checked = 0
    for e in events:
        ap = e["ap_id"]
        start = datetime.strptime(e["start_time"], ISO)
        end = datetime.strptime(e["end_time"], ISO)
        base = start + timedelta(minutes=25)
        if base >= end:
            continue
        checked += 1
        alerts = _preview(base, ap)
        title = (alerts[0].get("title") if alerts else "") or ""
        sustained = "已处于" in title or "预计持续" in title
        ok += sustained
        print(f"| {e.get('event_id', '')} | {ap} | {base.strftime('%m-%d %H:%M')} | "
              f"{title[:24] or '（无预警）'} | {'是' if sustained else '否'} |")
    print(f"\n汇总：{ok} / {checked} 个基准给出了持续风险提示。")


def accept_false_positive(events, step: int) -> None:
    """非事件时段抽样，统计误报率。"""
    print(f"\n### 3) 误报统计（非事件时段，每 {step} 分钟取一次基准）\n")
    spans = []
    for e in events:
        s = datetime.strptime(e["start_time"], ISO)
        t = datetime.strptime(e["end_time"], ISO)
        spans.append((s - timedelta(minutes=30), t + timedelta(minutes=10)))

    def near(t: datetime) -> bool:
        return any(a <= t <= b for a, b in spans)

    bases, t = [], datetime(2026, 1, 1, 0, 0)
    while t <= datetime(2026, 1, 7, 23, 0):
        if not near(t):
            bases.append(t)
        t += timedelta(minutes=step)

    fp = 0
    samples = []
    for b in bases:
        alerts = _preview(b)
        if alerts:
            fp += 1
            samples.append(f"{b.strftime('%m-%d %H:%M')} {alerts[0].get('title', '')[:26]}")
    print(f"| 项 | 值 |\n|---|---|")
    print(f"| 抽样基准数 | {len(bases)} |")
    print(f"| 出现预警 | {fp} |")
    print(f"| 误报率 | {fp / len(bases) * 100:.2f}% |" if bases else "| 误报率 | n/a |")
    for s in samples[:5]:
        print(f"\n> 误报样例：{s}")


def main_cli() -> int:
    ap = argparse.ArgumentParser(description="预测预警能力验收")
    ap.add_argument("--fp-step", type=int, default=15, help="误报抽样间隔（分钟），默认 15")
    ap.add_argument("--no-fp", action="store_true", help="跳过误报扫描")
    ap.add_argument("--no-persist", action="store_true", help="跳过持续风险统计")
    args = ap.parse_args()

    events = _load_events()
    if not events:
        return 2

    print("# 预测预警能力验收\n")
    print(f"数据：network_data_30d.csv（标签真值）+ spectrum_7d.csv（预警输入）+ fault_events.csv（{len(events)} 个注入故障）")
    print(f"判定口径：功率 < {main.ALERT_WEAK_POWER_DBM} dBm 或占用率 > {main.ALERT_CONGESTION_OCC_PCT}% ，"
          f"且 ≥40/50 频段越阈、连续 ≥3 分钟。")

    rows = accept_events(events)
    accept_lead(rows)
    if not args.no_persist:
        accept_persist(events)
    if not args.no_fp:
        accept_false_positive(events, args.fp_step)

    print("\n> 说明：本页面所有数字均由本脚本实测生成，可随时复跑。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
