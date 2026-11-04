"""由大模型生成"回答主体"，并用**校验器（harness）闭环核对**后才允许展示。

设计动机（用户诉求 + 风险控制）：
    现在的回答是"规则拼出来的四段话 + 一段模型解读"，模型看起来只做了点缀。
    本模块让模型**直接写整段回答**，但模型会编数字、编对象、编因果，因此必须配一个
    确定性校验器：任何数字/AP 名称/因果断言都必须能在证据里找到出处，否则拒收。

流水线：
    evidence(规则算出的证据) → generate()（模型写整段） → verify()（逐条核对）
      ├─ 通过 → 返回 (text, source="llm", check=ok)，界面标注"大模型生成·已校验"
      └─ 不通过 → 带违规清单重写 1 次；仍不通过 → 返回 None，上层退回规则文案

校验项（缺一不可）：
1. 数字必须来自证据（允许 ±0.5% 或 ±0.1 的取整误差），否则视为编造；
2. 提到的 AP / UE 编号必须在证据里出现，否则视为编造对象；
3. 禁止在无证据时使用"已确认根因/必然/一定会"等确定性断言；
4. 结构必须包含必需小节（结论/依据/方案），否则视为不合格；
5. 长度上限，避免模型灌水。
"""
from __future__ import annotations

import json
import re
import diagnosis_focus

REQUIRED_SECTIONS = ("结论", "依据", "方案")
MAX_CHARS = 1200
ATTEMPTS = 2

# 「提升」类词后跟的数字必须为非负，「下降」类必须为非正（防方向写反）
UP_WORDS = ("提升", "上升", "增加", "改善", "提高")
DOWN_WORDS = ("下降", "降低", "减少", "恶化", "变差")

FORBIDDEN_WHEN_NO_ROOT_CAUSE = ("已确认根因", "根因已确认", "必然是", "一定是", "一定会")
ALLOWED_TOOLS_NOTE = "只能使用给定证据中的数字与对象"

SYSTEM_PROMPT = """你是无线网络运维助手。你会收到一份**已由规则计算好的证据**（JSON），请据此写一段中文回答。

硬性要求：
1. 只允许使用证据中出现的数字、AP 编号、时间、参数值；**不得编造任何数字或对象**；
2. 不得使用"已确认根因/必然/一定会"之类确定性断言；候选原因只能表述为"候选/可能"；
3. 必须包含小节，标题用【】括起，至少三个：【结论】【依据】【方案】；
   若证据里有方案仿真数据，再加【预计效果】【前提与风险】两节；
4. 提到参数调整时必须写成"当前值→建议值"（例如"8°→5°"、"33→36 dBm"），
   数值必须与证据里 parameters 的 current / suggested 完全一致，不得改写；
5. 【依据】务必**简短**：一句话说明"哪几项指标越阈、方向如何"，最多引用 1~2 个关键数字，
   其余数字不要再罗列——界面下方有"证据与仿真明细"面板逐项列出；
6. 只能引用证据里出现的方案（按名字），不得提出证据之外的新方案；
7. 总长度不超过 500 字；
8. 只输出回答正文，不要输出 JSON、代码块或解释你的过程。
"""


def _numbers(text: str) -> list[float]:
    """抽取文本中的数字（含负号与小数），忽略百分比符号本身。"""
    out: list[float] = []
    for match in re.findall(r"-?\d+(?:\.\d+)?", text or ""):
        try:
            out.append(float(match))
        except ValueError:
            continue
    return out


def _evidence_numbers(evidence: dict) -> set[float]:
    """把证据里所有数值（含嵌套）摊平成集合，供数字核对用。"""
    values: set[float] = set()

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            values.add(round(float(node), 2))
        elif isinstance(node, str):
            for number in _numbers(node):
                values.add(round(number, 2))

    walk(evidence)
    return values


def _evidence_ids(evidence: dict) -> set[str]:
    text = json.dumps(evidence, ensure_ascii=False)
    return {m.upper() for m in re.findall(r"(?:AP|UE)[-－\s]?0*\d+", text)}


def _close_enough(value: float, pool: set[float]) -> bool:
    """数字是否可视为"来自证据"：允许取整（±0.5）与百分比换算误差。"""
    for candidate in pool:
        if abs(candidate - value) <= 0.5:
            return True
        if candidate and abs(value) <= 100 and abs(abs(value) - abs(candidate)) <= 0.5:
            return True
    return False


