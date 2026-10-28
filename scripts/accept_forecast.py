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
# 同一目录下的口径一致性度量（docs/fault_definition.md 第 7 节指标 3）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: E402
from compare_verdicts import judge_anomalous, spectrum_hit  # noqa: E402

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
                     "label": label, "alert_at": first_alert, "alert": alert,
                     "start": start, "end": end})
    return rows


def _load_spectrum():
    """加载频谱表（仅 4 列），用于计算"首次频谱越阈"时刻。"""
    path = DATA / "spectrum_7d.csv"
    if not path.exists():
        return None
    import pandas as _pd
    return _pd.read_csv(path, encoding="utf-8-sig",
                        usecols=["timestamp", "observation_id", "power_dbm", "occupancy_pct"])


def _first_spectrum_crossing(spec, ap, start, end, kind=""):
    """该 AP 在 [start, end] 内**第一次**达到"≥40/50 频段越阈"的时刻。

    这是"提前预警"的另一把尺子：相对**频谱越阈**，而不是相对网络标签。
    唐苗检测报告第 8 条要求两个口径分别统计 —— 不能把"预计 N 分钟后越阈"
    当成"提前 N 分钟发现网络故障"。

    审计第 12 条：**必须按故障类型分别统计**。原先把"功率越阈"与"占用率越阈"
    在同一分钟上做"或"再求和，20 个频段功率越阈 + 20 个频段占用越阈会被算成 40，
    但真实判据里这两类各自都要达到 ≥40 才算越阈。
    """
    if spec is None or start is None or end is None:
        return None
    s = start.strftime("%Y-%m-%d %H:%M:%S")
    e = end.strftime("%Y-%m-%d %H:%M:%S")
    seg = spec[(spec.observation_id == ap) & (spec.timestamp >= s) & (spec.timestamp <= e)]
    if seg.empty:
        return None
    if kind == "weak_coverage":
        mask = seg.power_dbm < main.ALERT_WEAK_POWER_DBM
    elif kind == "congestion":
        mask = seg.occupancy_pct > main.ALERT_CONGESTION_OCC_PCT
    else:                                    # 未给出类型时才退回"任一类越阈"
        mask = ((seg.power_dbm < main.ALERT_WEAK_POWER_DBM)
                | (seg.occupancy_pct > main.ALERT_CONGESTION_OCC_PCT))
    hit = seg.assign(_h=mask).groupby("timestamp")["_h"].sum()
    hit = hit[hit >= max(5, int(0.8 * 50))]      # 与 main._min_crossing_bands(50) 同口径
    if hit.empty:
        return None
    return datetime.strptime(str(hit.index[0]), "%Y-%m-%d %H:%M:%S")


