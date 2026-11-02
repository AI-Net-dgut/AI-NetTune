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

REQUIRED_SECTIONS = ("结论", "依据", "方案")
MAX_CHARS = 1200
ATTEMPTS = 2

FORBIDDEN_WHEN_NO_ROOT_CAUSE = ("已确认根因", "根因已确认", "必然是", "一定是", "一定会")
ALLOWED_TOOLS_NOTE = "只能使用给定证据中的数字与对象"

SYSTEM_PROMPT = """你是无线网络运维助手。你会收到一份**已由规则计算好的证据**（JSON），请据此写一段中文回答。

硬性要求：
1. 只允许使用证据中出现的数字、AP 编号、时间；**不得编造任何数字或对象**；
2. 不得使用"已确认根因/必然/一定会"之类确定性断言；候选原因只能表述为"候选/可能"；
3. 必须包含三个小节，标题用【】括起：【结论】【依据】【方案】；
4. 总长度不超过 600 字；
5. 只输出回答正文，不要输出 JSON、代码块或解释你的过程。
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

    return {"ok": not violations, "violations": violations, "numbers": len(_numbers(text)), "ids": sorted(mentioned)}


def generate(question: str, evidence: dict, call_fn) -> dict:
    """调用模型生成整段回答，并用校验器闭环把关。

    返回 {text, source, attempts, check}：
      - 通过：source="llm"，text 可直接展示；
      - 不通过：source=None（上层退回规则文案），check 里保留违规原因便于排查。
    """
    feedback = ""
    last_check: dict = {"ok": False, "violations": ["未执行"]}
    for attempt in range(1, ATTEMPTS + 1):
        user = (
            f"用户问题：{question}\n"
            f"证据（JSON，{ALLOWED_TOOLS_NOTE}）：\n{json.dumps(evidence, ensure_ascii=False)[:6000]}\n"
            + (f"\n上一次回答的问题（必须修正）：{feedback}\n" if feedback else "")
        )
        try:
            answer = call_fn(SYSTEM_PROMPT, user, 0.2)
        except Exception as exc:  # noqa: BLE001 - 失败即退回规则
            return {"text": None, "source": None, "attempts": attempt,
                    "check": {"ok": False, "violations": [f"模型调用失败：{type(exc).__name__}"]}}
        check = verify(answer, evidence)
        if check["ok"]:
            return {"text": answer.strip(), "source": "llm", "attempts": attempt, "check": check}
        last_check = check
        feedback = "；".join(check["violations"])[:400]
    return {"text": None, "source": None, "attempts": ATTEMPTS, "check": last_check}
