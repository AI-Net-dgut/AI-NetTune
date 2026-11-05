import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { EChartsOption } from "echarts";
import type { ChatResult, DashboardData, Diagnosis, MetricKey, SpatialMapData } from "../types/backend";
import { METRIC_META } from "../types/backend";
import { binObservationTrend, latestObservationLinks, observationTrend, observationMeans, targetLabel, uniqueObservationRecords } from "../services/dataObservation";
import type { ObservationTarget } from "../services/dataObservation";
import { displayMetric } from "../services/metrics";
import { getSpatialArea } from "../services/spatialMap";
import ChartPanel, { CHART_FONT, LIGHT_AXIS_STYLE } from "../components/charts/ChartPanel";
import SpatialChart, { createProjection, SceneAxes, SceneNode, SCENE_VIEWBOX } from "../components/charts/SpatialChart";
import ToolResultPanel from "../components/analysis/ToolResultPanel";
import { diagnosisMetric, sameQueryScope } from "./diagnosisModel";
import { scopeLabel } from "../services/backend";
import { toolLineMetadata } from "../components/analysis/toolChartMetadata";

const clock = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const RAW_METRICS: Partial<Record<MetricKey, string>> = {
  rsrpDbm: "rsrp", sinrDb: "sinr", throughputMbps: "throughput", resourceUtilizationPct: "prb_usage", packetLossPct: "packet_loss",
};

export type EvidenceView = "trend" | "coverage" | "connections" | "comparison";
export type EvidenceSelection = { id: number; view: EvidenceView; objectId?: string; metric?: MetricKey };

function EvidenceMap({ data, spatial, target, onSelect, connections }: {
  data: DashboardData; spatial: SpatialMapData | null; target: ObservationTarget; connections: boolean;
  onSelect: (target: ObservationTarget) => void;
}) {
  const clipId = useId().replace(/:/g, "");
  const base = spatial ? getSpatialArea(spatial, data.area) : data.area;
  const area = { ...base, xMax: Math.max(base.xMax, data.area.xMax + data.gridSize),
    yMax: Math.max(base.yMax, data.area.yMax + data.gridSize) };
  const projection = createProjection(area);
  const links = latestObservationLinks(data.metrics);
  const byId = new Map(data.nodes.map(node => [node.id, node]));
  const cells = spatial?.points.length ? spatial.points.map(point => ({
    x: point.x - spatial.cellSize.x / 2, y: point.y - spatial.cellSize.y / 2,
    width: spatial.cellSize.x, height: spatial.cellSize.y, value: point.value,
  })) : data.cells.map(cell => ({ ...cell, width: data.gridSize, height: data.gridSize }));
  const bands = ["#67768a", "#2e69d2", "#36b7f3", "#27d7d2", "#58edc6"];
  const latestSampled = !spatial?.points.length && data.sampledFrameTime !== undefined;
  return <SpatialChart title={connections ? "最新采样连接关系" : "覆盖热力图"} description={connections ? "最新帧服务连接 · 选中对象高亮" : `${spatial?.method === "kriging" ? "历史空间估计" : latestSampled ? "最新帧采样网格" : "历史采样网格"} · RSRP`}
    className="ain-ai-evidence-map" footer={<>{!connections && <div className="ain-ai-signal-key"><span>RSRP / dBm</span>
      {["< −105", "−105～−95", "−95～−85", "−85～−75", "≥ −75"].map((label, i) =>
        <span key={label}><i style={{ background: bands[i] }} />{label}</span>)}</div>}
      <details className="ain-map-explanation"><summary>图表说明</summary><p>{connections ? "连线表示最新采样帧的服务关系；点击节点高亮相关连接。" : latestSampled
        ? "背景为冻结分析时刻之前的最新采样帧；空白表示没有采样。研判的窗口指标另见趋势与文字证据。"
        : "背景为窗口统计或估计；空白表示没有数据。点击节点选择观察对象。"}</p></details>
      <p>最新采样：{data.metrics.at(-1)?.backendTime}（北京时间）</p></>}>
    {projection ? <svg className="ain-spatial-svg" viewBox={SCENE_VIEWBOX} role="group" aria-label="研判辅助覆盖与连接图">
      <defs><clipPath id={clipId}><rect x={projection.left} y={projection.top} width={projection.width} height={projection.height} /></clipPath></defs>
      <g clipPath={`url(#${clipId})`}>{!connections && cells.map(cell => <rect key={`${cell.x},${cell.y}`}
        x={projection.x(cell.x)} y={projection.y(cell.y + cell.height)}
        width={projection.x(cell.x + cell.width) - projection.x(cell.x)} height={projection.y(cell.y) - projection.y(cell.y + cell.height)}
        fill={bands[cell.value < -105 ? 0 : cell.value < -95 ? 1 : cell.value < -85 ? 2 : cell.value < -75 ? 3 : 4]} opacity={0.75}>
        <title>{`RSRP ${displayMetric(cell.value)} dBm；位置 (${cell.x}, ${cell.y}) m`}</title></rect>)}</g>
      <SceneAxes area={area} projection={projection} />
      {connections && links.map(link => {
        const start = byId.get(link.source), end = byId.get(link.target);
        if (!start || !end || !projection.contains(start) || !projection.contains(end)) return null;
        const related = target.kind === "all" || (target.kind === "ap" ? target.id === link.source : target.id === link.target);
        return <line key={link.id} x1={projection.x(start.x)} y1={projection.y(start.y)}
          x2={projection.x(end.x)} y2={projection.y(end.y)} stroke={related ? "#ffca6b" : "#8daec5"}
          strokeWidth={related ? 2.5 : 1} opacity={related ? 0.9 : 0.4}>
          <title>{`${link.source} → ${link.target}；RSRP ${displayMetric(link.rsrp)} dBm；吞吐量 ${displayMetric(link.throughput)} Mbps（最新帧）`}</title></line>;
      })}
      {data.nodes.filter(node => projection.contains(node)).map(node => <SceneNode key={node.id} node={node}
        projection={projection} selected={target.kind !== "all" && target.id === node.id}
        onSelect={item => onSelect({ kind: item.type, id: item.id })} />)}
    </svg> : <p className="ain-spatial-empty">当前坐标范围无法绘制地图。</p>}
  </SpatialChart>;
}

