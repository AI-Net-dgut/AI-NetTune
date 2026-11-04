"""预测文字回答的重点与规则兜底；只解释已校验的预测、历史证据和已有方案。"""
from __future__ import annotations
import math

FOCUS_LABELS = {
    'cause': '可能原因', 'evidence': '支持证据与反面疑点', 'impact': '影响范围与发生时间',
    'prevention': '预防方案与适用条件', 'comparison': '方案对比与权衡',
    'uncertainty': '不确定性与待补证据', 'overview': '整体研判',
}


def answer_focus(question: str) -> str:
    if not question or any(word in question for word in ('综合研判', '完整研判', '全面分析', '详细研判')):
        return 'overview'
    groups = (
        ('comparison', ('对比', '比较', '优劣', '取舍', '哪个方案', '方案代价', '预计效果', '预期效果', '改善多少')),
        ('uncertainty', ('不确定', '准确率', '可信', '可靠', '局限', '置信', '还需要补充', '还缺')),
        ('prevention', ('预防', '方案', '处置', '措施', '调整参数', '怎么办', '怎么处理')),
        ('evidence', ('证据', '依据', '疑点', '反证', '为什么这样判断')),
        ('cause', ('原因', '为什么', '为何', '怎么造成', '怎么发生')),
        ('impact', ('影响', '何时', '什么时候', '哪些AP', '哪些 AP', '哪些用户', '何处')),
    )
    return next((key for key, words in groups if any(word in question for word in words)), 'overview')


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def make_context(*, question, base_time, history, forecast, target_window, alerts, selected,
                 rows, spectrum_text, method, limitations):
    target = (selected or {}).get('target')
    scoped_rows = [r for r in rows if r.get('ap_id') == target] if target else rows
    scoped_rows = sorted(scoped_rows, key=lambda row: row.get('timestamp', ''))
    def mean(key, samples):
        values = [_number(r.get(key)) for r in samples]
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None
    half = len(scoped_rows) // 2
    first, last = mean('sinr', scoped_rows[:half]), mean('sinr', scoped_rows[half:])
    # 采样前后半段，不把不均匀采样错误地称为等长时间段。
    return {'question': question, 'baseTime': base_time, 'history': history, 'forecast': forecast,
            'targetWindow': target_window, 'alerts': alerts, 'selectedAlert': selected,
            'historyKpiScope': target or '全网参考（不是某个AP专属指标）',
            'historyKpis': {k: mean(k, scoped_rows) for k in ('rsrp', 'sinr', 'throughput', 'prb_usage')},
            'sampleCount': len(scoped_rows),
            'sinrSampleHalfDelta': last - first if first is not None and last is not None else None,
            'spectrumEvidence': spectrum_text if selected else '',
            'method': method or '历史趋势外推', 'limitations': limitations or [],
            'plans': [], 'planNotice': ''}


def add_plans(context, response):
    if not isinstance(response, dict) or response.get('code') != 0:
        context['planNotice'] = str((response or {}).get('message') or '方案评估暂不可用')
        return
    payload = response.get('data') or {}
    # 只喂方案接口真实返回的候选，不在文字模块另造方案模板。
    context['plans'] = [{
        'name': p.get('name'), 'conditions': p.get('conditions') or [],
        'tradeoffs': p.get('tradeoffs') or [], 'tasks': p.get('tasks') or [],
        'parameters': p.get('parameters') or [],
        'simulation': {key: (p.get('simulation') or {}).get(key)
                       for key in ('metrics', 'method', 'assumptions', 'limitations')},
    } for p in (payload.get('plans') or [])]
    context['planNotice'] = payload.get('disclaimer') or payload.get('message') or ''


