# llm_agent.py —— LLM 意图解析 + 工具编排（第2周：程思源；第5周升级：黑白名单分层正则 + 结构化JSON Prompt + 容错解析）
# 职责：听懂用户自然语言 → 输出四分类意图 JSON → 交给 main.py 的确定性接口执行
# 降级：LLM 不可用/超时/输出乱时，自动退回关键词规则版，保证演示不掉链子
import json
import os
import re
import requests
from llm_budget import network_timeouts, transport_failed, with_request_budget

# ===== 大模型配置（⚠️ 安全：key 一律走环境变量，禁止写入代码/提交仓库）=====
# 永久设置一次（新开终端即生效）：setx LLM_API_KEY "sk-你的真实key"
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
# DeepSeek / 豆包 / OpenAI 兼容接口都行，改 URL 和模型名即可
LLM_URL = os.getenv("LLM_URL", "https://api.deepseek.com/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# ===== 升级1：黑白名单分层正则（cell-free agent.py 借鉴：先排除提问/解释，再匹配工具意图）=====
# 黑名单：命中即视为"问原因/求解释"，不触发任何工具白名单，交给 LLM 或 KPI 兜底。
# 关键：不要放"原因/分析"这类词（"分析问题原因"是研判诉求，不能被挡掉）。
BLACKLIST_PATTERNS = [
    "为什么", "为何", "什么原因", "咋回事", "怎么回事", "什么意思",
    "解释", "怎么看", "啥原因", "是什么",
]

# 与 main.TOOLS 的键保持一致（模型只允许返回这些动作名，其它一律按不可解析降级）
TOOL_NAMES = {"query_kpi", "fault_diagnosis", "get_heatmap", "get_topology", "forecast", "chat", "user_experience"}

# 因果问句 + 网络故障词 → 走故障研判，不能被黑名单打成"概念解释"。
# 依据：核查清单第 3 条实测——"为什么 AP-03 信号差"落到 query_kpi，
# 同一窗口"分析 AP-03 故障"却能进 fault_diagnosis，问原因反而看不到候选原因与方案。
#
# 注意（复审 P2）：**不要放单字**（"卡""慢"）。单字会让"为什么银行卡被冻结了"
# "为什么审批这么慢"这类与网络无关的问句也进网络研判，返回的却是网络拥塞结论。
# 所以拆成两类：强词本身就指向网络；弱词必须配合网络上下文才算（"网络怎么这么卡" vs "键盘这么卡"）。
CAUSAL_HINTS_STRONG = [
    "信号差", "信号弱", "弱覆盖", "掉线", "断线", "卡顿", "网络卡", "上网卡",
    "网速", "丢包", "延迟高", "时延高", "速率低", "拥塞", "覆盖不好", "网络差", "网络慢",
]
CAUSAL_HINTS_WEAK = ["太卡", "很卡", "这么卡", "卡得", "不通", "故障", "异常"]
NETWORK_CONTEXT = [
    "网络", "信号", "AP", "ap", "基站", "覆盖", "上行", "下行", "蜂窝",
    "5G", "5g", "WiFi", "wifi", "频谱", "频段", "小区", "用户", "UE", "时延", "丢包",
]
# 兼容旧引用（测试/外部脚本可能只引用这一个名字）
CAUSAL_DIAGNOSIS_HINTS = CAUSAL_HINTS_STRONG + CAUSAL_HINTS_WEAK
# 纯概念解释标志：即使带故障词也留在黑名单（"解释一下什么是掉线""什么是弱覆盖"）
CONCEPT_PATTERNS = ["什么是", "是什么意思", "解释", "概念", "定义", "原理", "区别"]

# 白名单：按优先级分层匹配工具意图（明确的指令词优先，避免宽泛词抢意图）
HEATMAP_PATTERNS = ["热力", "信号分布", "覆盖图", "覆盖热", "地图"]
TOPOLOGY_PATTERNS = ["拓扑", "基站", "连接关系", "连接情况"]
FORECAST_PATTERNS = ["预测", "未来", "预警", "将来", "预防", "会怎样", "会不会", "接下来", "之后会"]
ANOMALY_PATTERNS = [
    "异常", "故障", "卡", "掉线", "拥塞", "弱", "覆盖问题", "信号差", "网络差",
    "质量差", "体验差", "不好", "不稳定", "分析问题", "分析原因", "有问题",
]

# ===== 结构化 JSON Prompt（cell-free build_unified_prompt 借鉴：强制固定 JSON 输出，一次调用完成分类+抽参）=====
SYSTEM_PROMPT = """你是无线网络监测平台的意图解析器。用户输入一句关于网络监测的自然语言，请判断意图并提取参数，只输出一个 JSON，不要输出任何其他文字、注释或解释：
{"action": "query_kpi | fault_diagnosis | get_heatmap | get_topology | forecast | user_experience | chat", "scenario": "校园|商场|体育馆", "params": {...}, "confidence": 0-1}
- query_kpi：查KPI数据。params 含 metric(rsrp/sinr/throughput)、time_range(如"最近1小时"、"12:34到12:50")
- fault_diagnosis：故障研判。用户询问网络体验/质量/好坏/是否正常/分析原因/拥塞/信号差等问题时，优先选本意图
- user_experience：具体用户的体验或吞吐优化。params 必须提取 user_id，若指定 AP 则同时提取 ap_id
- get_heatmap：热力图。params 含 metric(默认rsrp)
- get_topology：网络拓扑
- forecast：未来预测/预警。用户问"未来/预测/接下来/会怎样/会不会出问题"时选本意图
- chat：闲聊/无法判断。params 含 reply(你的简短回答)
规则：
1. scenario 必须从用户输入提取，没提到就用空字符串""
2. 用户提到具体时间或时间段（如"12:34到12:50"、"最近1小时"、"昨天14点"、"01-02 09:30到11:30"）时，必须提取到 params.time_range；用户没说时间就不写，不要编造
3. 用户明确提到 AP 或 UE 编号时必须提取 params.ap_id / params.user_id，不得更换对象
4. 输出必须是合法 JSON，不要带 Markdown 代码块标记"""


def build_unified_prompt(message: str) -> str:
    """组装发给 LLM 的用户消息（携带场景上下文，让 LLM 结构化输出更稳定）。"""
    return f"用户输入：{message}\n请按系统要求输出固定 JSON。"


def _post_chat(messages, temperature):
    """统一使用本轮剩余预算；失败交给既有规则/工具降级链路。"""
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY 未配置")
    timeout = network_timeouts()
    try:
        resp = requests.post(LLM_URL,
            json={"model": LLM_MODEL, "messages": messages, "temperature": temperature},
            headers={"Authorization": f"Bearer {LLM_API_KEY}"}, timeout=timeout)
        try:
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        finally:
            resp.close()
    except requests.RequestException:
        transport_failed()
        raise


def call_llm(message: str) -> str:
    """意图解析超时后由规则识别接管，不阻断图表数据。"""
    return _post_chat([{"role": "system", "content": SYSTEM_PROMPT},
                       {"role": "user", "content": message}], 0)


def call_llm_chat(system_prompt: str, user_message: str, temperature: float = 0.2) -> str:
    """正文与说明共享同一次分析的预算，失败回退已有工具文案。"""
    return _post_chat([{"role": "system", "content": system_prompt},
                       {"role": "user", "content": user_message}], temperature).strip()


def parse_unified_response(text: str) -> dict | None:
    """升级3：容错 JSON 解析（cell-free parse_unified_response 借鉴）。
    处理三种脏输出：①前后带废话/注释；②中文冒号/逗号；③尾逗号。
    失败返回 None（不抛异常），由上层决定降级。"""
    if not text:
        return None
    # 1) 整体解析
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # 2) 正则抓 JSON 块（花括号配平，避免截断嵌套结构）
    candidates = []
    for m in re.finditer(r"\{", text):
        depth = 0
        for i in range(m.start(), len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[m.start():i + 1])
                    break
    for raw in candidates:
        cleaned = raw.replace("，", ",").replace("：", ":")
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)  # 去尾逗号
        cleaned = re.sub(r"[\x00-\x1f]", "", cleaned)      # 去控制字符
        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def extract_time_range(message: str) -> dict | None:
    """纯规则时间提取（不依赖 LLM，LLM 挂了/没提取时兜底）。
    返回 {"start": 表达式, "end": 表达式}；相对时间带 relative=True；单点带 single=True。
    表达式交给 main.parse_time_range 解析为具体时间戳（含防未来校验）。"""
    if not message:
        return None
    # ① 带日期区间："01-02 09:30 到 11:30" / "1月2日09:30分-11:30分"
    m = re.search(
        r"(\d{1,2})[-/月](\d{1,2})(?:日)?\s*(\d{1,2}):(\d{2})分?\s*(?:到|至|~|—|－|–|−|-)\s*(\d{1,2}):(\d{2})分?",
        message,
    )
    if m:
        return {
            "start": f"{m.group(1)}-{m.group(2)} {m.group(3)}:{m.group(4)}",
            "end": f"{m.group(1)}-{m.group(2)} {m.group(5)}:{m.group(6)}",
        }
    # ② 纯时分区间："12:34到12:50" / "12:34分到12:50分" / "12:34-12:50"
    m = re.search(r"(\d{1,2}):(\d{2})分?\s*(?:到|至|~|—|－|–|−|-)\s*(\d{1,2}):(\d{2})分?", message)
    if m:
        return {"start": f"{m.group(1)}:{m.group(2)}", "end": f"{m.group(3)}:{m.group(4)}"}
    # ③ 钟点区间："14点到15点"
    m = re.search(r"(\d{1,2})点(?:钟)?\s*(?:到|至|~|—|－|–|−|-)\s*(\d{1,2})点(?:钟)?", message)
    if m:
        return {"start": f"{m.group(1)}:00", "end": f"{m.group(2)}:59"}
    # ④ 相对时间："最近1小时" / "过去30分钟" / "最近半小时"
    m = re.search(r"(?:最近|过去|前)?\s*(\d+(?:\.\d+)?)\s*(小时|分钟|半小时|h|min)", message)
    if m:
        unit = m.group(2)
        if unit == "半小时":
            return {"start": "-0.5h", "end": "now", "relative": True}
        n = float(m.group(1))
        return {"start": f"-{n}h" if unit in ("小时", "h") else f"-{n}m",
                "end": "now", "relative": True}
    m = re.search(r"(?:最近|过去)\s*(半小时)", message)
    if m:
        return {"start": "-0.5h", "end": "now", "relative": True}
    # ⑤ 单点："12:34"（取该分钟窗口）
    m = re.search(r"(\d{1,2}):(\d{2})", message)
    if m:
        return {"start": f"{m.group(1)}:{m.group(2)}",
                "end": f"{m.group(1)}:{m.group(2)}", "single": True}
    return None


