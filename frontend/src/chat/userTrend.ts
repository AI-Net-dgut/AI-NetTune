import type { EChartsOption } from 'echarts';
import { displayMetric } from '../services/metrics';
import type { GoalAssessment, UserAssessment } from '../types/plan';
export type UserTrendMetric = 'rsrp' | 'sinr' | 'throughput' | 'latency';
export interface UserTrendPoint {
  time: string; apId: string; rsrp: number | null; sinr: number | null;
  throughput: number | null; latency: number | null;
}
export interface UserTrendData { points: UserTrendPoint[]; goal?: GoalAssessment }
export const userTrendMetrics = {
  rsrp: { label: 'RSRP', unit: 'dBm' }, sinr: { label: 'SINR', unit: 'dB' },
  throughput: { label: '吞吐量', unit: 'Mbps' }, latency: { label: '时延', unit: 'ms' },
};
const clock = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
/** 趋势只使用观测值；时间断档插入空点，不能把断档画成连续正常。 */
export function userTrendOption(data: UserTrendData, metric: UserTrendMetric, assessment?: UserAssessment): EChartsOption {
  const points: [number, number | null][] = [];
  let previous: number | undefined;
  for (const point of data.points) {
    const time = Date.parse(point.time);
    if (previous !== undefined && time - previous > 90_000) points.push([previous + 1, null]);
    points.push([time, point[metric]]); previous = time;
  }
  const threshold = assessment?.checks.find(c => c.metric === metric)?.threshold;
  const target = metric === 'throughput' ? data.goal?.target : undefined;
  const lines: { yAxis: number; name: string; lineStyle: { color: string; type: 'dashed' }; label: { formatter: string; position: 'insideEndTop' } }[] = [];
  if (threshold !== undefined && Number.isFinite(threshold)) lines.push({ yAxis: threshold, name: '暂定告警阈值',
    lineStyle: { color: '#c87722', type: 'dashed' }, label: { formatter: `暂定阈值 ${threshold}`, position: 'insideEndTop' } });
  if (target != null && Number.isFinite(target)) lines.push({ yAxis: target, name: '本次固定基线的提升目标',
    lineStyle: { color: '#8464c4', type: 'dashed' }, label: { formatter: `目标 ${target.toFixed(2)}`, position: 'insideEndTop' } });
  // 参考线参与坐标范围，避免正常采样全部在阈值上方时阈值线被裁掉。
  const extent = [...points.map(p => p[1]), ...lines.map(line => line.yAxis)]
    .filter((value): value is number => value !== null && Number.isFinite(value));
  const low = extent.reduce((a, b) => Math.min(a, b), Infinity);
  const high = extent.reduce((a, b) => Math.max(a, b), -Infinity);
  const padding = Number.isFinite(low) ? Math.max((high - low) * .12, 1) : 1;
  return { animation: false, backgroundColor: '#fff',
    grid: { left: 12, right: 16, top: 28, bottom: 8, outerBoundsMode: 'same', outerBoundsContain: 'all' },
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    xAxis: { type: 'time', axisLabel: { formatter: (value: number) => clock.format(value) } },
    yAxis: { type: 'value', name: userTrendMetrics[metric].unit, scale: true,
      // 只格式化坐标文字，避免上限留白计算产生浮点尾数；原始采样值保持不变。
      axisLabel: { formatter: (value: number) => displayMetric(value), hideOverlap: true },
      min: Number.isFinite(low) ? low - padding : undefined, max: Number.isFinite(high) ? high + padding : undefined },
    series: [{ type: 'line', name: '用户实际采样', showSymbol: data.points.length < 3, connectNulls: false,
      data: points, itemStyle: { color: '#238f90' }, markLine: { symbol: 'none', data: lines } }],
  };
}
