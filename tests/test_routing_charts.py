"""路由与图表回归：模型虚构对象、会话切换、用户/AP 同窗数据。"""
from unittest.mock import patch
import json
import main as m
import user_experience as ue

START, END = '2026-01-02 14:00:00', '2026-01-02 14:59:00'


def chat(message, intent, params, previous=None):
    session = 'routing-chart-regression'
    m._SESSIONS.pop(session, None)
    if previous:
        m._remember_turn(session, previous)
    calls = []
    def tool(name):
        def run(*args, **kwargs):
            calls.append((name, args, kwargs))
            return {'reply': '未找到有效数据', 'status': 'no_data', 'intent': name}
        return run
    try:
        with patch.object(m, 'ENABLE_LLM', True), patch.object(m, 'get_intent', return_value={
                'source': 'llm', 'intent': intent, 'params': params}), \
             patch.object(m, '_plan_chain', return_value=None), \
             patch.object(m, 'tool_user_experience', side_effect=tool('user_experience')), \
             patch.dict(m.TOOLS, {name: tool(name) for name in ('query_kpi', 'fault_diagnosis', 'get_heatmap', 'get_topology')}):
            response = m.agent_chat(m.ChatRequest(message=message, scenario='校园', window_start=START,
                                                  window_end=END, session_id=session))
        return response, calls
    finally:
        m._SESSIONS.pop(session, None)


def test_routing_ap_question_cannot_be_hijacked_by_hallucinated_user():
    response, calls = chat('分析 AP-03 弱覆盖原因和方案', 'user_experience', {'user_id': 'UE-01'})
    data = response['data']
    assert calls[0][0] == 'fault_diagnosis'
    assert data['intent_debug']['parsed']['ap_id'] == 'AP-03'
    assert not data['intent_debug']['parsed'].get('user_id')
    assert '忽略' in data['intent_debug']['degrade_reason']


def test_routing_user_question_cannot_gain_hallucinated_ap_constraint():
    response, calls = chat('分析 UE3 体验', 'user_experience', {'user_id': 'UE-3', 'ap_id': 'AP-01'})
    parsed = response['data']['intent_debug']['parsed']
    assert parsed['user_id'] == 'UE-03' and not parsed.get('ap_id')
    assert calls[0][2]['ap_id'] is None


def test_routing_explicit_ap_user_and_normalized_ids_are_preserved():
    response, calls = chat('分析 AP-1 下 ue3 体验', 'user_experience', {'user_id': 'UE-99', 'ap_id': 'AP-99'})
    parsed = response['data']['intent_debug']['parsed']
    assert parsed['user_id'] == 'UE-03' and parsed['ap_id'] == 'AP-01'
    assert calls[0][2]['ap_id'] == 'AP-01'


def test_routing_followup_inherits_verified_objects_but_explicit_ap_starts_new_subject():
    previous = {'user_id': 'UE-03', 'ap_id': 'AP-01', 'scenario': '校园', 'intent': 'user_experience'}
    response, calls = chat('继续分析它', 'user_experience', {'user_id': 'UE-99'}, previous)
    parsed = response['data']['intent_debug']['parsed']
    assert parsed['user_id'] == 'UE-03' and parsed['ap_id'] == 'AP-01'
    assert calls[0][0] == 'user_experience'
    response, calls = chat('再分析 AP-03 弱覆盖', 'user_experience', {'user_id': 'UE-03'}, previous)
    assert calls[0][0] == 'fault_diagnosis'
    assert not response['data']['intent_debug']['parsed'].get('user_id')


def test_routing_whole_network_followup_does_not_inherit_user():
    response, calls = chat('再分析全网异常', 'user_experience', {'user_id': 'UE-03'},
                           {'user_id': 'UE-03', 'ap_id': 'AP-01'})
    parsed = response['data']['intent_debug']['parsed']
    assert not parsed.get('user_id') and not parsed.get('ap_id')
    assert calls[0][0] == 'fault_diagnosis'


def test_routing_visualization_task_is_preserved_and_multiple_subjects_rejected():
    response, calls = chat('查看 AP-03 拓扑连接关系', 'get_topology', {'ap_id': 'AP-3', 'user_id': 'UE-01'})
    assert calls[0][0] == 'get_topology'
    assert not response['data']['intent_debug']['parsed'].get('user_id')
    response, calls = chat('分析 UE-01 和 UE-03 体验', 'user_experience', {'user_id': 'UE-01'})
    assert response['code'] == 40002 and not calls


def test_user_chart_series_uses_only_filtered_ap_and_keeps_missing_values():
    data = [{'timestamp': f'2026-01-02 14:{i:02d}:00', 'user_id': 'UE-03',
             'ap_id': 'AP-01' if i < 10 else 'AP-03', 'rsrp': -90, 'sinr': 15,
             'latency': 20, 'throughput': None if i == 9 else 50} for i in range(20)]
    def select(scenario, start, end, ap_id=None, user_id=None, **kwargs):
        return [r for r in data if (not ap_id or r['ap_id'] == ap_id) and (not user_id or r['user_id'] == user_id)]
    with patch.object(m, 'filter_rows', side_effect=select):
        result = m.tool_user_experience('校园', START, END, 'UE-03', ap_id='AP-01')
    points = result['detail']['series']
    assert len(points) == 10 and all(p['apId'] == 'AP-01' for p in points)
    assert points[-1]['throughput'] is None and points[0]['time'].endswith('+08:00')
    json.dumps(result, allow_nan=False)


def test_user_chart_series_sorts_timestamps_and_never_converts_missing_to_zero():
    result = ue.chart_series([{'timestamp': END, 'rsrp': float('inf')},
                              {'timestamp': START, 'throughput': float('nan')}, {'timestamp': 'invalid'}])
    assert len(result) == 2 and result[0]['time'] < result[1]['time']
    assert result[0]['throughput'] is None and result[1]['rsrp'] is None
    json.dumps(result, allow_nan=False)


def test_user_series_reaches_http_json_but_not_answer_model_context():
    from tests.asgi_client import post_json
    data = [{'timestamp': f'2026-01-02 14:{i:02d}:00', 'user_id': 'UE-03', 'ap_id': 'AP-01',
             'rsrp': -90, 'sinr': 15, 'latency': 20, 'throughput': 50} for i in range(60)]
    def select(scenario, start, end, ap_id=None, user_id=None, **kwargs):
        return [r for r in data if (not ap_id or r['ap_id'] == ap_id) and (not user_id or r['user_id'] == user_id)]
    session = 'chart-json-regression'
    try:
        with patch.object(m, 'filter_rows', side_effect=select), patch.object(m, 'ENABLE_LLM', True), \
             patch.object(m, 'get_intent', return_value={'source': 'llm', 'intent': 'user_experience', 'params': {'user_id': 'UE-03'}}), \
             patch.object(m.answer_llm, 'generate', return_value={'check': {'ok':False}, 'text':None}) as generate:
            response = post_json(m.app, '/api/agent/chat', {
                'message': '分析 UE-03 体验', 'scenario': '校园', 'window_start': START,
                'window_end': END, 'session_id': session})
        assert response.status_code == 200
        result = response.json()['data']
        assert len(result['detail']['series']) == 60
        assert 'series' not in generate.call_args.args[1]['detail']
        assert result['detail']['series'][0]['time'].endswith('+08:00')
    finally:
        m._SESSIONS.pop(session, None)
