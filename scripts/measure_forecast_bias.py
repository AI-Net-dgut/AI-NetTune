"""量化"预测发生时间"的偏差（图第 5 步）。

目的（复查第 9 项）：预测文案给出"预计 N 分钟后越阈"，但这是**趋势外推**，不是倒计时。
赛前不承诺把算法调准，而是：
    1) 用同一批事件统计"预计发生时间"与"实际首次越阈"的误差（均值/中位数/分位数）；
    2) 同时给出持续时段误差与误报/漏报，作为"可信度"的证据；
    3) 把结果写进文档，并在界面标注"趋势估计"。

口径（与验收脚本一致，避免两套尺子）：
    - 预测时刻：以 "事件开始前 N 分钟" 为基准做一次预测，取该 AP 最早一条预警的起点；
    - 实际时刻：该 AP 在 [基准, 事件结束+60min] 内**首次达到 ≥40/50 频段越阈**的时刻；
    - 误差 = 预测时刻 − 实际时刻（负值表示"预测偏早"，正值表示"预测偏晚"）。

用法：
    python scripts/measure_forecast_bias.py                 # 默认基准 = 事件开始前 60 分钟
    python scripts/measure_forecast_bias.py --before 30     # 改成 30 分钟
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: E402
from accept_forecast import (  # noqa: E402
    ISO, _first_spectrum_crossing, _load_events, _load_spectrum, _preview, _scan_first_alert,
)


def _parse(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, ISO)
    except (TypeError, ValueError):
        return None


def measure(before_minutes: int, horizon: int) -> int:
    events = _load_events()
    if not events:
        print("没有事件数据，无法评估")
        return 1
    spec = _load_spectrum()
    if spec is None:
        print("缺少频谱数据，无法计算实际越阈时刻")
        return 1

    print(f"# 预测发生时间偏差评估（基准 = 事件开始前 {before_minutes} 分钟，视野 {horizon} 分钟）\n")
    print("| 事件 | AP | 预测首险时刻 | 实际首次越阈 | 误差(分) | 该轮风险数 |")
    print("|---|---|---|---|---:|---:|")

    errors: list[float] = []
    misses = 0
    for event in events:
        start = _parse(event.get("start_time", ""))
        end = _parse(event.get("end_time", "")) or start
        ap = event.get("ap_id", "")
        if not start or not ap:
            continue
        # 逐分钟推进基准，找到**第一次检出该 AP 风险**的基准时刻（这才是"何时开始能预警"），
        # 再比较"预测的越阈时刻"与"真实首次越阈时刻"的差 —— 直接量预测发生时间的偏差。
        found_base, found = _scan_first_alert(
            ap, start - timedelta(minutes=before_minutes), start + timedelta(minutes=30))
        scan_end = (end or start) + timedelta(minutes=60)
        actual = _first_spectrum_crossing(spec, ap, start - timedelta(minutes=before_minutes), scan_end,
                                          event.get("fault_type", ""))
        predicted = _parse((found or {}).get("start", "")) if found else None

        if predicted is None:
            misses += 1
            print(f"| {event.get('event_id')} | {ap} | — | "
                  f"{actual.strftime('%m-%d %H:%M') if actual else '未越阈'} | **未报** | — |")
            continue
        if actual is None:
            print(f"| {event.get('event_id')} | {ap} | {predicted.strftime('%m-%d %H:%M')} | 未越阈 | — | "
                  f"{found_base.strftime('%H:%M') if found_base else '-'} |")
            continue
        delta = (predicted - actual).total_seconds() / 60
        errors.append(delta)
        print(f"| {event.get('event_id')} | {ap} | {predicted.strftime('%m-%d %H:%M')} | "
              f"{actual.strftime('%m-%d %H:%M')} | {delta:+.0f} | "
              f"{found_base.strftime('%H:%M') if found_base else '-'} |")

    print()
    if not errors:
        print("没有可比对的样本（都未报或都未越阈）")
        return 1
    errors.sort()
    abs_errors = sorted(abs(x) for x in errors)
    mean = sum(errors) / len(errors)
    mean_abs = sum(abs_errors) / len(abs_errors)
    median_abs = abs_errors[len(abs_errors) // 2]
    within5 = sum(1 for x in abs_errors if x <= 5)
    within10 = sum(1 for x in abs_errors if x <= 10)
    print(f"- 样本 {len(errors)} 个；**未报/无预警 {misses} 个**")
    print(f"- 有符号平均误差：**{mean:+.1f} 分钟**（负=偏早，正=偏晚）")
    print(f"- 绝对误差：平均 **{mean_abs:.1f} 分钟**，中位 **{median_abs:.1f} 分钟**，"
          f"范围 {abs_errors[0]:.0f} ~ {abs_errors[-1]:.0f} 分钟")
    print(f"- 落在 ±5 分钟内 {within5}/{len(errors)}；±10 分钟内 {within10}/{len(errors)}")
    print("\n> 结论只用于**如实标注误差**，不作为算法达标的证据；"
          "提高刷新频率不会改变该误差。")
    return 0


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="预测发生时间偏差评估")
    parser.add_argument("--before", type=int, default=60, help="基准 = 事件开始前 N 分钟（默认 60）")
    parser.add_argument("--horizon", type=int, default=120, help="预测视野（默认 120 分钟）")
    args = parser.parse_args()
    return measure(args.before, args.horizon)


if __name__ == "__main__":
    raise SystemExit(main_cli())