export default function DiagnosisCharts({ data, diagnosis, toolResult, spatial, spatialPending, spatialError, onSpatialRetry, busy, onTool, selection = null }: {
  data: DashboardData; diagnosis: Diagnosis | null; toolResult: ChatResult | null; spatial: SpatialMapData | null;
  spatialPending: boolean; spatialError: string; onSpatialRetry: () => void; busy: boolean;
  onTool: (type: "heatmap" | "topology") => void; selection?: EvidenceSelection | null;
}) {
  const evidenceRef = useRef<HTMLElement>(null);
  const selectionId = selection?.id;
  useEffect(() => {
    // 上方入口会通过 key 重建此面板。切换后把图表带入当前滚动区域，
    // 也移动键盘焦点；普通加载或手动切换标签时不抢占滚动位置。
    if (selectionId == null) return;
    evidenceRef.current?.focus({ preventScroll: true });
    evidenceRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [selectionId]);
  const targetAps = diagnosis?.answerFocus?.targetAps.length ? diagnosis.answerFocus.targetAps
    : diagnosis?.answerFocus?.observedAps ?? diagnosis?.affectedAps;
  const firstAp = targetAps?.find(id => data.nodes.some(node => node.type === "ap" && node.id === id));
  const selectedNode = data.nodes.find(n => n.id === selection?.objectId);
  const [target, setTarget] = useState<ObservationTarget>(selectedNode ? { kind: selectedNode.type, id: selectedNode.id }
    : firstAp ? { kind: "ap", id: firstAp } : { kind: "all" });
  const [metric, setMetric] = useState<MetricKey>(selection?.metric ?? (diagnosis ? diagnosisMetric(diagnosis) : "throughputMbps"));
  const toolView: EvidenceView | null = toolResult?.chart?.type === "heatmap" ? "coverage" : toolResult?.chart?.type === "graph" ? "connections" : toolResult?.chart?.type === "line" ? "trend" : null;
  const [view, setView] = useState<EvidenceView>(selection?.view ?? (diagnosis?.answerFocus?.kind === "connections" ? "connections" : toolView) ?? "trend");
  // 原始趋势没有逐点对象标识，默认用已加载采样完成对象/指标联动。
  const [useTool, setUseTool] = useState(Boolean(!selection && toolView && toolView !== "trend" && !toolResult?.diagnosis));
  const [compareKind, setCompareKind] = useState<"ap" | "ue">("ue");
  const raw = useMemo(() => observationTrend(data.metrics, target, metric), [data.metrics, target, metric]);
  const points = useMemo(() => binObservationTrend(raw, 240), [raw]);
  const meta = METRIC_META[metric];
  const diagnosisAp = diagnosis?.affectedAps.length === 1 ? diagnosis.affectedAps[0] : null;
  const threshold = target.kind === "ap" && target.id === diagnosisAp
    ? diagnosis?.evidence.find(item => item.metric === RAW_METRICS[metric])?.threshold : undefined;
  const hasThreshold = Number.isFinite(threshold);
  const option: EChartsOption = {
    animation: false, backgroundColor: "#fff", textStyle: { fontFamily: CHART_FONT },
    // containLabel 在 ECharts 6 已弃用（老算法只按样本标签估算留白），
    // 换成等价的新机制；contain: 'all' 还会把**轴名**（如 Mbps）一起兜住 ——
    // 正是这张图的 x 轴单位被卡片右边缘裁成 "MI" 的原因。
    grid: { left: 8, right: 14, top: 26, bottom: 8, outerBoundsMode: "same", outerBoundsContain: "all" },
    tooltip: { trigger: "axis", renderMode: "richText", formatter: (params: unknown) => {
      const first = (Array.isArray(params) ? params[0] : params) as { dataIndex?: number } | undefined;
      const point = points[first?.dataIndex ?? -1];
      return point ? `${clock.format(point.time)}（北京时间）\n${targetLabel(target)} · ${meta.label}：${displayMetric(point.value ?? undefined)} ${meta.unit}${hasThreshold ? `\n后端阈值：${displayMetric(threshold)} ${meta.unit}` : ""}` : "暂无有效采样";
    } },
    xAxis: { ...LIGHT_AXIS_STYLE, type: "time", splitNumber: 4, axisLabel: { ...LIGHT_AXIS_STYLE.axisLabel,
      formatter: (value: number) => clock.format(value), hideOverlap: true } },
    yAxis: { ...LIGHT_AXIS_STYLE, type: "value", scale: true, name: meta.unit, nameTextStyle: { color: "#627a94" }, splitNumber: 3 },
    series: [{ type: "line", name: meta.label, connectNulls: false, showSymbol: points.length < 40, symbolSize: 4,
      lineStyle: { color: "#367deb", width: 2 }, itemStyle: { color: "#367deb" }, data: points.map(point => [point.time, point.value]) },
      ...(hasThreshold ? [{ type: "line" as const, name: "后端阈值", showSymbol: false, silent: true,
        lineStyle: { color: "#d69540", type: "dashed" as const, width: 1.4 }, data: points.map(point => [point.time, threshold]) }] : [])],
  };
  const uniqueRecords = useMemo(() => uniqueObservationRecords(data.metrics), [data.metrics]);
  const comparisons = data.nodes.filter(node => node.type === compareKind).map(node => ({ id: node.id,
    value: observationMeans(uniqueRecords.filter(row => compareKind === "ap" ? row.apId === node.id : row.ueId === node.id))[metric] }));
  const lowFirst = ["rsrpDbm", "sinrDb", "throughputMbps"].includes(metric);
  const ordered = [...comparisons].sort((a, b) => a.value === null ? b.value === null ? 0 : 1 : b.value === null ? -1 : lowFirst ? a.value - b.value : b.value - a.value);
  const visibleComparisons = ordered.slice(0, 10);
  const focused = target.kind !== "all" && target.kind === compareKind ? ordered.find(item => item.id === target.id) : null;
  if (focused && !visibleComparisons.some(item => item.id === focused.id)) visibleComparisons[visibleComparisons.length - 1] = focused;
  const compareOption: EChartsOption = { animation: false, backgroundColor: "#fff", grid: { left: 8, right: 20, top: 24, bottom: 8, outerBoundsMode: "same", outerBoundsContain: "all" },
    tooltip: { trigger: "item", renderMode: "richText" }, xAxis: { ...LIGHT_AXIS_STYLE, type: "value", name: meta.unit, scale: true },
    yAxis: { ...LIGHT_AXIS_STYLE, type: "category", inverse: true, data: visibleComparisons.map(item => item.id) },
    series: [{ type: "bar", barMaxWidth: 16, data: visibleComparisons.map(item => ({ name: item.id, value: item.value,
      itemStyle: { color: target.kind !== "all" && target.id === item.id ? "#e7a34b" : "#78a8ec", borderRadius: [0, 3, 3, 0] } })) }] };
  const map = spatial && sameQueryScope(data.scope, spatial.scope) ? spatial : null;
  const usingTool = Boolean(toolView === view && useTool && toolResult?.chart);
  const originalMetric = toolResult?.chart?.type === "line" ? toolLineMetadata(toolResult.chart.option).label
    : toolResult?.chart?.type === "heatmap" ? "RSRP / dBm" : "窗口连接关系";
  return <section ref={evidenceRef} tabIndex={-1} style={{ scrollMarginTop: 16 }} className="ain-ai-evidence" aria-label="辅助判断图表">
    <div className="ain-ai-subheading"><h4>研判证据图</h4><div className="ain-ai-chart-actions">
      {([['trend', '指标趋势'], ['coverage', '覆盖热力图'], ['connections', '连接关系'], ['comparison', '对象对比']] as const).map(([key, label]) =>
        <button key={key} className="ain-button" type="button" aria-pressed={view === key} onClick={() => setView(key)}>{label}</button>)}
    </div></div>
    <p className="ain-evidence-purpose">{view === "trend" ? "核查异常出现的时刻与变化趋势。" : view === "coverage" ? "定位弱信号集中区域，辅助核查覆盖原因。" : view === "connections" ? "核查用户与 AP 的服务关系；判断拥塞还需结合负载指标。" : "比较同一分析窗口中的用户或 AP，选中对象高亮。"}</p>
    <div className="ain-ai-chart-controls">
      {!usingTool && <label>对象<select value={target.kind === "all" ? "all" : `${target.kind}:${target.id}`}
        onChange={event => { const value = event.target.value; setTarget(value === "all" ? { kind: "all" } : { kind: value.startsWith("ap:") ? "ap" : "ue", id: value.slice(3) }); }}>
        <option value="all">全网</option>{data.nodes.map(node => <option key={node.id} value={`${node.type}:${node.id}`}>{node.id}</option>)}</select></label>}
      {!usingTool && (view === "trend" || view === "comparison") && <label>指标<select value={metric} onChange={event => setMetric(event.target.value as MetricKey)}>
        {(Object.keys(METRIC_META) as MetricKey[]).map(key => <option key={key} value={key}>{METRIC_META[key].label} / {METRIC_META[key].unit}</option>)}</select></label>}
      {view === "comparison" && <label>比较<select value={compareKind} onChange={event => setCompareKind(event.target.value as "ap" | "ue")}>
        <option value="ue">用户</option><option value="ap">AP</option></select></label>}
      {toolView === view && <label>数据<select value={useTool ? "tool" : "sample"} onChange={event => setUseTool(event.target.value === "tool")}>
        <option value="sample">已加载采样（可联动）</option><option value="tool">助手原始图（固定）</option></select></label>}
      {view === "coverage" && <button type="button" className="ain-ai-text-button" disabled={busy} onClick={() => onTool("heatmap")}>查询范围热力图</button>}
      {view === "connections" && <button type="button" className="ain-ai-text-button" disabled={busy} onClick={() => onTool("topology")}>查询范围连接</button>}
    </div>
    {usingTool && <p className="ain-ai-note">原始图：{originalMetric}；不按所选对象或指标重新查询。切回“已加载采样”可联动选择。</p>}
    {!usingTool && <p className="ain-ai-note">采样范围：{scopeLabel(data.scope)}{view === "trend" || view === "comparison" ? ` · ${targetLabel(target)} · ${meta.label} / ${meta.unit}` : " · 全范围背景，选中节点仅高亮"}</p>}
    {usingTool && toolResult?.chart ? <ToolResultPanel result={toolResult} compact />
      : view === "coverage" || view === "connections" ? <><EvidenceMap data={data} spatial={map} target={target} onSelect={setTarget} connections={view === "connections"} />
        {view === "coverage" && spatialPending && <p className="ain-ai-note" role="status">空间结果加载中，暂显示采样网格。</p>}
        {view === "coverage" && spatialError && <p className="ain-ai-note">空间结果暂不可用，显示采样网格。<button type="button" className="ain-ai-text-button" onClick={onSpatialRetry}>重试</button></p>}</>
      : view === "comparison" ? <>{visibleComparisons.some(item => item.value !== null) ? <ChartPanel title={`${compareKind === "ue" ? "用户" : "AP"} · ${meta.label}对比`} description="窗口采样均值 · 金色表示选中对象；无数据保留缺失"
        option={compareOption} height={Math.min(280, Math.max(180, visibleComparisons.length * 23 + 45))}
        onSelect={id => setTarget({ kind: compareKind, id })} /> : <p className="ain-ai-empty" role="status">当前比较对象暂无有效 {meta.label} 采样，请切换指标或范围。</p>}<p className="ain-ai-note">显示 {visibleComparisons.length}/{comparisons.length} 个对象，按指标{lowFirst ? "从低到高" : "从高到低"}排列并保留选中对象。吞吐量为采样均值；点击条形高亮。</p></>
      : <>{points.some(point => point.value !== null) ? <ChartPanel title={`${targetLabel(target)} · ${meta.label}趋势`} description={`${raw.length} 个采样时刻${points.length < raw.length ? `，压缩为 ${points.length} 个区间均值` : ""}${hasThreshold ? ` · 虚线：后端阈值 ${displayMetric(threshold)} ${meta.unit}` : ""}`}
        option={option} height={190} /> : <p className="ain-ai-empty" role="status">{targetLabel(target)} 在当前范围暂无有效 {meta.label} 采样，请切换对象、指标或范围。</p>}<p className="ain-ai-note">{target.kind === "ue" ? "单用户采样值" : "每个时刻关联用户的采样均值"}；无记录保留缺口，吞吐量不是 AP 总吞吐量。曲线用于辅助核查，故障起止时间仍需后端明确。</p></>}
  </section>;
}