def rule_intent(message: str) -> dict:
    """关键词规则版（fallback：LLM 挂了/超时/输出乱时用，保住核心链路可用）
    第5周升级：黑白名单分层 —— 先排除"为什么/解释"类提问，再按优先级匹配工具意图，
    解决"为什么信号差"被"差"误判成故障诊断的问题。
    返回 hit 标记：真正命中某类意图为 True；未命中任何关键词兜底 query_kpi 为 False，
    供上层判断是否值得再调 LLM 增强。"""
    # ① 提取场景（与 CSV 场景一致），用户没提场景则为空 → 上层回退默认
    scenario = next((s for s in ("校园", "商场", "体育馆") if s in message), None)
    params = {"scenario": scenario} if scenario else {}

    # ①b 提取时间（用户说"12:34到12:50"等 → 交给上层解析成查询窗口）
    tr = extract_time_range(message)
    if tr:
        params["time_range"] = tr

    # ② 白名单（明确指令词优先：热力图 > 拓扑 > 预测 > 研判）
    #    必须先于④的因果分支判断 —— 否则"为什么未来两小时会拥塞""为什么热力图这块信号弱"
    #    会被因果分支抢先判成故障研判，返回的是过去 24 小时的拥塞摘要（复审 P2）。
    if any(k in message for k in HEATMAP_PATTERNS):
        return {"intent": "get_heatmap", "params": params, "hit": True}
    if any(k in message for k in TOPOLOGY_PATTERNS):
        return {"intent": "get_topology", "params": params, "hit": True}
    if any(k in message for k in FORECAST_PATTERNS):
        return {"intent": "forecast", "params": params, "hit": True}

    # ③ 因果问句但**确实在问网络故障** → 走故障研判（核查清单第 3 条）：
    # "为什么信号差""这个 AP 为什么掉线"必须能看到候选原因、证据与方案，
    # 不能被黑名单打成 query_kpi。纯概念解释（"什么是 SINR""解释一下切换"）仍走黑名单。
    _concept_only = any(k in message for k in CONCEPT_PATTERNS)
    _net_context = any(k in message for k in NETWORK_CONTEXT)
    _causal_net = (any(k in message for k in CAUSAL_HINTS_STRONG)
                   or (_net_context and any(k in message for k in CAUSAL_HINTS_WEAK)))
    if any(k in message for k in BLACKLIST_PATTERNS):
        if not _concept_only and _causal_net:
            return {"intent": "fault_diagnosis", "params": params, "hit": True,
                    "reason": "因果问句命中网络故障词"}
        return {"intent": "query_kpi", "params": params, "hit": False}

    # ③-2 纯概念解释（"什么是弱覆盖""解释一下 SINR"）即使含故障词也不要进研判：
    #     否则会把"问定义"答成"你这个网络有故障"（复审 P3）。
    if _concept_only:
        return {"intent": "query_kpi", "params": params, "hit": False}

    # ③b "分析/排查/诊断/研判"这类动作词 → 走研判（要的是结论+方案，不是指标罗列）。
    # 但若同时出现明确的指标词（RSRP/SINR/吞吐/指标/数据…），说明用户要的是数值，
    # 仍按 KPI 查询处理，避免把"分析一下各 AP 的 RSRP"也变成研判。
    _DIAG_VERBS = ("分析", "排查", "诊断", "研判")
    _METRIC_WORDS = ("rsrp", "sinr", "吞吐", "throughput", "prb", "指标", "数据", "趋势", "统计")
    if any(v in message for v in _DIAG_VERBS) and not any(w in message.lower() for w in _METRIC_WORDS):
        return {"intent": "fault_diagnosis", "params": params, "hit": True}
    if any(k in message for k in ANOMALY_PATTERNS):
        return {"intent": "fault_diagnosis", "params": params, "hit": True}
    return {"intent": "query_kpi", "params": params, "hit": False}


