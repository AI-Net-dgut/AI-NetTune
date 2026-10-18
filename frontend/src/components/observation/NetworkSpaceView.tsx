import { useMemo, useState } from 'react';
import type { DashboardData, NetworkLink, SpatialMapData } from '../../types/backend';
import type { ObservationTarget } from '../../services/dataObservation';
import { isRelatedLink, latestObservationLinks, targetLabel } from '../../services/dataObservation';
import { topologyNodes } from '../../services/topology';
import { displayMetric } from '../../services/metrics';
import SpatialChart, { createProjection, SceneAxes, SceneNode, SCENE_VIEWBOX } from '../charts/SpatialChart';

const BANDS = [
  { label: '< −105', color: '#67768a' }, { label: '−105～−95', color: '#2e69d2' },
  { label: '−95～−85', color: '#36b7f3' }, { label: '−85～−75', color: '#27d7d2' },
  { label: '≥ −75', color: '#58edc6' },
];
const VARIANCE_COLORS = ['#36b7f3', '#27d7d2', '#d7df81', '#f3b45b', '#ef7769'];
type Layer = 'topology' | 'sampled' | 'estimated' | 'variance';
interface Props {
  data: DashboardData; target: ObservationTarget; spatial: SpatialMapData | null;
  pending: boolean; error: string; onRetry: () => void;
  onSelect: (target: ObservationTarget) => void;
  selectedLink: NetworkLink | null; onSelectLink: (link: NetworkLink) => void;
  spatialNotice?: string;
}
export default function NetworkSpaceView({ data, target, spatial, pending, error, onRetry, onSelect, selectedLink, onSelectLink, spatialNotice = '' }: Props) {
  const [layer, setLayer] = useState<Layer>('sampled');
  const [connections, setConnections] = useState(true);
  const [connectionPeriod, setConnectionPeriod] = useState<'latest' | 'window'>('window');
  const latestLinks = useMemo(() => latestObservationLinks(data.metrics), [data.metrics]);
  const links = connectionPeriod === 'latest' ? latestLinks : data.links;
  // 场景固定边界；异步估计结果和 UE 移动都不再改变画布比例。
  const area = data.area;
  const nodes = useMemo(() => topologyNodes(data, connectionPeriod), [data, connectionPeriod]);
  const projection = createProjection(area);
  const availableVariance = spatial?.method === 'kriging' && spatial.points.some(point => point.variance !== null);
  const sampled = layer === 'sampled' || (layer === 'estimated' && (!spatial || !spatial.points.length));
  const cells = sampled ? data.cells.map(cell => ({ x: cell.x, y: cell.y, width: data.gridSize, height: data.gridSize,
    value: cell.value, detail: `采样网格 (${cell.x}, ${cell.y}) m：${cell.value.toFixed(1)} dBm；${cell.count} 条记录` }))
    : layer === 'topology' ? [] : (spatial?.points ?? []).flatMap(point => {
      const value = layer === 'variance' ? point.variance : point.value;
      if (value === null) return [];
      return [{ x: point.x - spatial!.cellSize.x / 2, y: point.y - spatial!.cellSize.y / 2,
        width: spatial!.cellSize.x, height: spatial!.cellSize.y, value,
        detail: `中心 (${point.x}, ${point.y}) m：${layer === 'variance' ? '估计方差' : 'RSRP'} ${value.toFixed(2)} ${layer === 'variance' ? 'dB²' : 'dBm'}` }];
    });
  const varianceValues = cells.map(cell => cell.value);
  const varianceMin = varianceValues.length ? Math.min(...varianceValues) : 0;
  const varianceMax = varianceValues.length ? Math.max(...varianceValues) : 0;
  const color = (value: number) => layer === 'variance'
    ? VARIANCE_COLORS[varianceMax === varianceMin ? 0 : Math.min(4, Math.floor((value - varianceMin) / (varianceMax - varianceMin) * 5))]
    : BANDS[value < -105 ? 0 : value < -95 ? 1 : value < -85 ? 2 : value < -75 ? 3 : 4].color;
  const byId = new Map(nodes.map(node => [node.id, node]));
  const missingNodes = nodes.filter(node => node.missingSample).length;
  const outsideNodes = nodes.filter(node => !projection?.contains(node)).length;
  const outsideCells = cells.filter(cell => cell.x < area.xMin || cell.y < area.yMin || cell.x + cell.width > area.xMax || cell.y + cell.height > area.yMax).length;
  const related = new Set(links.filter(link => isRelatedLink(link, target)).flatMap(link => [link.source, link.target]));
  const description = layer === 'topology' ? 'AP / UE 位置与服务关系' : layer === 'variance' ? '空间不确定性 · dB²'
    : sampled ? data.sampledFrameTime !== undefined ? `最新帧采样网格 · ${data.sampledFrameTime ?? '暂无采样'} · RSRP`
      : '全网采样网格 · 历史窗口 RSRP 均值' : `${spatial?.method === 'kriging' ? 'Kriging 空间估计' : '后端网格均值'} · 历史窗口 RSRP`;
  return <SpatialChart title="网络空间主图" description={description} className="ain-observation-map" footer={<>
    {layer !== 'topology' && layer !== 'variance' && <div className="ain-spatial-key">{BANDS.map(band => <span key={band.label}><i style={{ background: band.color }} />{band.label} dBm</span>)}</div>}
    {layer === 'variance' && availableVariance && <p>方差范围：{varianceMin.toFixed(2)}～{varianceMax.toFixed(2)} dB²（蓝 → 红）；越大表示估计越不确定，空白表示未返回方差。</p>}
    <div className="ain-map-footer-line"><span>▲ AP · ● UE · 金色圈为选中对象</span>
      <details className="ain-map-explanation"><summary>图表说明</summary>
        <p>亮线为关联连接。{connectionPeriod === 'window' ? '节点采用窗口平均位置，连线为连接记录占比最高的主服务 AP；这是窗口汇总，非某一时刻拓扑。' : '节点采用最新帧实际采样位置，连线为同一帧服务关系。'}</p>
        <p>灰色虚线圈节点表示缺少对应采样，仅保留最近已知位置，不代表设备离线。坐标轴固定为场景范围。</p>
        <p>{layer === 'topology' ? '背景只展示网络结构。' : layer === 'variance' ? '方差不是信号强度。'
          : sampled || spatial?.method === 'grid_mean' ? '空白网格表示没有采样，不代表没有信号。' : '所选历史窗口的空间估计，可覆盖未采样位置。'}
          {layer !== 'topology' && ' 背景始终为全网范围；选中对象只改变高亮、指标和趋势。'}</p>
        <p>连线：{connectionPeriod === 'latest' ? '最新帧实际服务关系与采样值' : '窗口主服务关系与连接均值，非同一时刻拓扑'}。</p>
      </details></div>
  </>}>
    <div className="ain-observation-map-controls">
      <div className="ain-observation-tabs" aria-label="空间背景图层">
        {([['topology', '网络拓扑'], ['sampled', '采样信号'], ['estimated', '空间估计'], ['variance', '估计方差']] as [Layer, string][]).map(([value, label]) =>
          <button key={value} type="button" aria-pressed={layer === value} onClick={() => setLayer(value)}>{label}</button>)}
      </div>
      <div className="ain-observation-map-options">
        <label><input type="checkbox" checked={connections} onChange={event => setConnections(event.target.checked)} />显示连接</label>
        <label>连接口径<select value={connectionPeriod} onChange={event => setConnectionPeriod(event.target.value as 'latest' | 'window')}>
          <option value="window">窗口主连接（稳定概览）</option><option value="latest">最新帧连接（实际切换）</option>
        </select></label>
        <span>位置：{connectionPeriod === 'window' ? '窗口平均' : '最新采样'}</span>
        <span>关注：{targetLabel(target)}</span>
      </div>
    </div>
    {missingNodes > 0 && <p className="ain-observation-map-notice" role="status">{missingNodes} 个 UE 在{connectionPeriod === 'window' ? '本窗口' : '最新帧'}无采样，灰色标记保留最近已知位置；未补造指标或服务连接。</p>}
    {(outsideNodes > 0 || outsideCells > 0) && <p className="ain-observation-map-notice" role="status">{outsideNodes > 0 && `${outsideNodes} 个节点超出场景边界；`}{outsideCells > 0 && `${outsideCells} 个信号网格超出边界并裁剪；`}请核对场景坐标配置，画布不随异常坐标缩放。</p>}
    {(layer === 'estimated' || layer === 'variance') && <>
      {spatialNotice && <p className="ain-observation-map-notice" role="status">{spatialNotice}</p>}
      {pending && <p className="ain-observation-map-notice" role="status">空间结果加载中{layer === 'estimated' ? '，暂显示采样信号' : ''}…</p>}
      {error && <div className="ain-observation-map-notice" role="alert">空间结果加载失败：{error}。{layer === 'estimated' && '暂显示采样信号。'}<button className="ain-button" type="button" onClick={onRetry}>重试空间结果</button></div>}
      {!spatialNotice && !pending && !error && layer === 'estimated' && (!spatial || !spatial.points.length) && <p className="ain-observation-map-notice">空间接口没有可绘制点位，暂显示采样信号。</p>}
      {!spatialNotice && !pending && !error && layer === 'variance' && !availableVariance && <p className="ain-observation-map-notice" role="status">{spatial?.varianceNotice || '当前没有可用估计方差，仍可查看位置和连接。'}</p>}
      {layer === 'estimated' && spatial?.method === 'grid_mean' && <p className="ain-observation-map-notice">接口返回的是采样网格均值，本次未生成 Kriging 空间估计。</p>}
    </>}
    {projection ? <svg className="ain-spatial-svg" viewBox={SCENE_VIEWBOX} role="group" aria-label="融合位置、连接与信号图层的网络空间图">
      <defs><clipPath id="ain-observation-plot-clip"><rect x={projection.left} y={projection.top} width={projection.width} height={projection.height} /></clipPath></defs>
      <g clipPath="url(#ain-observation-plot-clip)">{cells.map(cell => <rect key={`${cell.x},${cell.y}`} x={projection.x(cell.x)} y={projection.y(cell.y + cell.height)}
        width={projection.x(cell.x + cell.width) - projection.x(cell.x)} height={projection.y(cell.y) - projection.y(cell.y + cell.height)}
        fill={color(cell.value)} opacity={0.68} stroke="#22364c" strokeWidth={0.4}><title>{cell.detail}</title></rect>)}</g>
      <SceneAxes area={area} projection={projection} />
      {connections && links.map(link => {
        const source = byId.get(link.source), end = byId.get(link.target);
        if (!source || !end || !projection.contains(source) || !projection.contains(end)) return null;
        const active = isRelatedLink(link, target);
        const title = `${link.source} → ${link.target}：RSRP ${displayMetric(link.rsrp)} dBm / SINR ${displayMetric(link.sinr)} dB / 吞吐量 ${displayMetric(link.throughput)} Mbps（${connectionPeriod === 'latest' ? '最新帧' : '窗口均值'}）`;
        return <g key={link.id} className={`ain-observation-link${active ? ' is-related' : ''}`} role="button" tabIndex={0}
          aria-label={`查看连接 ${link.source} → ${link.target}`} aria-pressed={selectedLink?.id === link.id}
          onClick={() => onSelectLink(link)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelectLink(link); } }}>
          <title>{title}</title>
          <line x1={projection.x(source.x)} y1={projection.y(source.y)} x2={projection.x(end.x)} y2={projection.y(end.y)}
            stroke={selectedLink?.id === link.id ? '#ffbb4a' : active ? '#a9eeff' : '#6c8aac'} strokeWidth={active ? 2.4 : 1.2} opacity={active ? 0.95 : 0.3} />
          <line className="ain-observation-link-hit" x1={projection.x(source.x)} y1={projection.y(source.y)} x2={projection.x(end.x)} y2={projection.y(end.y)} />
        </g>;
      })}
      {nodes.filter(node => projection.contains(node)).map(node => <SceneNode key={node.id} node={node} projection={projection}
        selected={target.kind !== 'all' && node.id === target.id} highlighted={target.kind !== 'all' && related.has(node.id)}
        onSelect={item => onSelect({ kind: item.type, id: item.id })} />)}
    </svg> : <p className="ain-spatial-empty">坐标范围无效，无法绘制。</p>}
  </SpatialChart>;
}
