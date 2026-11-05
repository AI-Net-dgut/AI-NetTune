"""回答中的状态和带单位指标校验；知识库数值不能充当本次观测值。"""
from __future__ import annotations
import re
from user_experience import finite

META = {
    "rsrp": ("rsrp", "dBm"), "rsrpDbm": ("rsrp", "dBm"), "rsrp_avg": ("rsrp", "dBm"),
    "sinr": ("sinr", "dB"), "sinrDb": ("sinr", "dB"), "sinr_avg": ("sinr", "dB"),
    "throughput": ("throughput", "Mbps"), "throughputMbps": ("throughput", "Mbps"),
    "thr_avg": ("throughput", "Mbps"), "latency": ("latency", "ms"), "latencyMs": ("latency", "ms"),
    "packet_loss": ("loss", "%"), "packetLossPct": ("loss", "%"),
    "prb_usage": ("prb", "%"), "resourceUtilizationPct": ("prb", "%"),
}
NAMES = {"rsrp": r"RSRP", "sinr": r"SINR", "throughput": r"吞吐(?:量)?|throughput|速率",
         "latency": r"时延|延迟", "loss": r"丢包(?:率)?", "prb": r"PRB(?:占用率)?|资源占用率"}
PARAMS = {"tilt": (r"(?:天线下)?倾角", "°"), "tx_power": (r"发射功率|功率", "dBm"),
          "beam_gain": (r"波束增益(?:补偿)?", "dB"), "offload_pct": (r"(?:向邻区)?分流比例", "%")}
NUMBER = r"[-+−]?\d+(?:\.\d+)?"


def canonical_id(text):
    match = re.fullmatch(r"(AP|UE)[-－\s]?0*(\d+)", text, re.I)
    return f"{match[1].upper()}-{int(match[2]):02d}" if match else text.upper()


def measurement_facts(evidence):
    """仅采纳计算结果和规则草稿中的指标，不采纳问题或知识文档。"""
    facts = {key: set() for key in NAMES}
    def add(key, value):
        number = finite(value)
        if key in facts and number is not None:
            facts[key].add(number)
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"references", "question", "policy"}:
                    continue
                if key in META and not isinstance(value, (dict, list)):
                    add(META[key][0], value)
                walk(value)
            key = node.get("key") or node.get("metric")
            if key in META:
                metric = META[key][0]
                for field in ("before", "after", "value", "avg_value", "threshold"):
                    add(metric, node.get(field))
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            for metric, names in NAMES.items():
                for match in re.finditer(rf"(?:{names})(?:\s*均值)?\s*[:：为是=]?\s*({NUMBER})\s*(?:dBm|dB|Mbps|ms|%)", node, re.I):
                    add(metric, match[1].replace("−", "-"))
    walk(evidence)
    return facts


