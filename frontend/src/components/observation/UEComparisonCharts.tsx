/* eslint-disable react-refresh/only-export-components -- 图表选项函数保留独立导出，供数据与绘图回归验证。 */
import { useMemo } from 'react';
import type { EChartsOption } from 'echarts';
import type { DashboardData, MetricKey } from '../../types/backend';
import { METRIC_META } from '../../types/backend';
import type { ObservationPeriod, ObservationTarget } from '../../services/dataObservation';
import { observationSnapshot, targetLabel } from '../../services/dataObservation';
import { ueComparison } from '../../services/ueComparison';
import type { UEComparisonItem } from '../../services/ueComparison';
import { displayMetric } from '../../services/metrics';
import ChartPanel, { LIGHT_AXIS_STYLE, CHART_FONT } from '../charts/ChartPanel';

const KEYS: MetricKey[] = ['rsrpDbm', 'sinrDb', 'throughputMbps'];
export function ueComparisonOption(items: UEComparisonItem[], metric: MetricKey, selectedUeId: string | null): EChartsOption {
  const meta = METRIC_META[metric];
  const values = items.map(item => item.means[metric]).filter((value): value is number => value !== null);
  const negative = metric === 'rsrpDbm' || metric === 'sinrDb';
  return {
    animation: false, backgroundColor: '#f7fbff', textStyle: { fontFamily: CHART_FONT },
    grid: { left: 6, right: 12, top: 6, bottom: 4, outerBoundsMode: 'same', outerBoundsContain: 'all' },
    tooltip: { trigger: 'axis', renderMode: 'richText', formatter: (params: unknown) => {
      const first = (Array.isArray(params) ? params[0] : params) as { dataIndex?: number } | undefined;
      const item = items[first?.dataIndex ?? -1];
      return item ? `${item.ueId}\n${meta.label}：${displayMetric(item.means[metric] ?? undefined)} ${meta.unit}\n参与统计的记录：${item.recordCount}` : '暂无采样';
    } },
    xAxis: { ...LIGHT_AXIS_STYLE, type: 'value', splitNumber: 3,
      axisLabel: { ...LIGHT_AXIS_STYLE.axisLabel, fontSize: 9, hideOverlap: true },
      min: negative && values.length ? Math.floor(Math.min(0, ...values) - 5) : 0,
      max: negative && values.length ? Math.ceil(Math.max(0, ...values) + 3) : undefined },
    yAxis: { ...LIGHT_AXIS_STYLE, type: 'category', inverse: true, data: items.map(item => item.ueId),
      axisLine: { show: false }, splitLine: { show: false },
      axisLabel: { ...LIGHT_AXIS_STYLE.axisLabel, fontSize: 10, width: 64, overflow: 'truncate' } },
    series: [{ name: meta.label, type: 'bar', barMaxWidth: 12,
      data: items.map(item => ({ name: item.ueId, value: item.means[metric], itemStyle: {
        color: item.ueId === selectedUeId ? '#ffbb4a' : '#36b7f3',
        opacity: selectedUeId && item.ueId !== selectedUeId ? 0.65 : 1,
        borderColor: item.ueId === selectedUeId ? '#ffe6a1' : '#36b7f3', borderWidth: item.ueId === selectedUeId ? 1 : 0,
      }, label: { color: item.ueId === selectedUeId ? '#5f410d' : '#173b6c' } })),
      label: { show: items.length <= 8, position: 'insideRight', color: '#173b6c', fontSize: 10,
        formatter: params => displayMetric(items[params.dataIndex]?.means[metric] ?? undefined) },
    }],
  };
}
interface Props {
  data: DashboardData; comparisonScope: ObservationTarget; target: ObservationTarget;
  period: ObservationPeriod; onSelectUE: (id: string) => void;
}
export default function UEComparisonCharts({ data, comparisonScope, target, period, onSelectUE }: Props) {
  const items = useMemo(() => ueComparison(data, comparisonScope, period), [data, comparisonScope, period]);
  const selectedUeId = target.kind === 'ue' ? target.id : null;
  const frameTime = useMemo(() => observationSnapshot(data.metrics, { kind: 'all' }, 'latest').frameTime, [data.metrics]);
  return <section className="ain-ue-comparison" aria-label="用户横向对比">
    <header className="ain-ue-comparison-heading"><h2>用户横向对比</h2>
      <p>{targetLabel(comparisonScope)} · {period === 'latest' ? `最新帧 ${frameTime || '无数据'}` : '窗口内各UE采样均值'} · {items.length} 个 UE</p>
      <div className="ain-ue-comparison-legend"><span><i />其他用户</span><span><i className="is-selected" />选中用户</span></div>
    </header>
    {items.length ? <div className="ain-ue-comparison-charts">{KEYS.map(metric => <ChartPanel key={metric}
      title={`${METRIC_META[metric].label} / ${METRIC_META[metric].unit}`} description={`各UE对比 · ${METRIC_META[metric].unit}`} height={Math.max(72, items.length * 18 + 24)}
      option={ueComparisonOption(items, metric, selectedUeId)} className="ain-ue-comparison-chart" onSelect={onSelectUE} />)}</div>
      : <p className="ain-observation-empty">该 AP 在此统计范围内没有服务用户。可切换“窗口均值”或返回全网查看。</p>}
    <div className="ain-ue-comparison-buttons" aria-label="选择对比用户">{items.map(item => <button type="button" key={item.ueId}
      aria-pressed={item.ueId === selectedUeId} onClick={() => onSelectUE(item.ueId)}>{item.ueId}</button>)}</div>
    <p className="ain-observation-note">点击柱子可联动；缺失值留空。{selectedUeId && ` 当前高亮：${selectedUeId}。`}</p>
  </section>;
}
