"""第三步：同窗不同提问的专项结论、证据、方案必须保持对应。"""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch
import json
import main
import answer_llm

START, END = '2026-01-02 14:00:00', '2026-01-02 14:59:00'
QUESTIONS = {
    'overview': '分析当前网络异常及原因，给出处理方案并比较仿真预计效果',
    'priority': '分析哪个 AP 的异常最需要处理，说明原因，给出方案并比较预计效果',
    'coverage': '分析弱覆盖异常的区域及原因，提出方案并比较预计覆盖效果',
    'congestion': '分析 AP 拥塞异常及原因，提出方案并比较预计负载变化',
    'connections': '分析用户连接分配异常及原因，提出调整方案并比较预计用户体验',
}

def rows():
    return [dict(timestamp=f'2026-01-02 14:{i:02d}:00', scenario='校园',
                 ap_id=ap, user_id='UE-'+ap[-2:], ap_x=0.,ap_y=0.,ue_x=2.,ue_y=2.,
                 rsrp=-112. if ap=='AP-01' else -90., sinr=-8. if ap=='AP-01' else 15.,
                 throughput=10., prb_usage=35. if ap=='AP-01' else 96., packet_loss=1.,
                 latency=10.,traffic_demand=10.,active_users=1,retransmission_count=0,
                 network_status='weak_coverage' if ap=='AP-01' else 'congestion')
            for i in range(60) for ap in ('AP-01','AP-02')]

@contextmanager
def isolated(sample=None, model=None):
    with patch.object(main,'filter_rows',return_value=deepcopy(rows() if sample is None else sample)), \
         patch.object(main,'_supported_range',return_value=None), \
         patch.dict(main._SPEC,{'df':None},clear=True), \
         patch.object(main,'_ANOMALY_CACHE_ON',False), \
         patch.object(main,'ENABLE_LLM',model is not None), \
         patch.object(main.llm_agent,'call_llm_chat',side_effect=model or AssertionError('禁止模型调用')), \
         patch('requests.sessions.Session.request',side_effect=AssertionError('禁止外部请求')):
        yield

def ask(kind, ap=None):
    return main.analyze_anomaly(main.AnomalyRequest(scenario='校园',window_start=START,
        window_end=END,message=QUESTIONS.get(kind,kind),ap_id=ap))['data']

def text(d):
    return '\n'.join(s['text'] for s in d['answer_focus']['sections'])

def test_diagnosis_focus_rule_presets_have_different_answers():
    with isolated():
        results={k:ask(k) for k in QUESTIONS}
    assert {d.get('answer_focus',{}).get('kind') for d in results.values()}==set(QUESTIONS)
    assert len({text(d) for d in results.values()})==5, '五个问题不能套同一份规则回答'
    assert '优先' in text(results['priority']) and '同档' in text(results['priority'])
    assert '连接' in text(results['connections']) and '不足' in text(results['connections'])


def test_diagnosis_focus_secondary_ap_owns_its_evidence_and_plans():
    with isolated():
        d=ask('congestion')
    assert d['is_anomaly'] and d['anomaly_type']=='网络拥塞'
    assert d['affected_aps']==['AP-02'], d['affected_aps']
    assert any(e['metric']=='prb_usage' and e['avg_value']==96 for e in d['kpi_evidence'])
    assert d['plans'] and all(p['objectIds']==['AP-02'] for p in d['plans'])
    assert all(p['scope']=={'scenario':'校园','start':START,'end':END} for p in d['plans'])
    assert all(s['ap_id']=='AP-02' and s['type']=='congestion' for s in d['anomaly_spans'])
    assert d['answer_focus']['plan_eligible']


def test_diagnosis_focus_missing_requested_fault_never_borrows_other_plans():
    weak=[r for r in rows() if r['ap_id']=='AP-01']
    with isolated(weak):
        d=ask('congestion')
    assert d['answer_focus']['status']=='not_confirmed'
    assert '未确诊' in text(d) and '拥塞' in text(d)
    assert not d['answer_focus']['plan_eligible'] and not d['plans']
    assert not d['is_anomaly'] and not d['anomaly_spans']
    assert d['answer_focus']['overview']['anomaly_type']=='覆盖故障'
    assert '补盲' not in text(d), '不能把覆盖方案当拥塞建议'


