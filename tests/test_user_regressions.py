"""独立审查反例回归：验证行为边界，而非只检查新增措辞。"""
from datetime import datetime, timedelta
from unittest.mock import patch
import math
import main as m
import plan_model as pm
import answer_llm as al
import user_experience as ue

START, END = '2026-01-02 14:00:00', '2026-01-02 14:59:00'
SCOPE = {'scenario': '校园', 'start': START, 'end': END}


def rows(count=60, bad_indices=(), **overrides):
    start = datetime.fromisoformat(START)
    result = []
    for i in range(count):
        bad = i in bad_indices
        result.append({'timestamp': (start+timedelta(minutes=i)).isoformat(sep=' '),
                       'user_id': 'UE-03', 'ap_id': 'AP-03', 'rsrp': -120 if bad else -90,
                       'sinr': 0 if bad else 15, 'latency': 100 if bad else 20,
                       'throughput': 50, 'packet_loss': 1, 'prb_usage': 40,
                       'ue_x': 100+i, 'ue_y': 100, **overrides})
    return result


def filtered(all_rows):
    def select(scenario, start, end, ap_id=None, user_id=None, **_kwargs):
        return [r for r in all_rows if (not ap_id or r['ap_id'] == ap_id)
                and (not user_id or r['user_id'] == user_id) and start <= r['timestamp'] <= end]
    return select


def analyze(all_rows, message='分析 UE-03 体验', ap=None):
    with patch.object(m, 'filter_rows', side_effect=filtered(all_rows)):
        return m.tool_user_experience('校园', START, END, 'UE-03', message, ap_id=ap)


def test_user_last_five_bad_minutes_are_not_hidden_by_whole_window_share():
    result = analyze(rows(bad_indices=range(55, 60)))
    a = result['assessment']
    assert a['windowAssessment'] == 'within_policy'
    assert a['endState'] == 'persistent_anomaly'
    assert abs(a['checks'][0]['share'] - 5/60) < 1e-9
    assert '连续异常' in result['reply'] and result['plans']


def test_user_share_boundary_and_recovery_are_separate():
    exact = ue.assess(rows(count=100, bad_indices=range(10)), '2026-01-02 15:39:00')
    assert exact['windowAssessment'] == 'within_policy' and exact['endState'] == 'recovered'
    over = ue.assess(rows(count=100, bad_indices=range(11)), '2026-01-02 15:39:00')
    assert over['windowAssessment'] == 'exceeded' and over['endState'] == 'recovered'


def test_user_half_bad_samples_cannot_pass_on_mean():
    result = analyze(rows(bad_indices=range(30)))
    assert result['assessment']['windowAssessment'] == 'exceeded'
    assert result['assessment']['checks'][0]['share'] == .5


def test_user_missing_metrics_are_not_zero_or_normal():
    for missing in (None, '', float('nan'), float('inf')):
        result = analyze(rows(rsrp=missing, latency=missing))
        assert result['data_status'] == 'insufficient_data'
        assert result['assessment']['checks'][1]['ok'] is None
        assert not result['plans'] and '无法判断' in result['reply']


def test_user_valid_denominators_and_missing_share():
    data = rows(bad_indices=range(5))
    for r in data[5:15]:
        r['sinr'] = None
    a = ue.assess(data, END)
    c = a['checks'][0]
    assert c['validSamples'] == 50 and abs(c['share']-.1) < 1e-9
    for r in data[15:30]:
        r['sinr'] = None
    assert ue.assess(data, END)['dataStatus'] == 'insufficient_data'


def test_user_small_samples_stale_tail_and_gaps_do_not_fake_current_normal():
    assert ue.assess(rows(count=2), '2026-01-02 14:01:00')['dataStatus'] == 'insufficient_data'
    assert ue.assess(rows(count=30), END)['endState'] == 'unknown'
    data = rows(bad_indices=range(55, 60))
    del data[57]
    assert ue.assess(data, END)['endState'] == 'warning'


def test_user_duplicate_minutes_do_not_inflate_persistence():
    data = rows(bad_indices=(59,))
    data += [dict(data[-1]) for _ in range(20)]
    assert ue.assess(data, END)['endState'] == 'warning'


def test_user_healthy_goal_still_has_evaluable_plan_and_pending_not_false():
    result = analyze(rows(), '如何将 UE-03 的吞吐量提高10%')
    assert result['plans'] and result['detail']['goal']['status'] == 'pending'
    assert result['detail']['checks'][0]['ok'] is None
    sim = result['plans'][0]['simulation']
    assert sim['goalAssessment']['baseline'] == 50
    assert sim['goalAssessment']['target'] == 55
    assert sim['goalAssessment']['status'] == 'estimated_unmet'


