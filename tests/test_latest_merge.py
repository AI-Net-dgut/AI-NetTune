"""新上传版本的 RAG 改动与路由/图表修复兼容性。"""
import json
from unittest.mock import patch
import answer_llm as al


def test_latest_citation_hint_preserves_parseable_evidence_and_is_available_to_model():
    reference = {'label': '故障定义 §2.2', 'section': '2.2', 'text': '覆盖需结合指标核查'}
    evidence = {'ap': 'AP-02', 'references': [reference], 'root_cause_confirmed': False}
    calls = []
    def model(system, user, temperature):
        calls.append((system, user))
        parsed = json.loads(user.split('）：\n', 1)[1])
        assert parsed == evidence
        assert reference['label'] in system
        return '【结论】AP-02 待核查。【依据】依据：故障定义 §2.2。【方案】现场核查。'
    result = al.generate('分析 AP-02', evidence, model)
    assert result['check']['ok'] and result['text']
    assert len(calls) == 1


def test_latest_citation_retry_cannot_rescue_invented_numeric_measurements():
    evidence = {'ap': 'AP-02', 'metrics': {'rsrp': -110}, 'references': [
        {'label': '故障定义 §2.2', 'section': '2.2', 'text': '结合 RSRP 判定'}]}
    result = al.generate('分析 AP-02', evidence, lambda *_: (
        '【结论】AP-02 待核查。【依据】RSRP 4 dBm。【方案】现场复核。'))
    assert not result['check']['ok'] and result['text'] is None


def test_latest_real_data_routing_keeps_ap_evidence_and_user_series_separate():
    import main as m
    from tests.asgi_client import post_json
    session = 'latest-real-data-routing'
    start, end = '2026-01-02 14:00:00', '2026-01-02 14:59:00'
    def ask(question):
        response = post_json(m.app, '/api/agent/chat', {'message': question, 'scenario': '校园',
            'window_start': start, 'window_end': end, 'session_id': session})
        assert response.status_code == 200
        return response.json()['data']
    try:
        with patch.object(m, 'ENABLE_LLM', True), patch.dict(m._ANOMALY_CACHE, {}, clear=True), \
             patch.object(m, 'get_intent', return_value={'source': 'llm', 'intent': 'user_experience',
                                                       'params': {'user_id': 'UE-01'}}), \
             patch.object(m.answer_llm, 'generate', return_value={'text': None, 'source': None,
                                                                 'check': {'ok': False}, 'attempts': 0}):
            ap = ask('分析 AP-03 弱覆盖原因和处理方案')
            assert ap['intent'] == 'fault_diagnosis'
            assert ap['query_ref']['ap_id'] == 'AP-03'
            assert ap['detail']['hourly_trend'] and ap['chart']['type'] == 'line'
            assert not ap['detail'].get('subject')
            user = ask('分析 UE-03 体验')
            assert user['intent'] == 'user_experience'
            assert user['detail']['subject']['id'] == 'UE-03'
            assert len(user['detail']['series']) == user['detail']['subject']['samples']
            constrained = ask('分析 AP-03 下 UE-03 体验')
            assert constrained['detail']['series']
            assert all(point['apId'] == 'AP-03' for point in constrained['detail']['series'])
            absent = ask('分析 AP-99 下 UE-03 体验')
            assert absent['status'] == 'no_data' and not absent.get('detail', {}).get('series')
    finally:
        m._SESSIONS.pop(session, None)
