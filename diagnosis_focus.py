"""现存问题的回答视图：只筛选已有规则结果，不新增故障判据。"""
from __future__ import annotations
import re

LABELS = {'overview':'全网异常', 'priority':'处理优先级', 'coverage':'弱覆盖专项',
          'congestion':'拥塞专项', 'connections':'连接分配核查'}
SEVERITY = {'严重':0, '中等':1, '低':9}


def requested_category(question: str) -> str | None:
    q=question or ''
    choices=[('coverage',r'弱覆盖|覆盖异常|覆盖不足|信号差|覆盖原因'),
             ('congestion',r'拥塞|容量不足|负载过高'),
             ('connections',r'连接分配|接入分配|用户分配|连接异常|切换异常')]
    hits=[key for key,pattern in choices if re.search(pattern,q)]
    return hits[0] if len(hits)==1 else None


def classify(question: str) -> str:
    category=requested_category(question)
    if category=='connections':return 'connections'
    if re.search(r'优先|最需要|最差|最严重|先处理',question or ''):return 'priority'
    return category or 'overview'


def prepare(question, findings, hourly, ap_results):
    if not (question or '').strip():
        return None, findings, hourly
    kind = classify(question)
    category=requested_category(question)
    focus = {'kind':kind, 'label':LABELS[kind], 'category':category}
    if kind=='priority' and category:
        focus['label']=LABELS[category].replace('专项','')+'处理优先级'
    if kind == 'overview':
        selected, hours = list(findings), list(hourly)
    elif kind == 'priority':
        specialty={'coverage':'weak_coverage','congestion':'congestion'}.get(category)
        eligible=[r for r in findings if not specialty or r.get('network_status')==specialty]
        eligible_hours=[r for r in hourly if not specialty or r.get('network_status')==specialty]
        selected, hours = eligible[:1], [] if eligible else eligible_hours[:1]
    else:
        status = {'coverage':'weak_coverage', 'congestion':'congestion'}.get(kind)
        selected = [r for r in findings if status and r.get('network_status') == status]
        hours = [r for r in hourly if status and r.get('network_status') == status]
    focus['status'] = ('insufficient' if kind == 'connections' else 'confirmed' if selected or hours
                       else 'no_fault' if kind=='overview' or (kind=='priority' and not category) else 'not_confirmed')
    focus['plan_eligible'] = False  # 完成方案生成后再按实际返回值开启。
    # 对象来自已完成 AP 过滤的结果，不能从横向对照中借用其他 AP 的故障。
    focus['observed_aps'] = [r['ap_id'] for r in ap_results]
    focus['target_aps'] = list(dict.fromkeys(r['ap_id'] for r in (selected or hours)))
    focus['primary_ap'] = (selected or hours or [{}])[0].get('ap_id')
    focus['classifications'] = [dict(ap_id=ap, anomaly_type=ty)
                                for ap,ty in dict.fromkeys((r['ap_id'], r['anomaly_type'])
                                                          for r in (selected or hours))]
    return focus, selected, hours


def observations(results):
    return '；'.join(
        f"{r['ap_id']}：RSRP {r['rsrp_avg']} dBm，SINR {r['sinr_avg']} dB，"
        f"PRB {r['prb_avg']}%，吞吐 {r['thr_avg']} Mbps；该 AP 主判定为{r['anomaly_type']}"
        for r in results) or '尚无可用 AP 指标'


def evidence_text(items):
    labels = {'rsrp':('RSRP','dBm'), 'sinr':('SINR','dB'), 'prb_usage':('PRB','%'),
              'throughput':('吞吐','Mbps'), 'packet_loss':('丢包率','%'),
              'weak_coverage_ratio':('弱覆盖采样占比','%'), 'congestion_ratio':('拥塞采样占比','%'),
              'spectrum_power_bands':('功率越阈频段数','个'),
              'spectrum_occupancy_bands':('占用率越阈频段数','个')}
    out=[]
    for row in items[:4]:
        key=row.get('metric','指标'); label,unit=labels.get(key,(key,''))
        value, threshold=row.get('avg_value'),row.get('threshold')
        if key.endswith('_ratio'):
            value=round(value*100,1) if isinstance(value,(int,float)) else value
            threshold=round(threshold*100,1) if isinstance(threshold,(int,float)) else threshold
        out.append(f'{label} {value} {unit}，参考阈值 {threshold} {unit}（{row.get("status", "未标注")}）')
    return '；'.join(out) or '未返回该专项的逐项异常证据'