def test_diagnosis_focus_connections_are_observations_not_fault_diagnosis():
    with isolated():
        d=ask('connections')
    assert d['answer_focus']['status']=='insufficient'
    assert 'UE' in text(d) and '切换' in text(d)
    assert not d['plans'] and not d['candidates']
    assert not d['answer_focus']['plan_eligible']
    assert '连接分配异常已确认' not in text(d)


def test_diagnosis_focus_ap_filter_cannot_borrow_other_ap():
    with isolated():
        d=ask('congestion','AP-01')
    assert not d['is_anomaly'] and not d['plans']
    assert 'AP-02' not in text(d)
    assert not d['affected_aps']


def test_diagnosis_focus_model_receives_focused_facts_and_question():
    calls=[]
    def model(system,user,_temp=.2):
        if not user.startswith('用户问题：'):return ''
        bundle=json.loads(user.split('）：\n',1)[1].split('\n上一次回答',1)[0])
        calls.append((system,user,bundle))
        # 复查（RAG）：证据里带 references 时，回答必须标注出处，否则校验器会拒收
        refs = bundle.get('references') or []
        cite = f"依据：{refs[0]['label']}。" if refs else ''
        return '【结论】AP-02 被判定存在拥塞。【依据】PRB 96%。' + cite + '【方案】先核查负载，按对应方案仿真。'
    with isolated(model=model):
        d=ask('congestion')
    assert d['answer_check']['ok'],d['answer_check']
    assert calls[0][2]['ap']=='AP-02' and calls[0][2]['answer_focus']['kind']=='congestion'
    assert QUESTIONS['congestion'] in calls[0][1]
    assert '拥塞' in calls[0][0] and '弱覆盖方案' in calls[0][0]
    assert all('补盲' not in p['name'] for p in calls[0][2]['plans'])


def test_diagnosis_focus_failed_or_irrelevant_model_uses_same_focused_rule_reply():
    def bad(_system,user,_temp=.2):
        return '【结论】已经确认 AP-99 硬件故障。【依据】RSRP -199 dBm。【方案】调整天线。' if user.startswith('用户问题：') else ''
    with isolated(model=bad):d=ask('congestion')
    assert not d['answer_check']['ok'] and not d['answer_llm']
    assert '拥塞' in text(d) and 'AP-99' not in text(d)
    assert all(p['objectIds']==['AP-02'] for p in d['plans'])


def test_diagnosis_focus_legacy_api_preserves_whole_network_result():
    with isolated():
        d=main.analyze_anomaly(main.AnomalyRequest(scenario='校园',window_start=START,window_end=END))['data']
    assert d['anomaly_type']=='覆盖故障' and d['affected_aps']==['AP-01','AP-02']
    assert d.get('answer_focus') is None


def test_diagnosis_focus_verified_model_cannot_ignore_requested_topic():
    evidence={'root_cause_confirmed':False,'metrics':{'prb_usage':96},'plans':[],
              'answer_focus':{'kind':'congestion','label':'拥塞专项','status':'not_confirmed'}}
    unrelated='【结论】存在弱覆盖。【依据】以窗口指标为准。【方案】调整天线覆盖。'
    assert not answer_llm.verify(unrelated,evidence)['ok'], '通过数字校验不等于回答了所问问题'


def test_diagnosis_focus_recovery_state_does_not_borrow_other_active_ap():
    sample=rows()
    for r in sample:
        if r['ap_id']=='AP-01' and r['timestamp'] >= '2026-01-02 14:35:00':
            r.update(network_status='normal',rsrp=-90.,sinr=15.,throughput=60.)
    with isolated(sample):d=ask('coverage')
    assert d['is_anomaly'] and d['current_state']=='recovered_in_window'
    assert all(s['ap_id']=='AP-01' and s['end'] < END for s in d['anomaly_spans'])
    assert '已恢复' in text(d),text(d)


def test_diagnosis_focus_overview_keeps_different_ap_fault_types_distinct():
    with isolated():d=ask('overview')
    conclusion=d['answer_focus']['sections'][0]['text']
    assert 'AP-01：覆盖故障' in conclusion and 'AP-02：网络拥塞' in conclusion,conclusion
    assert '主异常' in d['answer_focus']['sections'][1]['text']
    assert 'AP-01' in d['answer_focus']['sections'][3]['text']