def verify_measurements(answer, evidence):
    """按指标及单位匹配，禁止将频段数、对象号等数字挪用成 KPI。"""
    facts = measurement_facts(evidence)
    violations = []
    for metric, names in NAMES.items():
        # 限制语法跨度，避免把“采样占比”或另一个指标的值当作该指标。
        pattern = rf"(?:{names})(?:\s*(?:均值|平均值|平均))?\s*(?:为|是|:|：|=|达到|约为|约)?\s*({NUMBER})\s*(dBm|dB|Mbps|ms|%)"
        expected_unit = next(unit for key, unit in META.values() if key == metric)
        for match in re.finditer(pattern, answer, re.I):
            value = float(match[1].replace("−", "-"))
            unit = match[2]
            if unit.lower() != expected_unit.lower() or not any(abs(value-v) <= .051 for v in facts[metric]):
                violations.append(f"指标数值或单位缺少对应证据：{match[0]}")
        pairs = {(finite(row.get("before")), finite(row.get("after")))
                 for plan in evidence.get("plans") or [] for row in plan.get("effects") or []
                 if META.get(row.get("metric"), (None,))[0] == metric}
        arrow = rf"(?:{names})\s*({NUMBER})\s*(?:dBm|dB|Mbps|ms|%)?\s*(?:→|->|调整为|调整至)\s*({NUMBER})"
        for match in re.finditer(arrow, answer, re.I):
            left, right = (float(match[i].replace("−", "-")) for i in (1, 2))
            if not any(a is not None and b is not None and abs(left-a) <= .051 and abs(right-b) <= .051 for a,b in pairs):
                violations.append(f"指标效果对与对应指标证据不一致：{match[0]}")
    for pid, (names, unit) in PARAMS.items():
        parameters = [p for plan in evidence.get("plans") or [] for p in plan.get("parameters") or []
                      if p.get("id") == pid]
        values = [finite(p.get(key)) for p in parameters for key in ("current", "suggested")]
        scalar = rf"(?:{names})\s*(?:为|是|:|：|=|约为)?\s*({NUMBER})\s*({re.escape(unit)})"
        for match in re.finditer(scalar, answer, re.I):
            value = float(match[1].replace("−", "-"))
            if not any(v is not None and abs(value-v) <= .051 for v in values):
                violations.append(f"参数数值缺少对应证据：{match[0]}")
        arrow = rf"(?:{names})\s*({NUMBER})\s*(?:{re.escape(unit)})?\s*(?:→|->|调整为|调整至)\s*({NUMBER})"
        for match in re.finditer(arrow, answer, re.I):
            left, right = (float(match[i].replace("−", "-")) for i in (1, 2))
            if not any(finite(p.get("current")) is not None and finite(p.get("suggested")) is not None
                       and abs(left-float(p["current"])) <= .051 and abs(right-float(p["suggested"])) <= .051
                       for p in parameters):
                violations.append(f"参数对与对应参数证据不一致：{match[0]}")
    return violations


def verify_assessment(text, evidence):
    """用户体验结论逐层保留，禁止用正常概括末端异常或缺数据。"""
    assessment = evidence.get("assessment") or (evidence.get("detail") or {}).get("assessment") or {}
    if not assessment:
        return []
    errors = []
    window, end = assessment.get("windowAssessment"), assessment.get("endState")
    conclusion = re.search(r"【结论】([^【]*)", text)
    section = conclusion[1] if conclusion else text
    if assessment.get("dataStatus") != "ready":
        errors.append("用户有效数据不足，不接受模型改写判定")
    if window == "exceeded":
        if not re.search(r"未达标|不达标|超出.*门槛|超过.*门槛", section):
            errors.append("必须保留整窗体验未达标结论")
        if re.search(r"(?<!未)(?<!不)(?:体验|整窗)[^。；\n]{0,4}(?<!未)(?<!不)达标|体验正常|指标良好", section):
            errors.append("不能把整窗未达标改写为正常")
    if window == "within_policy" and not re.search(r"整窗[^。；\n]{0,20}(?:达标|未超|未超过)|占比[^。；\n]{0,20}(?:门槛|以内)", section):
        errors.append("必须明确整窗结论仅针对暂定占比门槛")
    if window == "within_policy" and re.search(r"整窗[^。；\n]{0,10}(?:未达标|不达标|超过门槛)", section):
        errors.append("不能把整窗占比未超门槛改写为整窗未达标")
    if end == "persistent_anomaly" and not re.search(r"(?:截至|末端|当前)[^。；\n]{0,35}(?:连续异常|持续异常|仍.*异常)", section):
        errors.append("必须保留截至分析时刻的连续异常状态")
    if end == "warning" and not re.search(r"短时越阈|短时异常|需要观察|继续观察", section):
        errors.append("必须保留末端短时越阈提示")
    if end == "recovered" and not re.search(r"已恢复|已经恢复", section):
        errors.append("必须保留窗口内异常已恢复状态")
    if end == "unknown" and not re.search(r"无法判断|未知|数据不足|过旧", section):
        errors.append("末端状态未知，不得声称当前正常")
    if end in ("persistent_anomaly", "warning", "unknown") and re.search(r"当前.*正常|截至[^。；\n]*正常|无需关注|无需处理|无异常", section):
        errors.append("不能用正常结论覆盖异常或未知的末端状态")
    if end in ("within_policy", "recovered") and re.search(r"(?:当前|截至|末端)[^。；\n]{0,20}(?:存在|仍有|持续|连续)异常", section):
        errors.append("不能将末端未越阈或已恢复改写为当前仍异常")
    return errors