def finish(focus, data, results, rows):
    """状态、证据、方案全部算完后生成确定性正文；模型失败与离线使用同一份。"""
    if focus is None:
        return None
    kind=focus['kind']; status=focus['status']; targets='、'.join(focus['target_aps'])
    scope=data['anomaly_scope']; plans=data.get('plans') or []
    focus['plan_eligible']=bool(plans and status=='confirmed')
    state=data['current_state']
    temporal=('窗口内发生过异常，截至分析时刻已恢复' if state=='recovered_in_window'
              else '窗口末段仍有异常证据' if state=='fault_active' else '尚未确诊故障')
    if scope=='hour':temporal+='；结论仅涉及个别小时片段，不代表整窗持续异常'
    cause_items=data.get('candidates') or []
    candidate=cause_items[0] if cause_items else {}
    reasons=candidate.get('cause') or data.get('cause') or '当前证据不足以确认具体原因'
    limits='；'.join(candidate.get('against_evidence') or [])
    cause=f'{reasons}。{limits + "。" if limits else ""}候选原因尚未经过现场验证，不能视为已确认根因。'
    evidence=evidence_text(data.get('kpi_evidence') or [])
    plan_text=('对应方案：'+'；'.join(p['name'] for p in plans)+'。选择方案后检查参数并比较对应仿真，预计效果不代表真实网络已改善。'
               if plans else '本次未生成对应的可用配置方案或仿真效果；先补充证据，不沿用其他问题的方案。')
    if kind=='connections':
        pairs={(r.get('ap_id'),r.get('user_id')) for r in rows if r.get('ap_id') and r.get('user_id')}
        users={ue for _,ue in pairs}
        conclusion='连接分配专项证据不足，不能据此确认接入或切换异常。当前仅能查看采样中的 AP—UE 连接关系。'
        evidence=(f'采样中有 {len(users)} 个 UE、{len(pairs)} 对 AP—UE 连接关系。'
                  '一个用户在窗口内关联多个 AP 可能来自正常切换，不能直接当成故障。')
        cause='尚缺接入失败、切换成功率及用户业务连续性等专项证据；现有覆盖或拥塞结果不能代替连接分配诊断。'
        plan_text='先查看连接关系并核查接入、切换记录；未确诊连接分配故障，本次不提供其他故障的配置和仿真方案。'
    elif status=='not_confirmed':
        noun='拥塞' if kind=='congestion' or focus.get('category')=='congestion' else '弱覆盖'
        conclusion=f'所选时间及对象的现有规则结果未确诊{noun}，不提供其他异常的处理方案。'
        evidence=observations(results)
        cause=('本结果按已有 AP 主判定筛选，不能排除同一 AP 上并存、短时或尚未采集的异常；'
               f'未返回足以支持{noun}专项原因判断的证据。')
        plan_text=f'先核查{noun}专项指标与现场记录；证据补齐后再选择配置，不套用其他异常的方案。'
    elif status=='no_fault':
        conclusion=('当前范围未确诊故障，没有需要排序的已确诊异常。' if kind=='priority'
                    else '当前范围未确诊故障；这不等于各项指标都良好，也不能排除未采集的因素。')
        evidence=observations(results)
        hints=data.get('performance_hints') or []
        cause='；'.join(h['cause'] for h in hints) if hints else '现有证据不足以确认具体故障原因。'
        plan_text='继续观察或补充采样；未确诊故障，本次不生成故障配置方案。'
    elif kind=='priority':
        chosen=next((r for r in results if r['ap_id'] in focus['target_aps']),None)
        same=[r['ap_id'] for r in results if chosen and r['anomaly_type'] not in ('正常','性能提示')
              and r['level']==chosen['level']
              and (not focus.get('category') or r.get('network_status') ==
                   {'coverage':'weak_coverage','congestion':'congestion'}.get(focus['category']))]
        conclusion=f'优先核查 {targets} 的{data["anomaly_type"]}：{temporal}。'
        evidence=(f'按现有异常等级排序（严重先于中等），不把规则评分当成风险概率。'
                  + (f'同档对象有 {"、".join(same)}；同档按现有结果顺序展示首项，不代表已证明该 AP 更危险。' if len(same)>1 else '')
                  + '所选对象依据：'+evidence)
    elif kind=='overview':
        facts='；'.join(f'{r["ap_id"]}：{r["anomaly_type"]}' for r in focus['classifications'])
        primary=f'{focus["primary_ap"]} 的{data["anomaly_type"]}'
        conclusion=f'本次全网研判的窗口异常：{facts}。窗口整体状态：{temporal}。以下原因与方案针对主异常 {primary}。'
        evidence=f'主异常 {focus["primary_ap"]} 的依据：{evidence}'
        plan_text=f'针对主异常 {primary}。{plan_text}'
    else:
        prefix='本次弱覆盖专项：' if kind=='coverage' else '本次拥塞专项：' if kind=='congestion' else '本次全网研判：'
        conclusion=f'{prefix}{targets} 被判定存在{data["anomaly_type"]}，{temporal}。'
    focus['sections']=[{'title':'研判结论','text':conclusion}, {'title':'判断依据','text':evidence},
                       {'title':'原因与限制','text':cause}, {'title':'处理与预估','text':plan_text}]
    return focus


def rule_reply(focus):
    return '\n'.join(f'【{s["title"]}】{s["text"]}' for s in focus['sections'])


def prompt(focus):
    return (f'\n本次回答重点：{focus["label"]}。必须先回答用户所问专项，再引用对应证据。'
            'answer_focus.sections 是已计算好的回答事实，必须保留其中的未确诊、证据不足、同档及时间范围限制。'
            '问拥塞时不能用弱覆盖方案充当拥塞建议；不能把同窗其他异常当成本次专项故障。'
            'plan_eligible=false 时不得声称已生成专项配置、预计改善幅度或仿真效果。'
            '结论中只说明本次 target_aps 的研判；优先级的同档及背景 AP 放在依据中说明。'
            '请继续使用【结论】【依据】【方案】，简洁回答；原因、限制按问题需要补充。')