def verify(answer: str, evidence: dict) -> dict:
    """逐条核对模型输出；返回 {ok, violations, numbers, ids}。"""
    violations: list[str] = []
    text = (answer or "").strip()
    if not text:
        return {"ok": False, "violations": ["回答为空"], "numbers": [], "ids": []}

    if len(text) > MAX_CHARS:
        violations.append(f"过长（{len(text)} 字 > {MAX_CHARS}）")

    for section in REQUIRED_SECTIONS:
        if f"【{section}】" not in text:
            violations.append(f"缺少小节【{section}】")

    focus = evidence.get("answer_focus") or {}
    topic_words = {"coverage": r"覆盖", "congestion": r"拥塞", "connections": r"连接|接入|切换",
                   "priority": r"优先|同档|先核查|排序"}
    topic = topic_words.get(focus.get("category")) or topic_words.get(focus.get("kind"))
    if focus and focus.get("status") != "confirmed":
        violations.append("本次专项未确诊，仅使用确定性提示，不接受模型编写故障与方案")
    if focus.get("kind") == "priority" and focus.get("status") == "confirmed":
        conclusion = re.search(r"【结论】([^【]*)", text)
        subject_ids = set(re.findall(r"AP-\d+", conclusion.group(1) if conclusion else ""))
        targets = set(focus.get("target_aps") or [])
        if not subject_ids.intersection(targets) or subject_ids - targets:
            violations.append("优先级结论必须指向本次排序目标；同档及背景对象放在依据中说明")
    if focus.get("kind") in ("coverage", "congestion") and focus.get("status") == "confirmed":
        allowed = set(focus.get("target_aps") or [])
        stated = {m.upper().replace("－", "-").replace(" ", "") for m in re.findall(r"AP[-－\s]?0*\d+", text)}
        if stated - allowed or not stated.intersection(allowed):
            violations.append("专项回答的诊断对象必须对应本次 target_aps，不能借用其他 AP 的指标")
    if focus and focus.get("status") == "confirmed" and evidence.get("current_state") == "recovered_in_window":
        if not re.search(r"已恢复|已经恢复|已结束|已经结束", text):
            violations.append("窗口内故障已恢复，回答必须保留这一时间状态")
    if topic and not re.search(topic, text):
        violations.append("回答未回应本次专项问题")
    if focus.get("status") in ("not_confirmed", "insufficient"):
        if not re.search(r"未确诊|不足|待核查|无法确认|不能确认", text):
            violations.append("专项证据不足时必须保留未确诊或无法确认的限制")
        if re.search(r"(?:→|->|调整为|调整至)\s*[-+]?\d", text):
            violations.append("未确诊专项不可给出其他故障的配置调整")
    if not focus.get("plan_eligible", True) and re.search(r"(?:预计|预期|仿真).{0,12}(?:提升|改善|降低|增加).{0,5}\d", text):
        violations.append("无对应方案时不可声称已得到量化仿真效果")

    # 1) 数字必须来自证据
    pool = _evidence_numbers(evidence)
    unknown = sorted({n for n in _numbers(text) if not _close_enough(n, pool)})
    # 允许 1/2/3 之类序数与年份以外的极小整数（步骤编号、条目编号）
    unknown = [n for n in unknown if abs(n) > 5 and n not in (100.0,)]
    if unknown:
        violations.append(f"出现证据中没有的数字：{unknown[:5]}")

    # 2) 对象必须来自证据
    known_ids = _evidence_ids(evidence)
    mentioned = {m.upper().replace("－", "-").replace(" ", "") for m in re.findall(r"(?:AP|UE)[-－\s]?0*\d+", text)}
    unknown_ids = sorted({m for m in mentioned if m not in {k.replace(" ", "") for k in known_ids}})
    if unknown_ids:
        violations.append(f"出现证据中没有的对象：{unknown_ids}")

    # 3) 无根因证据时禁止确定性断言
    if not evidence.get("root_cause_confirmed"):
        hit = [word for word in FORBIDDEN_WHEN_NO_ROOT_CAUSE if word in text]
        if hit:
            violations.append(f"使用了确定性断言：{hit}")

    # 4) 参数调整必须与证据一致 —— 只拦**硬矛盾**：
    #    仅当"左侧数值 == 某个参数当前值"、但右侧 != 该参数建议值时才报错。
    #    早期版本把所有 "A→B" 都当参数对，于是仿真效果（-111.06→-107.46）被误判成违规，
    #    整段回答被拒、退回规则文案（实测踩到）。
    pairs = _evidence_param_pairs(evidence)
    if pairs:
        allowed = set(pairs) | {(round(b, 2), round(a, 2)) for a, b in pairs} | _evidence_effect_pairs(evidence)
        stated = re.findall(r"(-?\d+(?:\.\d+)?)\s*(?:°|dBm|dB|%|个)?\s*(?:→|->|调整为|调整至)\s*(-?\d+(?:\.\d+)?)", text)
        for left, right in stated:
            a, b = round(float(left), 2), round(float(right), 2)
            if (a, b) in allowed:
                continue
            # 左侧不是任何参数的当前值 → 不是"参数调整"，交给数字检查去管
            if not any(abs(a - cur) <= 0.5 for cur, _ in pairs):
                continue
            violations.append(f"参数调整与证据不一致：{left}→{right}（证据为 "
                              + "、".join(f"{cur}→{tgt}" for cur, tgt in pairs) + "）")

    # 5) 方向矛盾（防"改善 −3.6 dB"）：只在**正负号明显矛盾**时报错。
    #    难点：RSRP 的绝对值本身就是负数（"提升至 -107.46 dBm"是正常表述），不能一律当增量。
    #    因此只在"词后 10 字内不含 至/到、且单位是 dB/%/Mbps（非 dBm）"时判断增量符号。
    for word in UP_WORDS:
        for match in re.finditer(word + r"[^。；\n至到]{0,10}?(-\d+(?:\.\d+)?)\s*(?:dB(?!m)|%|Mbps)", text):
            violations.append(f"'{word}'后面出现负增量：{match.group(0)}")
    for word in DOWN_WORDS:
        for match in re.finditer(word + r"[^。；\n至到]{0,10}?(\+\d+(?:\.\d+)?)\s*(?:dB(?!m)|%|Mbps)", text):
            violations.append(f"'{word}'后面出现正增量：{match.group(0)}")

    # 6) 不得提出证据之外的方案名（用《》或「」标注的方案名必须在证据里）
    known_names = {str(n) for n in _evidence_plan_names(evidence)}
    if known_names:
        for name in re.findall(r"[《「]([^》」]{2,20})[》」]", text):
            if name not in known_names and not any(name in k or k in name for k in known_names):
                violations.append(f"出现证据之外的方案名：{name}")

    return {"ok": not violations, "violations": violations, "numbers": len(_numbers(text)), "ids": sorted(mentioned)}