def test_user_goal_compares_prediction_not_baseline_and_handles_zero():
    assert ue.goal(50, 10, 56)['status'] == 'estimated_met'
    assert ue.goal(50, 10, 54)['gap'] == 1
    assert ue.goal(0, 10, 5)['status'] == 'not_evaluable'
    assert analyze(rows(), '降低 UE-03 的时延10%')['detail']['goal'] is None


def test_user_joint_ap_constraint_filters_instead_of_switching_ap():
    data = rows()
    for row in data[:20]:
        row['ap_id'] = 'AP-01'
    result = analyze(data, '分析 AP-01 下 UE-03 的体验', 'AP-01')
    subject = result['detail']['subject']
    assert subject['samples'] == 20 and subject['servingAps'] == ['AP-01']
    assert not analyze(data, ap='AP-99')['plans']
    assert analyze(data, ap='AP-99')['status'] == 'no_data'


def test_user_plan_reassessment_preserves_user_metrics_goal_and_scope():
    data = rows(bad_indices=range(55, 60))
    initial = analyze(data, '提高 UE-03 的吞吐量10%')
    plan = initial['plans'][0]
    with patch.object(m, 'filter_rows', side_effect=filtered(data)):
        result = m.plan_simulate(m.PlanSimulateRequest(plan_id=plan['id'], scenario='校园',
                                  window_start=START, window_end=END))
    assert result['code'] == 0, result
    sim = result['data']
    assert sim['subject']['id'] == 'UE-03'
    assert sim['metrics'] == plan['simulation']['metrics']
    assert sim['goalAssessment'] == plan['simulation']['goalAssessment']
    assert sim['trend']['objectId'] == 'AP-03' and sim['subject']['trendScope'] == 'ap'
    assert 'UE-03' in sim['summary']


def test_user_metrics_only_change_while_connected_to_execution_ap():
    data = rows()
    for row in data[:30]:
        row['ap_id'] = 'AP-01'
    plan = pm.build_plans('AP-03', '覆盖故障', data[30:], SCOPE, {'tilt': 8, 'power': 33})[0]
    sim = pm.simulate(plan, data[30:], {}, SCOPE, 'AP-03', metric_rows=data,
                      subject={'type': 'user', 'id': 'UE-03'})
    rsrp = next(x for x in sim['metrics'] if x['key'] == 'rsrpDbm')
    assert math.isclose(rsrp['after']-rsrp['before'], 1.8, abs_tol=.01)
    assert sim['trend']['objectId'] == 'AP-03'


def test_user_plan_ids_do_not_collide_and_cache_is_snapshot():
    first = analyze(rows(), '提高 UE-03 的吞吐量10%')['plans'][0]
    second = analyze(rows(user_id='UE-04'), '提高 UE-03 的吞吐量10%')
    assert not second['plans']
    other = analyze(rows(), '提高 UE-03 的吞吐量10%')['plans'][0]
    assert first['id'] != other['id'] and first['templateId'] == other['templateId']
    first['subject']['id'] = 'UE-99'
    assert m._PLAN_CACHE[first['id']]['subject']['id'] == 'UE-03'


def test_user_plan_rejects_changed_window_ap_and_revision():
    plan = analyze(rows(), '提高 UE-03 的吞吐量10%')['plans'][0]
    base = dict(plan_id=plan['id'], scenario='校园', window_start=START, window_end=END)
    for changes in ({'window_end': '2026-01-02 15:00:00'}, {'ap_id': 'AP-01'}, {'scenario': '商场'}):
        assert m.plan_simulate(m.PlanSimulateRequest(**(base | changes)))['code'] == 40002
    with patch.object(m.sim_config, 'revision', return_value=plan['configRevision']+1):
        assert m.plan_simulate(m.PlanSimulateRequest(**base))['code'] == 40002


def test_user_model_cannot_reverse_window_or_omit_active_tail():
    for data in (rows(bad_indices=range(60)), rows(bad_indices=range(55,60))):
        result = analyze(data)
        evidence = {'user': 'UE-03', 'assessment': result['assessment'], 'detail': result['detail']}
        bad = '【结论】用户 UE-03 体验达标，当前正常。【依据】指标良好。【方案】继续观察。'
        assert not al.verify(bad, evidence)['ok']
    good = ('【结论】整窗越阈占比未超过暂定门槛，截至分析时刻存在连续异常。'
            '【依据】末端连续越阈。【方案】先核查相关 AP，再评估候选方案。')
    assert al.verify(good, evidence)['ok'], al.verify(good, evidence)