def accept_lead(rows) -> None:
    print("\n### 1) 命中与提前量（两个口径并列）\n")
    spec = _load_spectrum()
    print("| 事件 | AP | 类型 | 首条预警 | 首个网络标签 | 相对标签 | 首次频谱越阈 | 相对越阈 |")
    print("|---|---|---|---|---|---|---|---|")
    early = late = miss = 0
    cross_early = cross_sync = cross_late = cross_na = 0
    cross_leads = []
    for r in rows:
        label = r["label"].strftime("%m-%d %H:%M") if r["label"] else "-"
        if r["alert_at"] is None:
            miss += 1
            print(f"| {r['event']} | {r['ap']} | {r['type']} | — | {label} | **漏报** | — | — |")
            continue
        at = r["alert_at"].strftime("%m-%d %H:%M")
        # 第二把尺子：相对"首次频谱越阈"
        cross = _first_spectrum_crossing(spec, r["ap"], r.get("start"), r.get("end"), r.get("type"))
        if cross is not None:
            d2 = (cross - r["alert_at"]).total_seconds() / 60
            cross_txt = cross.strftime("%m-%d %H:%M")
            rel2 = f"提前 {abs(d2):.0f} 分" if d2 > 0 else ("同步" if d2 == 0 else f"晚 {abs(d2):.0f} 分")
            cross_early += d2 > 0
            cross_sync += d2 == 0
            cross_late += d2 < 0
            cross_leads.append(d2)
        else:
            cross_txt, rel2 = "—", "未越阈"
            cross_na += 1
        if r["label"]:
            delta = (r["label"] - r["alert_at"]).total_seconds() / 60
            rel = f"提前 {abs(delta):.0f} 分" if delta > 0 else f"晚 {abs(delta):.0f} 分"
            early += delta > 0
            late += delta <= 0
        else:
            rel = "无标签"
        print(f"| {r['event']} | {r['ap']} | {r['type']} | {at} | {label} | {rel} | {cross_txt} | {rel2} |")
    print(f"\n汇总：命中 {len(rows) - miss} / {len(rows)}，漏报 {miss}")
    print(f"- **相对首个网络标签**：提前 {early}、晚 {late}")
    print(f"- **相对首次频谱越阈**：提前 {cross_early}、同步 {cross_sync}、晚 {cross_late}、未越阈 {cross_na}")
    if cross_leads:
        cross_leads.sort()
        print(f"- 相对越阈平均提前 **{sum(cross_leads)/len(cross_leads):+.1f} 分钟**"
              f"（中位 {cross_leads[len(cross_leads)//2]:+.1f}）")

    # 平均提前量：只有"提前"的那几条算正值，晚的算负值，这样能给出一个可写在
    # 答辩材料里的单一数字（含范围与中位数，避免被极端值带偏）。
    leads = []
    for r in rows:
        if r["alert_at"] is None or r["label"] is None:
            continue
        leads.append((r["label"] - r["alert_at"]).total_seconds() / 60)
    if leads:
        leads.sort()
        avg = sum(leads) / len(leads)
        mid = leads[len(leads) // 2]
        print(f"平均提前量：**{avg:+.1f} 分钟**（中位 {mid:+.1f}，范围 {min(leads):+.0f} ~ {max(leads):+.0f} 分钟）")
        print(f"其中提前（>0）{sum(1 for x in leads if x > 0)} 个、"
              f"同步/滞后（≤0）{sum(1 for x in leads if x <= 0)} 个")
        how = next((r["alert"] or {}).get("title", "") for r in rows if r["alert"])
        if how:
            print(f"\n> 口径说明：这里的\"提前量\"= 首个异常标签时刻 − 首条预警的基准时刻，"
                  f"衡量的是**发现早晚**；预警文案里的\"预计 N 分钟后越阈\"是**预测目标时间**，两者不是一回事。")


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


def accept_consistency(events) -> None:
    """第 3 项指标：研判口径 vs 预警口径的一致率（docs/fault_definition.md 第 7 节）。

    在故障边界窗口上对比「现存研判」与「频谱越阈」两个结论，
    两者相反即说明"故障定义"没统一（v1 曾有 4/56 相反，v2 后应为 0）。
    """
    print("\n### 4) 两套口径一致率（研判 vs 频谱越阈）\n")
    spec = main._pd.read_csv(DATA / "spectrum_7d.csv", encoding="utf-8-sig",
                             usecols=["timestamp", "observation_id", "power_dbm", "occupancy_pct"])
    same = diff = 0
    mismatches = []
    print("| 事件 | 窗口 | 研判 | 频谱越阈 | 一致 |")
    print("|---|---|---|---|---|")
    for e in events:
        ap_id = e["ap_id"]
        start = datetime.strptime(e["start_time"], ISO)
        end = datetime.strptime(e["end_time"], ISO)
        windows = [(start, start + timedelta(minutes=n)) for n in (5, 10, 15, 20, 30)]
        windows += [(end - timedelta(minutes=n), end) for n in (5, 10)]
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
                mismatches.append((e["event_id"], f"{ss[5:16]}~{ee[11:16]}", jt, sh))
            print(f"| {e['event_id']} | {ss[5:16]}~{ee[11:16]} | {jt} | "
                  f"{'是' if sh else '否'} | {'✅' if ok else '❌'} |")
    total = same + diff
    rate = same / total * 100 if total else 0
    print(f"\n汇总：一致 {same} / {total} = **{rate:.1f}%**")
    if mismatches:
        print("\n不一致明细：")
        for eid, win, jt, sh in mismatches:
            print(f"  {eid} {win} 研判={jt} 频谱={'越阈' if sh else '未越阈'}")


def main_cli() -> int:
    ap = argparse.ArgumentParser(description="预测预警能力验收")
    ap.add_argument("--fp-step", type=int, default=15, help="误报抽样间隔（分钟），默认 15")
    ap.add_argument("--no-fp", action="store_true", help="跳过误报扫描")
    ap.add_argument("--no-persist", action="store_true", help="跳过持续风险统计")
    ap.add_argument("--no-consistency", action="store_true", help="跳过口径一致率统计")
    args = ap.parse_args()

    events = _load_events()
    if not events:
        return 2

    print("# 预测预警能力验收\n")
    print(f"数据：network_data_30d.csv（标签真值）+ spectrum_7d.csv（预警输入）+ fault_events.csv（{len(events)} 个注入故障）")
    print(f"判定口径：功率 < {main.ALERT_WEAK_POWER_DBM} dBm 或占用率 > {main.ALERT_CONGESTION_OCC_PCT}% ，"
          f"且 ≥{main._min_crossing_bands(50)}/50 频段越阈、连续 ≥{main.ALERT_MIN_CROSSING_MINUTES} 分钟。")

    rows = accept_events(events)
    accept_lead(rows)
    if not args.no_persist:
        accept_persist(events)
    if not args.no_fp:
        accept_false_positive(events, args.fp_step)
    if not args.no_consistency:
        accept_consistency(events)

    print("\n> 说明：本页面所有数字均由本脚本实测生成，可随时复跑。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