def test_diagnosis_focus_priority_ranks_within_requested_specialty():
    with isolated():d=ask('哪个 AP 的拥塞最需要优先处理？')
    assert d['affected_aps']==['AP-02'] and d['anomaly_type']=='网络拥塞',d['anomaly_type']
    assert all(p['objectIds']==['AP-02'] for p in d['plans'])
    weak=[r for r in rows() if r['ap_id']=='AP-01']
    with isolated(weak):missing=ask('哪个 AP 的拥塞最需要优先处理？')
    assert missing['answer_focus']['status']=='not_confirmed'
    assert not missing['plans'] and '拥塞' in text(missing)


def test_diagnosis_focus_unsupported_is_rule_only_even_when_model_is_available():
    calls=[]
    def unrelated(_system,user,_temp=.2):
        calls.append(user)
        return '【结论】拥塞尚未确诊。【依据】以窗口指标为准。【方案】应用覆盖补盲方案，调整天线提升覆盖。'
    sample=[r for r in rows() if r['ap_id']=='AP-01']
    with isolated(sample,unrelated):d=ask('congestion')
    assert not d['answer_check']['ok'] and not d['answer_llm'],d['answer_llm']
    assert not any(u.startswith('用户问题：') for u in calls), '缺少专项证据时不让模型编造方案'


def test_diagnosis_focus_model_cannot_attribute_target_metrics_to_other_ap():
    def wrong(_system,user,_temp=.2):
        return '【结论】AP-01 被判定存在拥塞。【依据】PRB 96%。【方案】先核查负载，按对应方案仿真。' if user.startswith('用户问题：') else ''
    with isolated(model=wrong):d=ask('congestion')
    assert not d['answer_check']['ok'] and not d['answer_llm']
    assert 'AP-02' in text(d)


def test_diagnosis_focus_hourly_fallback_stays_inside_requested_window():
    from datetime import datetime,timedelta
    sample=[]
    for i in range(90):
        r=rows()[0];r.update(timestamp=(datetime(2026,1,2,14,30)+timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M:%S'),
            network_status='normal',rsrp=-115. if i<30 else -85.,sinr=-10. if i<30 else 20.,throughput=60.)
        sample.append(r)
    with isolated(sample):
        d=main.analyze_anomaly(main.AnomalyRequest(scenario='校园',window_start='2026-01-02 14:30:00',
            window_end='2026-01-02 15:59:00',message=QUESTIONS['coverage']))['data']
    assert d['anomaly_scope']=='hour'
    assert all(s['start']>='2026-01-02 14:30:00' and s['end']<='2026-01-02 15:59:00' for s in d['anomaly_spans']),d['anomaly_spans']


def test_diagnosis_focus_unrelated_spans_cannot_hide_target_recovery():
    from datetime import datetime,timedelta
    sample=[]
    for i in range(240):
        for ap in ('AP-01','AP-02'):
            r=next(r for r in rows() if r['ap_id']==ap)
            label=('weak_coverage' if ap=='AP-01' and i<=96 and i%4==0 else
                   'congestion' if ap=='AP-02' and 180<=i<=184 else 'normal')
            r.update(timestamp=(datetime(2026,1,2)+timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M:%S'),network_status=label)
            sample.append(r)
    with isolated(sample):
        d=main.analyze_anomaly(main.AnomalyRequest(scenario='校园',window_start='2026-01-02 00:00:00',
            window_end='2026-01-02 03:59:00',message=QUESTIONS['congestion']))['data']
    assert d['current_state']=='recovered_in_window',d['anomaly_spans']
    assert d['anomaly_spans']==[{'ap_id':'AP-02','type':'congestion','start':'2026-01-02 03:00:00',
        'end':'2026-01-02 03:04:00','minutes':5,'source':'label'}]


def test_diagnosis_focus_model_priority_cannot_rank_unrelated_ap():
    def wrong(_system,user,_temp=.2):
        return '【结论】优先核查 AP-01 的拥塞。【依据】PRB 96%。【方案】先核查负载，按对应方案仿真。' if user.startswith('用户问题：') else ''
    with isolated(model=wrong):d=ask('哪个 AP 的拥塞最需要优先处理？')
    assert not d['answer_check']['ok'] and not d['answer_llm']
    assert 'AP-01' not in d['answer_focus']['sections'][1]['text'], '拥塞优先级不能把覆盖 AP 当成同档拥塞对象'
