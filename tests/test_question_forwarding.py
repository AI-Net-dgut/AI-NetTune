"""第一步回归：用户原问题必须到达回答生成，缓存不能串用不同问题。

使用真实聊天分流、诊断和 answer_llm 校验，捕获回答生成入口，替换外部模型调用；未确诊专项按第三步规则直接兜底。
运行：python tests/run_tests.py question_forwarding
（与原测试相同，需要本地已有网络/频谱 CSV；无需模型密钥。）
"""
from contextlib import contextmanager
from unittest.mock import patch
import json
import main

SCENARIO = "校园"
START, END = "2026-01-01 10:00:00", "2026-01-01 11:59:59"
QUESTIONS = (
    "分析当前网络异常及原因，给出处理方案并比较仿真预计效果",
    "分析哪个 AP 的异常最需要处理，说明原因，给出方案并比较预计效果",
    "分析弱覆盖异常的区域及原因，提出方案并比较预计覆盖效果",
    "分析 AP 拥塞异常及原因，提出方案并比较预计负载变化",
    "分析用户连接分配异常及原因，提出调整方案并比较预计用户体验",
)


@contextmanager
def _offline_answer_model():
    calls = []
    original_generate = main.answer_llm.generate
    def capture_generate(question, evidence, call_fn):
        calls.append((question, evidence))
        return original_generate(question, evidence, call_fn)

    def model(_system, user, _temperature=0.7):
        if not user.startswith("用户问题："):
            return ""  # 其他方案/预测润色继续走原有模板，不发外部请求。
        question = user.split("\n证据（JSON", 1)[0].removeprefix("用户问题：")
        bundle = json.loads(user.split("）：\n", 1)[1].split("\n上一次回答", 1)[0])
        if bundle.get('answer_focus'):
            sections=bundle['answer_focus']['sections']
            return '\n'.join(f'【{title}】{sections[index]["text"]}' for title,index in [('结论',0),('依据',1),('方案',3)])
        # 旧入口保持通用结构；本测试只核对传参与缓存。
        return "【结论】本次网络状态需要结合证据核查。【依据】以给出的真实指标为准。【方案】先核查候选原因。"

    with patch.object(main, "ENABLE_LLM", True), \
         patch.object(main, "_ANOMALY_CACHE_ON", True), \
         patch.dict(main._ANOMALY_CACHE, {}, clear=True), \
         patch.dict(main._SESSIONS, {}, clear=True), \
         patch.object(main.answer_llm, "generate", side_effect=capture_generate), \
         patch.object(main.llm_agent, "call_llm_chat", side_effect=model), \
         patch("requests.sessions.Session.request", side_effect=AssertionError("本测试禁止外部请求")):
        yield calls


def _chat(question, session="question-forwarding"):
    return main.agent_chat(main.ChatRequest(
        message=question, scenario=SCENARIO, session_id=session,
        window_start=START, window_end=END, current_time=END,
    ))["data"]


def test_question_forwarding_chat_presets_and_cache():
    """不同问题必须分别到达模型；重复同一问题可以命中缓存。"""
    with _offline_answer_model() as calls:
        for question in QUESTIONS:
            result = _chat(question)
            assert result["intent"] == "fault_diagnosis"
            detail=result['detail']
            confirmed=(detail.get('answer_focus') or {}).get('status') == 'confirmed'
            assert detail['answer_check']['ok'] == confirmed, detail['answer_check']
            if not confirmed:
                assert not detail['answer_llm'] and detail['answer_check']['attempts']==0
            assert result["query_ref"]["start_time"] == START
            assert result["query_ref"]["end_time"] == END
        assert [question for question, _ in calls] == list(QUESTIONS), \
            f"原问题丢失或被缓存覆盖：{[question for question, _ in calls]}"
        _chat(QUESTIONS[0])
        assert len(calls) == len(QUESTIONS), "相同问题、相同窗口应继续使用缓存"


def test_question_forwarding_ap_scope_is_preserved():
    question = "分析 AP-02 弱覆盖的原因"
    with _offline_answer_model() as calls:
        result = _chat(question)
        assert calls and calls[0][0] == question, "指定 AP 的原问题未到达回答生成"
        assert calls[0][1]["ap"] == "AP-02"
        assert result["query_ref"]["ap_id"] == "AP-02"
        assert result["query_ref"]["end_time"] == END


def test_question_forwarding_compound_request():
    question = "哪个 AP 最差，未来 3 小时会怎样"
    with _offline_answer_model() as calls:
        result = _chat(question)
        assert result["kind"] == "chain"
        assert calls and calls[0][0] == question, "复合问题在诊断子步骤中丢失"
        assert result["horizon_minutes"] == 180
        assert calls[0][1]["window"] == {"start": START, "end": END}


def test_question_forwarding_legacy_api_and_tool_still_work():
    with _offline_answer_model() as calls:
        result = main.analyze_anomaly(main.AnomalyRequest(
            scenario=SCENARIO, window_start=START, window_end=END))
        assert result["code"] == 0
        assert calls[0][0] == f"请解读 {SCENARIO} 在 {START} 至 {END} 的研判结论"
        # 旧函数调用不需要增加位置参数，且继续命中同一无问题缓存。
        legacy = main.tool_fault_diagnosis(SCENARIO, START, END)
        assert legacy["intent"] == "fault_diagnosis"
        assert len(calls) == 1


def test_question_forwarding_direct_api_accepts_optional_message():
    question = "只解释这段时间的原因与反证"
    with _offline_answer_model() as calls:
        result = main.analyze_anomaly(main.AnomalyRequest(
            scenario=SCENARIO, window_start=START, window_end=END, message=question))
        assert result["code"] == 0
        assert calls and calls[0][0] == question, "可选 message 被请求模型丢弃"


def test_question_forwarding_cache_separates_model_mode():
    """规则缓存不能遮住刚开启的模型回答；仍保留原配置版本的缓存隔离。"""
    with _offline_answer_model() as calls:
        with patch.object(main, "ENABLE_LLM", False):
            _chat(QUESTIONS[0])
        result = _chat(QUESTIONS[0])
        assert calls and calls[0][0] == QUESTIONS[0]
        assert result["detail"]["answer_check"]["ok"]
