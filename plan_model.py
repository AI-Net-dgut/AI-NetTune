"""处理方案生成 + "原配置 vs 候选配置"对比仿真（闭环管控的"方案制定—动态调整"一环）。

设计原则（与项目口径一致）：

1. **不引入信道/传播模型**：参数改动 → 指标偏移只用经验系数折算，与申报书
   "Model-free 免建模"一致；但**绝不谎称是物理仿真或实测**，method/assumptions/
   limitations 里都如实写明。
2. **前后对比基于同一窗口的真实采样**：把偏移逐样本叠加后重新统计，
   而不是另造一套数——这样"相同条件下对比"是真的同条件。
3. **参数可编辑、可复算**：前端改参数后调用 /api/plan/simulate 重算，
   返回结构与 /api/agent/chat 里 plans[].simulation 完全一致。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import simulation_engine

# ---- 经验系数（全部可调，改动请同步 docs/api.md 的"方案仿真口径"）----
TILT_DB_PER_DEGREE = 1.2      # 下倾角每减小 1°，边缘 RSRP 约 +1.2 dB
POWER_DB_PER_DB = 1.0         # 发射功率每 +1 dB，RSRP 约 +1 dB
SINR_PER_RSRP = 0.4           # RSRP 每 +1 dB，SINR 约 +0.4 dB
THROUGHPUT_PER_DB = 0.03      # RSRP 每 +1 dB，吞吐约 +3%
PRB_PER_OFFLOAD_PCT = 0.8     # 分流比例每 +1%，本区 PRB 约 −0.8 个百分点
CAPACITY_PRB_DROP = 18.0      # 新增 1 个载波，PRB 约 −18 个百分点
THROUGHPUT_PER_PRB = 0.02     # PRB 每**降** 1 个百分点，吞吐约 +2%（拥塞缓解的收益）
LATENCY_PER_PRB = 0.004       # PRB 每 −1 个百分点，时延约 −0.4%
LOSS_PER_PRB = 0.02           # PRB 每 −1 个百分点，丢包约 −2%

WEAK_RSRP_DBM = -105.0        # 与 judge_ap 的研判阈值一致
SCENE_AREA = {"xMin": 0.0, "yMin": 0.0, "xMax": 500.0, "yMax": 450.0}
GRID = 20.0

SIM_METHOD = "经验系数外推（非信道模型）：参数改动折算为指标偏移，逐样本叠加后在同一窗口重算"
SIM_ASSUMPTIONS = [
    "对比双方使用同一时间窗口、同一批采样点，只有配置参数不同",
    "指标偏移按经验系数折算，未使用信道模型或射线追踪",
    "周边小区负载、邻区干扰、终端能力视为不变",
    "天线下倾角与发射功率的当前值取自默认工参（数据表未提供工参字段）",
]
SIM_LIMITATIONS = [
    "经验系数来自工程经验，不是本场景实测标定",
    "未评估参数调整对邻区的连带影响，也未做稳定性与切换冲击评估",
    "结果为趋势判断，不能替代现场验证",
]


# ---------------- 参数模板 ----------------
def _num(pid, ap, label, unit, current, suggested, lo, hi, step, desc):
    return {
        "id": pid, "objectId": ap, "key": pid, "label": label, "description": desc,
        "type": "number", "unit": unit, "currentValue": current, "suggestedValue": suggested,
        "editable": True, "unavailableReason": "", "min": lo, "max": hi, "step": step,
        "options": [],
    }


def _enum(pid, ap, label, unit, current, suggested, options, desc):
    return {
        "id": pid, "objectId": ap, "key": pid, "label": label, "description": desc,
        "type": "enum", "unit": unit, "currentValue": current, "suggestedValue": suggested,
        "editable": True, "unavailableReason": "", "min": None, "max": None, "step": None,
        "options": options,
    }


def _templates(ap, anomaly_type, base):
    """按故障类型给出候选方案。base 提供当前值（天线下倾角/功率/PRB 等）。"""
    if anomaly_type == "覆盖故障":
        return [
            {
                "slug": "tilt",
                "name": "下调天线下倾角",
                "description": "把倾角从 {tilt}° 调到 {tilt_new}°，让主瓣更覆盖本区，改善边缘 RSRP。",
                "parameters": [_num("tilt", ap, "天线下倾角", "°", base["tilt"], base["tilt"] - 3,
                                    0, 15, 1, "减小倾角可扩大覆盖半径，但可能增加对邻区干扰")],
                "goals": ["rsrpDbm", "throughputMbps"],
                "conditions": ["本区边缘弱覆盖、邻区无明显过覆盖"],
                "tradeoffs": ["覆盖外扩可能抬高邻区干扰，需复核邻区 SINR"],
                "tasks": ["现场核实天线下倾角实际读数", "调整后复测边缘点位 RSRP/SINR"],
            },
            {
                "slug": "power",
                "name": "提升发射功率",
                "description": "把发射功率从 {power} dBm 提到 {power_new} dBm，直接抬升覆盖电平。",
                "parameters": [_num("tx_power", ap, "发射功率", "dBm", base["power"], base["power"] + 3,
                                    20, 46, 1, "每 +1 dB 约提升 1 dB 边缘 RSRP；受功放与规范限制")],
                "goals": ["rsrpDbm", "sinrDb"],
                "conditions": ["该 AP 功放余量充足、无过覆盖投诉"],
                "tradeoffs": ["功耗与电费上升；过覆盖可能引起导频污染"],
                "tasks": ["确认设备功率余量与当地发射规范上限"],
            },
        ]
    if anomaly_type == "网络拥塞":
        return [
            {
                "slug": "offload",
                "name": "邻区负载均衡",
                "description": "把 {offload}% 的业务分流到邻区，降低本区 PRB 占用。",
                "parameters": [_num("offload_pct", ap, "向邻区分流比例", "%", 0, 15, 0, 50, 5,
                                    "每分流 1%，本区 PRB 约下降 0.8 个百分点")],
                "goals": ["resourceUtilizationPct", "throughputMbps"],
                "conditions": ["邻区有剩余容量、UE 处于重叠覆盖区"],
                "tradeoffs": ["分流可能带来切换增加，邻区若同时拥塞则无效"],
                "tasks": ["确认邻区 PRB 余量", "观察切换成功率变化"],
            },
            {
                "slug": "capacity",
                "name": "新增载波扩容",
                "description": "增加一个载波，直接提升小区容量，PRB 占用可显著下降。",
                "parameters": [_enum("capacity", ap, "扩容方式", "", "none", "one_carrier",
                                     [{"value": "none", "label": "不扩容"},
                                      {"value": "one_carrier", "label": "新增 1 个载波"}],
                                     "需要频谱与硬件资源；见效快但成本高")],
                "goals": ["throughputMbps", "resourceUtilizationPct"],
                "conditions": ["有可用频谱与基带板资源"],
                "tradeoffs": ["硬件与许可成本；需规划邻区频点避免同频干扰"],
                "tasks": ["核对频谱许可与基带板槽位"],
            },
        ]
    if anomaly_type == "边缘弱用户":
        return [
            {
                "slug": "beam",
                "name": "波束增益补偿 + 边缘引导",
                "description": "提高波束赋形增益 {beam_gain} dB，并在现场引导边缘用户至覆盖较好位置。",
                "parameters": [_num("beam_gain", ap, "波束增益补偿", "dB", 0, 3, 0, 6, 1,
                                    "只补偿本区边缘，避免全局抬功率")],
                "goals": ["sinrDb", "throughputMbps"],
                "conditions": ["边缘用户集中在固定方位"],
                "tradeoffs": ["波束变窄后移动中的用户可能频繁切换"],
                "tasks": ["现场确认边缘用户分布方位", "评估移动场景下的切换次数"],
            },
        ]
    return []


# ---------------- 指标重算 ----------------
def _mean(rows, key, adjust=None):
    total, count = 0.0, 0
    for r in rows:
        raw = r.get(key)
        if raw in (None, ""):
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if adjust is not None:
            v = adjust(v)
        total += v
        count += 1
    return round(total / count, 2) if count else None


def _weak_ratio(rows, d_rsrp):
    total = weak = 0
    for r in rows:
        raw = r.get("rsrp")
        if raw in (None, ""):
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        total += 1
        if v + d_rsrp < WEAK_RSRP_DBM:
            weak += 1
    return round(weak / total, 3) if total else None


def _deltas_from_values(param_values, plan):
    """把每个参数的变化折算成 (ΔRSRP, ΔPRB)。

    **唯一实现委托给 simulation_engine**（第二轮复查 P0）：
    此前这里用 (cur-new)*1.2、sim_config 用 (new-cur)*0.5，导致"评估改善、应用恶化"。
    现在两边都调 simulation_engine.calculate_effects，方向与量级不可能再分叉。

    enum 参数必须**命中选项白名单**才生效（审计 5.3）：非法值不产生任何增益。
    """
    baseline: dict = {}
    target: dict = {}
    for p in plan["parameters"]:
        pid = p["id"]
        raw = param_values.get(pid, p["suggestedValue"])
        if p["type"] == "enum":
            allowed = {str(o.get("value")) for o in (p.get("options") or [])}
            if str(raw) not in allowed:      # 非白名单值：不产生增益
                continue
            baseline[pid] = str(p["currentValue"])
            target[pid] = str(raw)
            continue
        try:
            new = float(raw)
            cur = float(p["currentValue"]) if p["currentValue"] is not None else new
        except (TypeError, ValueError):
            continue
        # 服务端再夹一次参数范围，避免非法值被当成"有效方案"算进结论
        if p["min"] is not None:
            new = max(float(p["min"]), new)
        if p["max"] is not None:
            new = min(float(p["max"]), new)
        baseline[pid] = cur
        target[pid] = new
    effects = simulation_engine.calculate_effects(baseline, target)
    return effects.get("rsrp", 0.0), effects.get("prb_usage", 0.0)


def _trend(rows, d_rsrp, ap_id):
    """10 分钟桶的趋势对比（时间带 +08:00 偏移，前端 planContract 要求 ISO 且带时区）。"""
    buckets = {}
    for r in rows:
        ts = r.get("timestamp")
        raw = r.get("rsrp")
        if not ts or raw in (None, ""):
            continue
        try:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            v = float(raw)
        except (TypeError, ValueError):
            continue
        key = dt - timedelta(minutes=dt.minute % 10, seconds=dt.second)
        buckets.setdefault(key, []).append(v)
    if len(buckets) < 2:
        return None
    points = []
    for key in sorted(buckets):
        vals = buckets[key]
        before = sum(vals) / len(vals)
        points.append({
            "time": key.strftime("%Y-%m-%dT%H:%M:%S") + "+08:00",
            "before": round(before, 2),
            "after": round(min(-40.0, max(-140.0, before + d_rsrp)), 2),
        })
    return {"metric": "rsrpDbm", "objectId": ap_id, "points": points}


def _coverage(rows, d_rsrp, ap_id):
    """20m 网格上的 RSRP 前后对比（只输出有采样的格子）。"""
    cells = {}
    for r in rows:
        try:
            x, y, v = float(r["ue_x"]), float(r["ue_y"]), float(r["rsrp"])
        except (KeyError, TypeError, ValueError):
            continue
        ix = int((x - SCENE_AREA["xMin"]) // GRID)
        iy = int((y - SCENE_AREA["yMin"]) // GRID)
        cells.setdefault((ix, iy), []).append(v)
    points = []
    for (ix, iy), vals in sorted(cells.items()):
        before = sum(vals) / len(vals)
        points.append({
            "x": round(SCENE_AREA["xMin"] + ix * GRID + GRID / 2, 1),
            "y": round(SCENE_AREA["yMin"] + iy * GRID + GRID / 2, 1),
            "before": round(before, 2),
            "after": round(min(-40.0, max(-140.0, before + d_rsrp)), 2),
        })
    if not points:
        return None
    return {"area": dict(SCENE_AREA), "cellSize": {"x": GRID, "y": GRID}, "points": points[:400]}


def simulate(plan, rows, param_values, scope, ap_id):
    """用给定参数值重算仿真结果；结构等同于 plans[].simulation。"""
    d_rsrp, d_prb = _deltas_from_values(param_values, plan)

    before = {
        "rsrpDbm": _mean(rows, "rsrp"),
        "sinrDb": _mean(rows, "sinr"),
        "throughputMbps": _mean(rows, "throughput"),
        "latencyMs": _mean(rows, "latency"),
        "packetLossPct": _mean(rows, "packet_loss"),
        "resourceUtilizationPct": _mean(rows, "prb_usage"),
    }
    after = {
        "rsrpDbm": _mean(rows, "rsrp", lambda v: min(-40.0, v + d_rsrp)),
        "sinrDb": _mean(rows, "sinr", lambda v: min(40.0, v + d_rsrp * SINR_PER_RSRP)),
        "throughputMbps": _mean(rows, "throughput",
                                lambda v: max(0.0, v * (1.0 + d_rsrp * THROUGHPUT_PER_DB
                                                        - d_prb * THROUGHPUT_PER_PRB))),
        "latencyMs": _mean(rows, "latency", lambda v: max(1.0, v * (1.0 + d_prb * LATENCY_PER_PRB))),
        "packetLossPct": _mean(rows, "packet_loss", lambda v: max(0.0, v * (1.0 + d_prb * LOSS_PER_PRB))),
        "resourceUtilizationPct": _mean(rows, "prb_usage",
                                        lambda v: min(100.0, max(0.0, v + d_prb))),
    }
    metrics = [{"key": k, "objectId": ap_id, "before": before[k], "after": after[k]}
               for k in ("rsrpDbm", "sinrDb", "throughputMbps",
                         "latencyMs", "packetLossPct", "resourceUtilizationPct")]

    weak_before = _weak_ratio(rows, 0.0)
    weak_after = _weak_ratio(rows, d_rsrp)
    # 只把**确实发生变化**的指标写进结论，避免"RSRP −99.43 → −99.43（+0.0）"这种噪声
    parts = []
    dr = None
    if before["rsrpDbm"] is not None and after["rsrpDbm"] is not None:
        dr = after["rsrpDbm"] - before["rsrpDbm"]
        if abs(dr) >= 0.5:
            parts.append(f"RSRP {before['rsrpDbm']} → {after['rsrpDbm']} dBm（{dr:+.1f}）")
    if weak_before is not None and weak_after is not None and abs(weak_after - weak_before) >= 0.005:
        parts.append(f"弱覆盖样本占比 {weak_before * 100:.0f}% → {weak_after * 100:.0f}%")
    if before["throughputMbps"] and after["throughputMbps"] is not None:
        ratio = after["throughputMbps"] / before["throughputMbps"] - 1.0
        if abs(ratio) >= 0.01:
            parts.append(f"吞吐 {before['throughputMbps']} → {after['throughputMbps']} Mbps"
                         f"（{ratio * 100:+.0f}%）")
    if abs(d_prb) >= 0.5 and before["resourceUtilizationPct"] is not None:
        parts.append(f"PRB {before['resourceUtilizationPct']} → {after['resourceUtilizationPct']}%")
    if before["latencyMs"] and after["latencyMs"] is not None:
        ratio = after["latencyMs"] / before["latencyMs"] - 1.0
        if abs(ratio) >= 0.01:
            parts.append(f"时延 {before['latencyMs']} → {after['latencyMs']} ms（{ratio * 100:+.0f}%）")
    summary = f"{plan['name']}： " + ("；".join(parts) if parts else "各指标基本不变") + "。"

    # RSRP 无变化时（如拥塞类方案：分流/载波扩容只影响 PRB），
    # trend/coverage 的 before 与 after 完全相同，属于冗余数据，直接不返回。
    # 前端 planContract 已处理 null：不显示对应按钮，默认展示"指标对比"。
    has_rsrp_change = abs(d_rsrp) > 0.01

    return {
        "id": f"SIM-{plan['id']}",
        "status": "completed",
        "scope": dict(scope),
        "method": SIM_METHOD,
        "summary": summary,
        "assumptions": list(SIM_ASSUMPTIONS),
        "limitations": list(SIM_LIMITATIONS),
        "parameterValues": {p["id"]: param_values.get(p["id"], p["suggestedValue"])
                            for p in plan["parameters"]},
        "metrics": metrics,
        "trend": _trend(rows, d_rsrp, ap_id) if has_rsrp_change else None,
        "coverage": _coverage(rows, d_rsrp, ap_id) if has_rsrp_change else None,
    }


def build_plans(ap_id, anomaly_type, rows, scope, base):
    """生成候选方案（每个方案自带一份按建议值算好的仿真结果）。

    base: {"tilt": 度, "power": dBm}——没有真实工参表时用固定初值，并在描述里说明。
    """
    templates = _templates(ap_id, anomaly_type, base)
    plans = []
    for i, tpl in enumerate(templates, 1):
        plan_id = f"PLAN-{ap_id}-{tpl['slug']}-{i}"
        plan = {
            "id": plan_id,
            "name": tpl["name"],
            "description": tpl["description"].format(
                tilt=base["tilt"], tilt_new=base["tilt"] - 3,
                power=base["power"], power_new=base["power"] + 3,
                offload=15, beam_gain=3),
            "scope": dict(scope),
            "objectIds": [ap_id],
            "goals": tpl["goals"],
            "conditions": tpl["conditions"],
            "tradeoffs": tpl["tradeoffs"],
            "tasks": tpl["tasks"],
            "parameters": tpl["parameters"],
            "simulation": None,
        }
        plan["simulation"] = simulate(
            plan, rows,
            {p["id"]: p["suggestedValue"] for p in plan["parameters"]},
            scope, ap_id)
        plans.append(plan)
    return plans
