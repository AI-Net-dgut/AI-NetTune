"""用户级体验判定与吞吐目标计算；全部门槛均为项目暂定经验参数。"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import math
import os


def _setting(name, default, low, high):
    """拒绝非有限或越界配置，避免配置错误静默改变判定口径。"""
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} 必须在 {low} ~ {high} 范围内")
    if isinstance(default, int) and not value.is_integer():
        raise ValueError(f"{name} 必须是整数")
    return value


POLICY = {
    "version": "ue-experience-v2",
    "source": "项目暂定经验门槛，未进行实网标定，不代表通用业务 SLA",
    "badShareLimit": _setting("NETTUNE_UE_BAD_SHARE_LIMIT", .10, 0, 1),
    "persistentMinutes": int(_setting("NETTUNE_UE_PERSIST_MINUTES", 5, 1, 1440)),
    "minValidSamples": int(_setting("NETTUNE_UE_MIN_SAMPLES", 10, 1, 10000)),
    "minValidShare": _setting("NETTUNE_UE_MIN_VALID_SHARE", .8, 0, 1),
    "freshnessSeconds": _setting("NETTUNE_UE_FRESHNESS_SECONDS", 90, 1, 3600),
}
METRICS = {
    "sinr": (5., "SINR", "dB", "<"),
    "rsrp": (-105., "RSRP", "dBm", "<"),
    "latency": (50., "时延", "ms", ">"),
}


def finite(value):
    """缺失、布尔值、无穷及非数字均不当作观测值 0。"""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def mean(rows, field):
    values = [v for row in rows if (v := finite(row.get(field))) is not None]
    return sum(values) / len(values) if values else None


def assess(rows, end, policy=None):
    """分别计算整窗比例、连续区间和末端状态；时间断档会中断连续段。"""
    policy = dict(policy or POLICY)
    end_time = datetime.fromisoformat(end)
    checks, spans = [], []
    tail_bad, tail_unknown, tail_persistent = False, False, False
    for field, (threshold, label, unit, direction) in METRICS.items():
        valid, minutes = [], {}
        for row in rows:
            value = finite(row.get(field))
            if value is None:
                continue
            bad = value < threshold if direction == "<" else value > threshold
            valid.append((value, bad))
            try:
                stamp = datetime.fromisoformat(row["timestamp"])
            except (KeyError, TypeError, ValueError):
                continue
            minute = stamp.replace(second=0, microsecond=0)
            # 同分钟任一样本越阈即保留该分钟提示，重复行不会增加持续时长。
            minutes[minute] = minutes.get(minute, False) or bad
        count, total = len(valid), len(rows)
        sufficient = (count >= policy["minValidSamples"] and total > 0
                      and count / total >= policy["minValidShare"])
        share = sum(bad for _, bad in valid) / count if count else None
        series = sorted(minutes.items())
        run, previous = [], None
        metric_spans = []
        for stamp, bad in series:
            if not bad or (previous and (stamp - previous).total_seconds() != 60):
                if len(run) >= policy["persistentMinutes"]:
                    metric_spans.append(run)
                run = []
            if bad:
                run.append(stamp)
            previous = stamp
        if len(run) >= policy["persistentMinutes"]:
            metric_spans.append(run)
        for interval in metric_spans:
            spans.append({"metric": field, "start": interval[0].isoformat(sep=" "),
                          "end": interval[-1].isoformat(sep=" "), "minutes": len(interval)})
        fresh = bool(series and 0 <= (end_time - series[-1][0]).total_seconds()
                     <= policy["freshnessSeconds"])
        last_bad = bool(fresh and series[-1][1])
        persistent = bool(last_bad and len(run) >= policy["persistentMinutes"])
        tail_unknown |= not fresh
        tail_bad |= last_bad
        tail_persistent |= persistent
        limit = policy["badShareLimit"]
        share_text = f"{share * 100:.1f}%" if share is not None else "无法计算"
        checks.append({"metric": field, "value": mean(rows, field), "threshold": threshold,
                       "ok": share <= limit if sufficient else None, "share": share,
                       "validSamples": count, "totalSamples": total, "unit": unit,
                       "label": f"{label}{direction}{threshold:g} {unit} 的有效采样越阈占比 {share_text}"
                                f"（暂定门槛 ≤{limit*100:g}%）",
                       "dataStatus": "ready" if sufficient else "insufficient_data",
                       "endState": "persistent_anomaly" if persistent else "warning" if last_bad
                                   else "within_policy" if fresh else "unknown"})
    sufficient = all(c["dataStatus"] == "ready" for c in checks)
    window = ("exceeded" if any(c["ok"] is False for c in checks) else
              "within_policy" if sufficient else "insufficient_data")
    end_state = ("persistent_anomaly" if tail_persistent else "warning" if tail_bad else
                 "unknown" if tail_unknown or not sufficient else
                 "recovered" if spans else "within_policy")
    return {"dataStatus": "ready" if sufficient else "insufficient_data",
            "windowAssessment": window, "endState": end_state,
            "checks": checks, "anomalySpans": spans, "policy": policy}


def goal(baseline, pct, predicted=None):
    """目标绑定原始基线；零基线不计算相对提升率。"""
    baseline, pct, predicted = finite(baseline), finite(pct), finite(predicted)
    if baseline is None or baseline <= 0 or pct is None or pct <= 0:
        return {"status": "not_evaluable", "baseline": baseline, "percent": pct,
                "target": None, "predicted": predicted, "gap": None, "improvementPct": None}
    target = baseline * (1 + pct / 100)
    return {"status": "pending" if predicted is None else
                      "estimated_met" if predicted >= target - 1e-9 else "estimated_unmet",
            "baseline": round(baseline, 4), "percent": pct, "target": round(target, 4),
            "predicted": predicted, "gap": None if predicted is None else round(max(0., target-predicted), 4),
            "requiredGain": round(target-baseline, 4),
            "improvementPct": None if predicted is None else round((predicted/baseline-1)*100, 4)}


def chart_series(rows):
    """逐样本保留服务 AP 与缺失值；不插值、不用均值替代时序。时间按项目北京时间返回。"""
    result = []
    for row in rows:
        try:
            stamp = datetime.fromisoformat(row["timestamp"])
        except (KeyError, ValueError, TypeError):
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone(timedelta(hours=8)))
        result.append({"time": stamp.isoformat(), "apId": row.get("ap_id"),
                       **{field: finite(row.get(field)) for field in
                          ("rsrp", "sinr", "throughput", "latency")}})
    return sorted(result, key=lambda item: datetime.fromisoformat(item["time"]))
