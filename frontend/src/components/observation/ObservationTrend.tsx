import { useMemo, useState } from 'react';
import type { EChartsOption } from 'echarts';
import { METRIC_META } from '../../types/backend';
import type { DashboardData, MetricKey } from '../../types/backend';
import type { ObservationTarget } from '../../services/dataObservation';
import { binObservationTrend, observationTrend, targetLabel } from '../../services/dataObservation';
import ChartPanel, { AXIS_STYLE, CHART_BACKGROUND, CHART_FONT } from '../charts/ChartPanel';
import { displayMetric } from '../../services/metrics';

const dateTime = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const clock = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
function MetricTrend({ data, target, metric, small = false }: { data: DashboardData; target: ObservationTarget; metric: MetricKey; small?: boolean }) {
  const raw = useMemo(() => observationTrend(data.metrics, target, metric), [data.metrics, target, metric]);
  const points = useMemo(() => binObservationTrend(raw), [raw]);
  const binned = points.length !== raw.length;
  const meta = METRIC_META[metric];
  const label = `${targetLabel(target)} · ${meta.label}`;
  const span = (raw.at(-1)?.time ?? 0) - (raw[0]?.time ?? 0);
  const option: EChartsOption = {
    animation: false, backgroundColor: CHART_BACKGROUND, textStyle: { fontFamily: CHART_FONT },
    grid: { left: 46, right: 18, top: 25, bottom: 28, containLabel: false },
    tooltip: { trigger: 'axis', renderMode: 'richText', formatter: (params: unknown) => {
      const first = (Array.isArray(params) ? params[0] : params) as { dataIndex?: number } | undefined;
      const point = points[first?.dataIndex ?? -1];
      if (!point) return '暂无采样';
      return `${label}\n${dateTime.format(point.time)}${binned ? ` — ${dateTime.format(point.endTime)}` : ''}（北京时间）\n${binned ? '区间时刻均值' : target.kind === 'ue' ? '采样值' : '同刻用户均值'}：${displayMetric(point.value ?? undefined)} ${meta.unit}\n有效记录：${point.count}${binned ? `\n区间采样范围：${displayMetric(point.min ?? undefined)}～${displayMetric(point.max ?? undefined)} ${meta.unit}` : ''}`;
    } },
    xAxis: { ...AXIS_STYLE, type: 'time', splitNumber: small ? 3 : 4, axisLabel: { ...AXIS_STYLE.axisLabel, fontSize: 10,
      formatter: (value: number) => span >= 86400000 ? dateTime.format(value) : clock.format(value), hideOverlap: true } },
    yAxis: { ...AXIS_STYLE, type: 'value', scale: true, name: meta.unit, nameTextStyle: { color: '#c3d5ee' },
      min: metric === 'rsrpDbm' || metric === 'sinrDb' ? undefined : 0, splitNumber: 3 },
    series: [{ type: 'line', name: label, showSymbol: !binned && points.length < 150, symbolSize: 4, connectNulls: false,
      lineStyle: { color: '#72e6f3', width: 1.8 }, itemStyle: { color: '#72e6f3' },
      data: points.map(point => [point.time, point.value]) }],
  };
  return <ChartPanel title={label} description={`${meta.unit} · ${raw.length} 个采样时刻${binned ? ` → ${points.length} 个等时长汇总区间` : ''} · ${target.kind === 'ue' ? '单用户采样' : '同刻服务用户均值'}`}
    option={option} height={small ? 116 : 128} className="ain-observation-trend-chart" />;
}
export default function ObservationTrend({ data, target }: { data: DashboardData; target: ObservationTarget }) {
  const [metric, setMetric] = useState<MetricKey>('throughputMbps');
  const [compare, setCompare] = useState(false);
  const keys: MetricKey[] = compare ? ['rsrpDbm', 'sinrDb', 'throughputMbps'] : [metric];
  return <section className="ain-observation-trends" aria-label="历史时间趋势">
    <header className="ain-observation-trend-heading"><div><h2>历史时间趋势 · {targetLabel(target)}</h2>
      <p>观察变化发生的时间 · 北京时间</p></div><div className="ain-observation-trend-controls">
      <label>分析指标<select value={metric} disabled={compare} onChange={event => setMetric(event.target.value as MetricKey)}>
        {(Object.keys(METRIC_META) as MetricKey[]).map(key => <option value={key} key={key}>{METRIC_META[key].label} / {METRIC_META[key].unit}</option>)}
      </select></label><label><input type="checkbox" checked={compare} onChange={event => setCompare(event.target.checked)} />多指标对照</label></div>
    </header>
    <div className={`ain-observation-trend-grid${compare ? ' is-compare' : ''}`}>{keys.map(key => <MetricTrend key={key} data={data} target={target} metric={key} small={compare} />)}</div>
    <p className="ain-observation-note">{target.kind === 'ap' ? 'AP 曲线按每个时刻实际服务用户汇总；无关联采样时保留缺口。' : target.kind === 'all' ? '全网曲线为每个时刻有效 UE 采样的均值，吞吐量不是全网总吞吐量。' : 'UE 曲线显示该用户的历史采样。'}
      长窗口按等时长区间汇总；不同单位使用各自纵轴。KPI 的最新帧 / 窗口均值切换不改变历史曲线范围。</p>
  </section>;
}
