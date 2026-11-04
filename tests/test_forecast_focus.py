"""第二步：预测追问重点不同，但基准、预警和证据不能串用。"""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch
import main

BASE = '2026-01-02 14:12:00'
HISTORY = {'start': '2026-01-02 13:12:00', 'end': BASE}
FUTURE = {'start': BASE, 'end': '2026-01-02 16:12:00'}
QUESTIONS = {
    'cause': '针对所选预测风险，解释可能原因与判断限制',
    'evidence': '所选预测风险有哪些支持证据和反面疑点',
    'impact': '所选预测风险可能影响哪些AP或用户，预计何时发生',
    'prevention': '针对所选预测风险，给出可采取的预防方案和适用条件',
    'comparison': '对比所选预测风险的预防方案，说明预期效果与代价',
    'uncertainty': '所选预测风险有哪些不确定性，还需要补充哪些证据',
}
ALERT = {'id': 'ALT-AP-03-weak', 'target': 'AP-03', 'title': 'AP-03 预计弱覆盖',
         'summary': '预计功率持续低于门槛，需核查。', 'severity': 'high',
         'start': '2026-01-02 14:33:00', 'end': '2026-01-02 15:30:00',
         'spectrumId': 'AP-03-power', 'bandIds': [], 'impactId': None}


@contextmanager
def _round(alerts=None, model=None):
    cached = {'id': 'FC-focus-test', 'scenario': '校园', 'baseTime': BASE,
              'history': HISTORY, 'forecast': FUTURE, 'status': 'ready',
              'alerts': deepcopy([ALERT] if alerts is None else alerts),
              'method': '阻尼趋势外推', 'limitations': ['未提供回测准确率']}
    rows = []
    for minute in range(12, 60):
        for ap, rsrp, sinr in [('AP-03', -110., -9.), ('AP-01', -70., 20.)]:
            rows.append({'timestamp': f'2026-01-02 13:{minute:02d}:00', 'scenario': '校园',
                         'ap_id': ap, 'user_id': 'UE-'+ap[-2:], 'rsrp': rsrp, 'sinr': sinr,
                         'throughput': 3., 'prb_usage': 45., 'latency': 20., 'packet_loss': 1.,
                         'traffic_demand': 8., 'retransmission_count': 0, 'active_users': 1,
                         'network_status': 'normal'})
    with patch.dict(main._FORECAST_CACHE, {'FC-focus-test': cached}, clear=True), \
         patch.dict(main._SPEC, {'df': None}, clear=True), \
         patch.dict(main._PLAN_CACHE, {}, clear=True), \
         patch.object(main, 'filter_rows', return_value=rows), \
         patch.object(main, 'ENABLE_LLM', model is not None), \
         patch.object(main.llm_agent, 'LLM_API_KEY', 'test-only' if model else ''), \
         patch.object(main, 'call_llm_chat', side_effect=model or AssertionError('不应调用模型')), \
         patch('requests.sessions.Session.request', side_effect=AssertionError('禁止外部请求')):
        yield cached


def _ask(question, alert=ALERT['id'], target=None):
    return main.forecast_analyze(main.ForecastAnalyzeRequest(
        forecastId='FC-focus-test', scenario='校园', baseTime=BASE, alertId=alert,
        history=main._TimeRange(**HISTORY), forecast=main._TimeRange(**FUTURE),
        question=question, targetWindow=main._TimeRange(**target) if target else None))


def test_forecast_focus_rule_answers_have_different_substance():
    with _round():
        results = {focus: _ask(question) for focus, question in QUESTIONS.items()}
    for focus, result in results.items():
        assert result['code'] == 0, result
        data = result['data']
        assert data.get('answerFocus') == focus, (focus, data.get('answerFocus'))
        assert data['source'] == 'rule'
        assert data['forecastId'] == 'FC-focus-test' and data['alertId'] == ALERT['id']
        assert data['baseTime'] == BASE
        assert 'AP-03' in data['reply']
    # 除去问题回显，各类回答正文仍须不同，而不只是换一下标题或问题。
    bodies = [r['data']['reply'].split('\n\n', 1)[-1] for r in results.values()]
    assert len(set(bodies)) == 6
    assert '候选' in results['cause']['data']['reply']
    assert 'dBm' in results['evidence']['data']['reply']
    assert '受影响' in results['impact']['data']['reply']
    assert '适用' in results['prevention']['data']['reply']
    assert '权衡' in results['comparison']['data']['reply']
    assert '准确率' in results['uncertainty']['data']['reply']


def test_forecast_focus_model_receives_task_and_validated_context():
    captured = []
    def model(system, user, **_kwargs):
        captured.append((system, user))
        return '【依据】先核对给定证据，原因仍待验证。'
    with _round(model=model):
        for focus, question in QUESTIONS.items():
            result = _ask(question)['data']
            assert result.get('answerFocus') == focus
            assert result['source'] == 'llm'
    assert len(captured) == 6
    assert len({system for system, _ in captured}) == 6, '模型提示不能还是固定的六段全集'
    for (_, user), question in zip(captured, QUESTIONS.values()):
        assert f'用户的问题：{question}' in user
        assert BASE in user and 'AP-03' in user and HISTORY['start'] in user