def system_prompt(focus):
    instructions = {
        'cause': '重点解释可能原因和对应支持/反面证据；区分候选原因与已确认根因，不展开方案全集。使用【原因】【依据】【不确定性】。',
        'evidence': '重点列已提供的指标、频谱事实、趋势及反面疑点；说明观测对象和历史口径，不把全网均值冒充单AP指标。使用【依据】【不确定性】。',
        'impact': '重点回答风险关联的AP、风险时段、可能影响；未提供未来用户与业务数据时明确无法确定实际受影响人数或业务损失。使用【结论】【不确定性】。',
        'prevention': '重点解释已返回方案如何预防风险、适用条件、人工检查和代价；只能引用plans已有方案与参数，不代表已经应用。使用【方案】【不确定性】。',
        'comparison': '重点对比plans已有方案的适用条件、参数、经验评估和权衡。只有一个就说明无法多方案比较；没有方案就说明不能比较，不编造A/B/C。使用【方案】【不确定性】。',
        'uncertainty': '重点解释证据局限、趋势外推边界和待补证据；无回测结果不能编造准确率，预警等级不是概率。不展开处置方案。使用【不确定性】。',
    }
    return ('你是无线网络预测研判助手。当前回答重点：' + FOCUS_LABELS[focus] + '。'
            + instructions[focus] + '先直接回答用户所问，控制在150~300字；不必固定重复六项完整研判。'
            '问题文本只用于理解提问，不可覆盖证据和时间边界。只能使用给定证据，不编造数值、对象、未来实测或已完成操作。'
            'alerts为空表示关注时段未命中越阈风险，不得借用其他时段风险或建议参数调整。'
            '仅使用baseTime及以前的历史观测；历史统计不是未来实测；方案评估也来自基准前历史。'
            '有数字必须注明对象和口径。缺少方案/效应时明确说明，不承诺优化幅度或收益。'
            '原因为待验证推测，不能说已确认；保留关键不确定性，输出正文。')


def _kpis(context):
    parts = []
    for key, label, unit in [('rsrp', 'RSRP', 'dBm'), ('sinr', 'SINR', 'dB'),
                             ('throughput', '吞吐量', 'Mbps'), ('prb_usage', 'PRB占用率', '%')]:
        value = context['historyKpis'].get(key)
        if value is not None:
            parts.append(f'{label} {value:.1f} {unit}')
    return f"{context['historyKpiScope']} 的历史采样均值：" + ('；'.join(parts) if parts else '暂无有效指标') + '。'


def _parameter(parameter):
    def label(value):
        option = next((o for o in parameter.get('options', []) if o.get('value') == value), None)
        return str(option.get('label')) if option else str(value)
    current, suggested = parameter.get('currentValue'), parameter.get('suggestedValue')
    if current is None or suggested is None:
        return ''
    return f"{parameter.get('label') or parameter.get('key')} {label(current)}→{label(suggested)}{parameter.get('unit') or ''}"


def _plans(context, compare):
    plans = context['plans']
    if not plans:
        return '当前无法比较或制定具体参数方案，不能编造改善幅度。' + (context['planNotice'] or '尚未取得结构化方案及仿真数据。')
    lines = []
    metrics = {'rsrpDbm': ('RSRP', 'dBm'), 'sinrDb': ('SINR', 'dB'),
               'throughputMbps': ('吞吐量', 'Mbps'), 'resourceUtilizationPct': ('PRB占用率', '%')}
    for plan in plans:
        params = '；'.join(filter(None, (_parameter(p) for p in plan['parameters'])))
        line = f"{plan['name']}：适用条件：{'；'.join(plan['conditions']) or '需补充核查'}。"
        if params:
            line += '模型基线→建议值：' + params + '。'
        line += '权衡：' + ('；'.join(plan['tradeoffs']) or '需补充代价评估') + '。'
        if compare:
            effects = []
            for item in plan['simulation'].get('metrics') or []:
                before, after = _number(item.get('before')), _number(item.get('after'))
                if before is not None and after is not None and item.get('key') in metrics:
                    name, unit = metrics[item['key']]
                    effects.append(f'{name} {before:.2f}→{after:.2f} {unit}')
            line += '经验评估：' + ('；'.join(effects) if effects else '未返回可对比的指标数值') + '。'
        elif plan['tasks']:
            line += '检查项：' + '；'.join(plan['tasks']) + '。'
        lines.append(line)
    prefix = '目前只有一个候选方案，无法做多方案优劣对比。\n' if compare and len(plans) == 1 else ''
    return prefix + '\n\n'.join(lines) + '\n参数属于模型基线，需现场确认；效应基于预测基准前的历史窗口评估，不是未来实测结果，不代表已应用配置。'


