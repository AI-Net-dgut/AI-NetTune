# llm_agent.py —— LLM 意图解析 + 工具编排（第2周：程思源；第5周升级：黑白名单分层正则 + 结构化JSON Prompt + 容错解析）
# 职责：听懂用户自然语言 → 输出四分类意图 JSON → 交给 main.py 的确定性接口执行
# 降级：LLM 不可用/超时/输出乱时，自动退回关键词规则版，保证演示不掉链子
import json
import os
import re
import requests

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

# 白名单：按优先级分层匹配工具意图（明确的指令词优先，避免宽泛词抢意图）
HEATMAP_PATTERNS = ["热力", "信号分布", "覆盖图", "覆盖热", "地图"]
TOPOLOGY_PATTERNS = ["拓扑", "基站", "连接关系", "连接情况"]
ANOMALY_PATTERNS = [
    "异常", "故障", "卡", "掉线", "拥塞", "弱", "覆盖问题", "信号差", "网络差",
    "质量差", "体验差", "不好", "不稳定", "分析问题", "分析原因", "有问题",
]

# ===== 结构化 JSON Prompt（cell-free build_unified_prompt 借鉴：强制固定 JSON 输出，一次调用完成分类+抽参）=====
SYSTEM_PROMPT = """你是无线网络监测平台的意图解析器。用户输入一句关于网络监测的自然语言，请判断意图并提取参数，只输出一个 JSON，不要输出任何其他文字、注释或解释：
{"action": "query_kpi | fault_diagnosis | get_heatmap | get_topology | chat", "scenario": "校园|商场|体育馆", "params": {...}, "confidence": 0-1}
- query_kpi：查KPI数据。params 含 metric(rsrp/sinr/throughput)、time_range(如"最近1小时")
- fault_diagnosis：故障研判。用户询问网络体验/质量/好坏/是否正常/分析原因/拥塞/信号差等问题时，优先选本意图
- get_heatmap：热力图。params 含 metric(默认rsrp)
- get_topology：网络拓扑
- chat：闲聊/无法判断。params 含 reply(你的简短回答)
规则：
1. scenario 必须从用户输入提取，没提到就用空字符串""
2. 不要编造时间，用户没说时间就不写
3. 输出必须是合法 JSON，不要带 Markdown 代码块标记"""


def build_unified_prompt(message: str) -> str:
    """组装发给 LLM 的用户消息（携带场景上下文，让 LLM 结构化输出更稳定）。"""
    return f"用户输入：{message}\n请按系统要求输出固定 JSON。"


def call_llm(message: str) -> str:
    """调用大模型，返回原始文本。超时/网络错误会抛异常，由上层降级。"""
    resp = requests.post(
        LLM_URL,
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            "temperature": 0,
        },
        headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def call_llm_chat(system_prompt: str, user_message: str, temperature: float = 0.2) -> str:
    """通用对话式 LLM 调用：自定义 system prompt，返回润色后的文本。
    未配置 key / 超时 / 网络错误 / 非 2xx 一律抛异常，由上层降级到模板文案。"""
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY 未配置")
    resp = requests.post(
        LLM_URL,
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
        },
        headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        timeout=45,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


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


def rule_intent(message: str) -> dict:
    """关键词规则版（fallback：LLM 挂了/超时/输出乱时用，保住核心链路可用）
    第5周升级：黑白名单分层 —— 先排除"为什么/解释"类提问，再按优先级匹配工具意图，
    解决"为什么信号差"被"差"误判成故障诊断的问题。
    返回 hit 标记：真正命中某类意图为 True；未命中任何关键词兜底 query_kpi 为 False，
    供上层判断是否值得再调 LLM 增强。"""
    # ① 提取场景（与 CSV 场景一致），用户没提场景则为空 → 上层回退默认
    scenario = next((s for s in ("校园", "商场", "体育馆") if s in message), None)
    params = {"scenario": scenario} if scenario else {}

    # ② 黑名单：问原因/求解释 → 不触发工具白名单（hit=False，留给 LLM 或 KPI 兜底）
    if any(k in message for k in BLACKLIST_PATTERNS):
        return {"intent": "query_kpi", "params": params, "hit": False}

    # ③ 白名单（明确指令词优先：热力图 > 拓扑 > 研判）
    if any(k in message for k in HEATMAP_PATTERNS):
        return {"intent": "get_heatmap", "params": params, "hit": True}
    if any(k in message for k in TOPOLOGY_PATTERNS):
        return {"intent": "get_topology", "params": params, "hit": True}
    if any(k in message for k in ANOMALY_PATTERNS):
        return {"intent": "fault_diagnosis", "params": params, "hit": True}
    return {"intent": "query_kpi", "params": params, "hit": False}


def get_intent(message: str) -> dict:
    """总入口：LLM 优先，任何异常/输出不可解析都降级到规则版（这就是申报书里的 API 降级）。
    返回结构兼容 main.py：{intent, params, hit, scenario?, confidence?}"""
    try:
        raw = call_llm(build_unified_prompt(message))
        obj = parse_unified_response(raw)
        if obj and obj.get("action"):
            return {
                "intent": obj["action"],
                "params": obj.get("params", {}) if isinstance(obj.get("params", {}), dict) else {},
                "hit": False,
                "scenario": obj.get("scenario", ""),
                "confidence": obj.get("confidence", 0),
            }
    except Exception:
        pass
    return rule_intent(message)