def test_forecast_focus_selected_ap_metrics_are_not_network_means():
    with _round():
        reply = _ask(QUESTIONS['evidence'])['data']['reply']
        assert 'RSRP -110.0 dBm' in reply, reply
        assert 'RSRP -90.0 dBm' not in reply, '全网均值不能冒充所选AP指标'
        assert 'SINR -9.0 dB' in reply


def test_forecast_focus_comparison_reuses_forecast_plans():
    with _round():
        reference = main.forecast_plans(main.ForecastPlanRequest(
            forecastId='FC-focus-test', scenario='校园', baseTime=BASE, alertId=ALERT['id'],
            history=main._TimeRange(**HISTORY), forecast=main._TimeRange(**FUTURE)))['data']
        reply = _ask(QUESTIONS['comparison'])['data']['reply']
        for plan in reference['plans']:
            assert plan['name'] in reply
        assert '历史' in reply and '未来实测' in reply
        assert '权衡' in reply


def test_forecast_focus_no_risk_in_target_does_not_borrow_outside_risk():
    target = {'start': '2026-01-02 14:15:00', 'end': '2026-01-02 14:20:00'}
    with _round():
        for question in [QUESTIONS['cause'], QUESTIONS['comparison'], QUESTIONS['prevention']]:
            result = _ask(question, alert=None, target=target)
            assert result['code'] == 0
            data = result['data']
            assert '未命中越阈风险' in data['reply'], data['reply']
            assert '下调天线下倾角' not in data['reply']
            assert data['targetWindow'] == target
        assert not main._PLAN_CACHE, '无风险的关注时段不应生成处置方案'


def test_forecast_focus_missing_plans_do_not_invent_effects():
    with _round(), patch.object(main, 'forecast_plans', return_value={
            'code': 40002, 'message': '基准前没有有效参数基线'}):
        reply = _ask(QUESTIONS['comparison'])['data']['reply']
        assert '无法' in reply or '不能' in reply
        assert '基准前没有有效参数基线' in reply
        assert '下调天线下倾角' not in reply
        assert '提升3' not in reply and '改善30%' not in reply


def test_forecast_focus_model_failure_falls_back_to_same_focus():
    def failing(*_args, **_kwargs):
        raise RuntimeError('模拟模型不可用')
    with _round(model=failing):
        result = _ask(QUESTIONS['impact'])['data']
        assert result.get('answerFocus') == 'impact'
        assert result['source'] == 'rule'
        assert '受影响' in result['reply']


def test_forecast_focus_validation_still_precedes_model_and_plan_generation():
    def model(*_args, **_kwargs):
        raise AssertionError('无效上下文不得调用模型')
    with _round(model=model), patch.object(main, 'forecast_plans', side_effect=AssertionError('无效上下文不得出方案')):
        assert _ask(QUESTIONS['comparison'], alert='UNKNOWN')['code'] == 40002
        assert _ask(QUESTIONS['comparison'], target={'start': BASE, 'end': '2026-01-03 10:00:00'})['code'] == 40003
        del main._FORECAST_CACHE['FC-focus-test']
        assert _ask(QUESTIONS['comparison'])['code'] == 40003


def test_forecast_focus_unique_risk_uses_available_spectrum_without_alert_id():
    import pandas as pd
    frame = pd.DataFrame([{'timestamp': BASE, 'power_dbm': -100. if i < 49 else -80.,
                           'occupancy_pct': 90. if i < 7 else 20., 'band_id': f'band-{i}'}
                          for i in range(50)])
    with _round(), patch.dict(main._SPEC, {'df': frame}), patch.object(main, 'spectrum_window', return_value=frame):
        for alert in (ALERT['id'], None):
            reply = _ask(QUESTIONS['evidence'], alert=alert)['data']['reply']
            assert '49/50' in reply and '7/50' in reply, reply
            assert '尚无所选对象的频谱逐频段证据' not in reply
            assert '未取得所选对象的完整频谱证据' not in reply


def test_forecast_focus_model_target_without_risk_has_no_outside_alert():
    import json
    captured = []
    def model(_system, user, **_kwargs):
        captured.append(json.loads(user.split('：\n', 1)[-1]))
        return '【结论】关注时段未命中风险，不能据此排除其他未观测因素。'
    target = {'start': '2026-01-02 14:15:00', 'end': '2026-01-02 14:20:00'}
    with _round(model=model):
        response = _ask(QUESTIONS['cause'], alert=None, target=target)
        assert response['code'] == 0
    assert captured and not captured[0]['alerts']
    assert captured[0]['selectedAlert'] is None
    assert captured[0]['targetWindow'] == target