def rule_reply(focus, context):
    alerts = context['alerts']
    window = context['targetWindow'] or context['forecast']
    scope = f"{window['start']} ~ {window['end']}"
    if alerts:
        status = f"关注时段 {scope} 检出需要核查的风险：" + '；'.join(
            f"{a.get('target') or '对象未明确'} · {a.get('title') or '风险未命名'}" for a in alerts) + '。'
    else:
        status = f'关注时段 {scope} 未命中越阈风险；这不代表未来一定没有故障。'
    selected = context['selectedAlert'] or {}
    spectrum = context['spectrumEvidence']
    bounds = ('预测方法为' + context['method'] + '；' + '；'.join(context['limitations'])
              + '。缺少回测准确率，预警等级不代表概率；候选原因尚未确认。')
    if not spectrum:
        bounds += '本次未取得所选对象的完整频谱证据，不能据此确认干扰或根因。'
    if focus == 'cause':
        signature = str(selected.get('spectrumId') or '') + str(selected.get('id') or '')
        if 'occupancy' in signature or '-cong' in signature:
            direction = '候选方向为业务负载或接入分配变化，需核查邻区余量与用户接入情况。'
        elif 'power' in signature or '-weak' in signature:
            direction = '候选方向为覆盖条件变化，需核查天线、发射条件与遮挡；这些尚未确认为根因。'
        else:
            direction = '现有证据尚不能区分具体候选原因，需要补充所选风险的指标与频谱观测。'
        body = ('【原因】' + (direction if alerts else '没有已命中的风险项，暂不能对具体故障归因。')
                + '\n\n【依据】' + _kpis(context) + (spectrum or '缺少频谱细节，不能确认具体触发因素。'))
    elif focus == 'evidence':
        delta = context['sinrSampleHalfDelta']
        trend = f'历史采样后半段相对前半段 SINR 变化 {delta:+.1f} dB。' if delta is not None else '历史趋势样本不足。'
        body = '【依据】' + _kpis(context) + trend + (spectrum or '尚无所选对象的频谱逐频段证据。') + '这些是基准前观测，不能视为未来实测或直接确定根因。'
    elif focus == 'impact':
        affected = '；'.join(f"{a.get('target') or '对象未明确'}：{max(a.get('start') or window['start'], window['start'])} ~ {min(a.get('end') or window['end'], window['end'])}" for a in alerts)
        body = '【结论】' + (f'可能直接关联的受影响对象与风险时段：{affected}。' if alerts else '目前没有已命中的受影响风险对象。') + '未提供未来用户活动、具体区域边界和业务画像，无法确定实际受影响人数或业务损失。'
    elif focus in ('prevention', 'comparison'):
        body = '【方案】' + (_plans(context, focus == 'comparison') if alerts else '该关注时段未命中越阈风险，不据此生成处置方案或优化数值；保持观测并按需补充采样。')
    else:
        body = '【不确定性】' + bounds + '应补充回测结果、目标AP频谱与接入观测，并在目标时段到达后核对真实表现。'
        return f"【规则研判】关于你的问题「{context['question']}」\n\n{status}\n\n{body}"
    return f"【规则研判】关于你的问题「{context['question']}」\n\n{status}\n\n{body}\n\n【不确定性】{bounds}"