def get_intent(message: str) -> dict:
    """总入口：优先让大模型识别，失败/不可解析时降级到规则版。

    返回结构：{intent, params, hit, source, confidence?, degrade_reason?}
    **source 必须由这里给出实际来源**（核查清单第 4 条）：此前外层只要看到
    返回里有 intent 就标 "llm"，而本函数异常时返回的是 rule_intent，
    于是"规则兜底"被标成了"大模型识别"。
    """
    try:
        raw = call_llm(build_unified_prompt(message))
        obj = parse_unified_response(raw)
        # action 必须是**已知工具名字符串**：模型偶尔返回 ["query_kpi"] 这类列表，
        # 之前会当成成功（source=llm），到 main 里 `intent not in TOOLS` 抛
        # TypeError → 接口 500（复审 P3）。类型/取值不对一律按"不可解析"降级。
        action = obj.get("action") if isinstance(obj, dict) else None
        if isinstance(action, str) and action in TOOL_NAMES:
            return {
                "intent": action,
                "params": obj.get("params", {}) if isinstance(obj.get("params", {}), dict) else {},
                "hit": False,
                "scenario": obj.get("scenario", ""),
                "confidence": obj.get("confidence", 0),
                "source": "llm",
            }
        reason = "模型输出的 action 不可用" if action is not None else "模型输出无法解析"
    except Exception as exc:  # noqa: BLE001 - 降级是设计行为，只记录原因
        reason = f"模型调用失败：{type(exc).__name__}"
    result = rule_intent(message)
    result["source"] = "rule"
    result["degrade_reason"] = reason
    return result
