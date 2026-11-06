"""模型慢或断网时，应保留证据图和方案数据；预算按分析隔离。"""
from pathlib import Path
import re
from unittest.mock import patch
import requests
import llm_budget as budget
import llm_agent
import main as m
from tests.asgi_client import post_json


def test_budget_nested_tools_share_remaining_time_and_reset_after_request():
    clock = [100.]
    with patch.object(budget.time, 'monotonic', side_effect=lambda: clock[0]):
        with budget.request_budget(30):
            clock[0] = 128.
            with budget.request_budget(30):
                assert sum(budget.network_timeouts()) <= 2.
            clock[0] = 131.
            try:
                budget.network_timeouts()
                raise AssertionError('预算耗尽不能再发模型请求')
            except requests.Timeout:
                pass
        with budget.request_budget(30):
            assert sum(budget.network_timeouts()) > 2.


def test_transport_failure_blocks_only_current_request_and_releases_response():
    with budget.request_budget():
        budget.transport_failed()
        try:
            budget.network_timeouts()
            raise AssertionError('本轮网络失败后不应再次等待')
        except requests.Timeout:
            pass
    with budget.request_budget():
        assert budget.network_timeouts()[1] > 0
    from unittest.mock import Mock
    response = Mock()
    response.json.return_value = {'choices': [{'message': {'content': '已返回'}}]}
    with patch.object(llm_agent, 'LLM_API_KEY', 'unit-test-only'), patch.object(llm_agent.requests, 'post', return_value=response) as post:
        with budget.request_budget():
            assert llm_agent.call_llm_chat('系统', '用户') == '已返回'
    response.close.assert_called_once()
    assert isinstance(post.call_args.kwargs['timeout'], tuple)


def test_model_timeout_returns_ap_evidence_instead_of_empty_or_foreign_user():
    with patch.object(m, 'ENABLE_LLM', True), patch.object(llm_agent, 'LLM_API_KEY', 'unit-test-only'), \
         patch.object(llm_agent.requests, 'post', side_effect=requests.Timeout('模拟超时')) as network, \
         patch.dict(m._ANOMALY_CACHE, {}, clear=True), patch.dict(m._SESSIONS, {}, clear=True):
        response = post_json(m.app, '/api/agent/chat', {'message': '分析 AP-03 弱覆盖原因和方案',
            'scenario': '校园', 'window_start': '2026-01-02 14:00:00',
            'window_end': '2026-01-02 16:00:00', 'session_id': 'timeout-budget-ap'})
    assert response.status_code == 200
    result = response.json()['data']
    assert result['intent'] == 'fault_diagnosis' and result['query_ref']['ap_id'] == 'AP-03'
    assert result['chart']['type'] == 'line' and result['detail']['hourly_trend']
    assert not result['detail'].get('subject') and result['plans']
    assert network.call_count == 1, '后续解释与正文不能重复访问已超时的模型'


def test_model_timeout_keeps_user_trend_series_and_fixed_goal():
    with patch.object(m, 'ENABLE_LLM', True), patch.object(llm_agent, 'LLM_API_KEY', 'unit-test-only'), \
         patch.object(llm_agent.requests, 'post', side_effect=requests.Timeout('模拟超时')) as network, \
         patch.dict(m._SESSIONS, {}, clear=True):
        response = post_json(m.app, '/api/agent/chat', {'message': '提高 UE-03 的吞吐量10%',
            'scenario': '校园', 'window_start': '2026-01-02 14:00:00',
            'window_end': '2026-01-02 14:59:00', 'session_id': 'timeout-budget-user'})
    result = response.json()['data']
    assert result['intent'] == 'user_experience'
    assert result['detail']['subject']['id'] == 'UE-03'
    assert len(result['detail']['series']) == 60 and result['detail']['goal']['target'] > 0
    assert network.call_count == 1


def test_release_marker_matches_backend_and_built_frontend():
    data = m.health()['data']
    root = Path(__file__).resolve().parents[1]
    marker = re.search(r'<meta name="nettune-release" content="([^"]+)"', (root/'frontend/index.html').read_text()).group(1)
    assert marker == data['release'] == 'evidence-v81-20261106'
    assert all(data['features'].values())