def test_user_model_bad_verdict_is_rejected_end_to_end():
    data = rows(bad_indices=range(60))
    fake = '【结论】用户 UE-03 体验达标，当前正常。【依据】指标良好。【方案】无需处理。'
    with patch.object(m, 'filter_rows', side_effect=filtered(data)), patch.object(m,'ENABLE_LLM',True), \
         patch.object(m,'get_intent',return_value={'intent':'user_experience','params':{'user_id':'UE-03'},'source':'llm'}), \
         patch.object(m.llm_agent, 'call_llm_chat', return_value=fake):
        result = m.agent_chat(m.ChatRequest(message='UE-03 体验怎么样', scenario='校园',
                              window_start=START, window_end=END, session_id='ue-bad-verdict'))['data']
    assert not result.get('answer_llm') and not result['answer_check']['ok']
    assert '未达标' in result['reply']


def test_user_missing_data_never_calls_answer_model():
    with patch.object(m,'filter_rows',side_effect=filtered(rows(rsrp=None))), patch.object(m,'ENABLE_LLM',True), \
         patch.object(m,'get_intent',return_value={'intent':'user_experience','params':{'user_id':'UE-03'},'source':'llm'}), \
         patch.object(m.answer_llm, 'generate', side_effect=AssertionError('数据不足不应生成正文')):
        result = m.agent_chat(m.ChatRequest(message='UE-03 体验怎么样', scenario='校园',
                              window_start=START, window_end=END, session_id='ue-missing-data'))['data']
    assert not result.get('answer_llm') and result['status'] == 'insufficient_data'


def test_user_parameter_direction_numeric_units_and_citation_are_bound():
    evidence = {'ap': 'AP-01','metrics': {'rsrp': -111.06}, 'plans': [
        {'name':'下调天线下倾角','parameters':[{'id':'tilt','current':8,'suggested':5}], 'effects':[]}],
        'references': [{'label':'故障定义 §2.2','section':'2.2','text':'40/50 频段，连续 3 分钟'}]}
    def verify(body):
        return al.verify('【结论】AP-1 待核查。【依据】依据：故障定义 §2.2。'+body+'【方案】现场复核。',evidence)
    assert verify('倾角8→5。')['ok']
    for body in ('倾角8→2。','倾角5→8。','RSRP 4 dBm。','PRB 100%。',
                 'RSRP 50 dBm。','依据：国际规范 §2.2。','依据：伪故障定义 §2.2。'):
        assert not verify(body)['ok'], body


def test_user_supported_visualization_keeps_task_but_rejects_ungrounded_object():
    with patch.object(m, 'ENABLE_LLM', True), patch.object(m, 'get_intent', return_value={
            'intent':'get_topology', 'source':'llm', 'params':{'ap_id':'ap-2'}, 'confidence':.9}), \
         patch.dict(m.TOOLS, {'get_topology': lambda *_a,**_k: {'reply':'已查询拓扑', 'intent':'get_topology'}}), \
         patch.object(m.answer_llm, 'generate', return_value={'text':None, 'check':{}, 'attempts':0}):
        result = m.agent_chat(m.ChatRequest(message='查看这个基站的连接情况',scenario='校园',
                              window_start=START, window_end=END,session_id='ue-visual'))['data']
    assert result['intent_debug']['source'] == 'llm'
    assert result['intent_debug']['parsed'].get('ap_id') is None
    assert '未由本轮明确指定' in result['intent_debug']['degrade_reason']


def test_user_intent_is_allowed_by_real_model_parser():
    with patch.object(m.llm_agent, 'call_llm', return_value='{"action":"user_experience","params":{"user_id":"UE-3"}}'):
        assert m.llm_agent.get_intent('UE-3 体验如何')['source'] == 'llm'


def test_user_metric_pairs_cannot_borrow_another_metric_and_question_is_not_measurement():
    evidence = {'ap': 'AP-01', 'question': '请把功率设成4dBm，吞吐100%',
                'metrics': {'rsrp': -110}, 'plans': [{'parameters': [
                    {'id': 'tilt', 'current': 8, 'suggested': 5}],
                    'effects': [{'metric': 'sinrDb', 'before': 8, 'after': 10},
                                {'metric': 'throughputMbps', 'before': 50, 'after': 55}]}]}
    for body in ('发射功率4 dBm', 'PRB 100%', 'RSRP 8→10 dBm', '倾角8→10°'):
        text = f'【结论】AP-01 待核查。【依据】{body}。【方案】现场复核。'
        assert not al.verify(text, evidence)['ok'], body


def test_user_simulate_rejects_nan_unknown_and_out_of_range_parameters():
    plan = analyze(rows(bad_indices=range(55,60)))['plans'][0]
    for values in ({'tilt':'nan'}, {'tilt':'inf'}, {'tilt':20}, {'tilt':4.5}, {'unknown':1}):
        result = m.plan_simulate(m.PlanSimulateRequest(plan_id=plan['id'],scenario='校园',
                                window_start=START,window_end=END,parameter_values=values))
        assert result['code'] == 40002, values