def _evidence_param_pairs(evidence: dict) -> list[tuple[float, float]]:
    """证据里所有 (当前值 → 建议值) 参数对，供"参数必须一致"核对。"""
    pairs: list[tuple[float, float]] = []
    for plan in evidence.get("plans") or []:
        for param in (plan.get("parameters") or []):
            current, suggested = param.get("current"), param.get("suggested")
            if isinstance(current, (int, float)) and isinstance(suggested, (int, float)):
                pairs.append((round(float(current), 2), round(float(suggested), 2)))
    return pairs


def _evidence_deltas(evidence: dict) -> list[float]:
    """证据里方案仿真的指标变化（绝对值集合），用于核对"提升/下降 N"的方向与量级。"""
    out: list[float] = []
    for plan in evidence.get("plans") or []:
        effects = plan.get("effects") or []
        if isinstance(effects, list):
            for row in effects:
                value = row.get("delta") if isinstance(row, dict) else None
                if isinstance(value, (int, float)):
                    out.append(round(abs(float(value)), 2))
        simulation = plan.get("simulation")
        if isinstance(simulation, dict):
            for value in simulation.values():
                if isinstance(value, (int, float)):
                    out.append(round(abs(float(value)), 2))
    return out


def _evidence_effect_pairs(evidence: dict) -> set[tuple[float, float]]:
    """证据里仿真效果的 (before, after) 对：这些 "A→B" 不是参数调整，必须放行。"""
    out: set[tuple[float, float]] = set()
    for plan in evidence.get("plans") or []:
        for row in (plan.get("effects") or []):
            if isinstance(row, dict):
                before, after = row.get("before"), row.get("after")
                if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                    out.add((round(float(before), 2), round(float(after), 2)))
    return out


def _evidence_plan_names(evidence: dict) -> list[str]:
    return [str(p.get("name")) for p in (evidence.get("plans") or []) if p.get("name")]


def generate(question: str, evidence: dict, call_fn) -> dict:
    """调用模型生成整段回答，并用校验器闭环把关。

    返回 {text, source, attempts, check}：
      - 通过：source="llm"，text 可直接展示；
      - 不通过：source=None（上层退回规则文案），check 里保留违规原因便于排查。
    """
    focus = evidence.get("answer_focus") or {}
    if focus and focus.get("status") != "confirmed":
        return {"text": None, "source": None, "attempts": 0,
                "check": {"ok": False, "violations": ["专项未确诊，使用已有证据生成的规则提示"]}}
    feedback = ""
    last_check: dict = {"ok": False, "violations": ["未执行"]}
    for attempt in range(1, ATTEMPTS + 1):
        user = (
            f"用户问题：{question}\n"
            f"证据（JSON，{ALLOWED_TOOLS_NOTE}）：\n{json.dumps(evidence, ensure_ascii=False)[:6000]}\n"
            + (f"\n上一次回答的问题（必须修正）：{feedback}\n" if feedback else "")
        )
        try:
            answer = call_fn(SYSTEM_PROMPT + (diagnosis_focus.prompt(evidence["answer_focus"])
                                             if evidence.get("answer_focus") else ""), user, 0.2)
        except Exception as exc:  # noqa: BLE001 - 失败即退回规则
            return {"text": None, "source": None, "attempts": attempt,
                    "check": {"ok": False, "violations": [f"模型调用失败：{type(exc).__name__}"]}}
        check = verify(answer, evidence)
        if check["ok"]:
            return {"text": answer.strip(), "source": "llm", "attempts": attempt, "check": check}
        last_check = check
        feedback = "；".join(check["violations"])[:400]
    return {"text": None, "source": None, "attempts": ATTEMPTS, "check": last_check}
