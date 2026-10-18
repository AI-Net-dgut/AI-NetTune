import type { EChartsOption } from "echarts";
import { toEpoch } from "../services/backend";
import type { ForecastResult, SpectrumForecast, ImpactForecast } from "./types";
import type { TaggedSpectrumFrame } from "./spectrumViews";

const palette = [[33, 69, 209], [45, 142, 240], [58, 214, 220], [213, 231, 91], [255, 179, 63], [226, 79, 67]];
export function spectrumColor(value: number | null, min: number, max: number) {
  if (value === null) return "#e8edf4";
  const position = Math.max(0, Math.min(1, (value - min) / (max - min))) * (palette.length - 1);
  const index = Math.min(palette.length - 2, Math.floor(position)), mix = position - index;
  return `rgb(${palette[index].map((n, i) => Math.round(n + (palette[index + 1][i] - n) * mix)).join(",")})`;
}
export function spectrumCells(result: ForecastResult, spectrum: SpectrumForecast) {
  return (["history", "predicted"] as const).flatMap(kind => spectrum[kind].flatMap(frame => spectrum.bands.map((band, i) => ({
    kind, ...band, start: frame.start, end: frame.end, value: frame.values[i],
    fill: spectrumColor(frame.values[i], spectrum.scale.min, spectrum.scale.max),
    label: `${kind === "history" ? "历史观测" : "未来预测"} · ${frame.start}—${frame.end}\n${spectrum.observationLabel} · ${band.label} ${band.lowerMHz}—${band.upperMHz} MHz\n${frame.values[i] === null ? "暂无数据" : `${frame.values[i]} ${spectrum.unit}`} · 基准 ${result.baseTime}`,
  }))));
}
export function spectrumCurveOption(result: ForecastResult, spectrum: SpectrumForecast, frame: TaggedSpectrumFrame, bandIds: string[] = []): EChartsOption {
  const points: { value: [number, number | null]; label: string }[] = [];
  spectrum.bands.forEach((band, i) => {
    const previous = spectrum.bands[i - 1];
    if (previous && previous.upperMHz < band.lowerMHz) points.push({ value: [(previous.upperMHz + band.lowerMHz) / 2000, null], label: "未提供该频率范围的数据" });
    points.push({ value: [(band.lowerMHz + band.upperMHz) / 2000, frame.values[i]],
      label: `${band.label} · ${band.lowerMHz}—${band.upperMHz} MHz\n频段接收功率：${frame.values[i] === null ? "暂无数据" : `${frame.values[i]} dBm`}` });
  });
  const values = frame.values.filter((v): v is number => v !== null);
  const color = frame.kind === "predicted" ? "#2779e9" : "#279c91";
  return {
    animation: false, backgroundColor: "#fff", textStyle: { fontFamily: "Arial, Microsoft YaHei, sans-serif" },
    grid: { left: 50, right: 23, top: 24, bottom: 35 },
    tooltip: { trigger: "axis", renderMode: "richText", formatter: (params: unknown) => {
      const first = (Array.isArray(params) ? params[0] : params) as { dataIndex?: number } | undefined;
      return `${frame.kind === "predicted" ? "未来预测" : "历史观测"} · ${frame.start}—${frame.end}\n${spectrum.observationLabel}\n${points[first?.dataIndex ?? -1]?.label ?? "暂无数据"}${result.source === "demo" ? "\n示例数据" : ""}`;
    } },
    xAxis: { type: "value", name: "GHz", nameLocation: "end", nameTextStyle: { color: "#617a96", padding: [25, 0, 0, -12] },
      min: spectrum.bands[0].lowerMHz / 1000, max: spectrum.bands.at(-1)!.upperMHz / 1000, splitNumber: 5,
      axisLine: { lineStyle: { color: "#cfdded" } }, axisTick: { show: false }, splitLine: { lineStyle: { color: "#eef3f9" } },
      axisLabel: { color: "#607995", fontSize: 10, hideOverlap: true, formatter: (n: number) => Number(n.toFixed(3)).toString() } },
    yAxis: { type: "value", name: "dBm", nameTextStyle: { color: "#617a96" }, min: Math.min(spectrum.scale.min, ...values), max: Math.max(spectrum.scale.max, ...values), splitNumber: 4,
      splitLine: { lineStyle: { color: "#eaf0f7" } }, axisLabel: { color: "#607995", fontSize: 10, formatter: (n: number) => Number(n.toFixed(2)).toString() } },
    series: [{ name: "分频段接收功率", type: "line", data: points.map(p => p.value), connectNulls: false,
      showSymbol: spectrum.bands.length <= 64, symbolSize: 4, lineStyle: { color, width: 2, type: frame.kind === "predicted" ? "dashed" : "solid" }, itemStyle: { color },
      markArea: { silent: true, itemStyle: { color: "rgba(245,182,64,0.12)" }, data: spectrum.bands.filter(b => bandIds.includes(b.id)).map(b => [{ xAxis: b.lowerMHz / 1000 }, { xAxis: b.upperMHz / 1000 }]) } }],
  };
}
export function forecastTrendOption(result: ForecastResult, impact: ImpactForecast): EChartsOption {
  const base = toEpoch(result.baseTime), begin = toEpoch(result.history.start), end = toEpoch(result.forecast.end);
  const history = impact.history.map(p => [toEpoch(p.time), p.value]);
  const predicted = impact.predicted.map(p => [toEpoch(p.time), p.value]);
  if (history.at(-1)?.[0] === base && (!predicted.length || Number(predicted[0][0]) > base)) predicted.unshift(history.at(-1)!);
  const values = [...impact.history, ...impact.predicted].flatMap(p => [p.value, p.lower, p.upper]).filter((v): v is number => typeof v === "number");
  if (impact.threshold) values.push(impact.threshold.value);
  const low = values.length ? Math.min(...values) : 0, high = values.length ? Math.max(...values) : 1, pad = Math.max((high - low) * 0.12, 1);
  const axis = { axisLabel: { color: "#607995", fontSize: 10 }, axisLine: { lineStyle: { color: "#d8e4f1" } }, axisTick: { show: false }, splitLine: { lineStyle: { color: "#eaf0f7" } } };
  return {
    animation: false, backgroundColor: "#fff", textStyle: { fontFamily: "Arial, Microsoft YaHei, sans-serif" },
    grid: { left: 48, right: 22, top: 28, bottom: 29 },
    tooltip: { trigger: "axis", renderMode: "richText", formatter: (params: unknown) => {
      const first = (Array.isArray(params) ? params[0] : params) as { value?: [number, unknown] } | undefined;
      const stamp = first?.value?.[0], p = [...impact.predicted, ...impact.history].find(x => toEpoch(x.time) === stamp);
      return p ? `${p.time}（北京时间）\n${Number(stamp) > base ? "未来预测" : "历史观测"} · ${impact.label}：${p.value === null ? "暂无数据" : `${p.value} ${impact.unit}`}${p.lower != null && p.upper != null ? `\n预测区间：${p.lower}—${p.upper} ${impact.unit}` : ""}` : "暂无数据";
    } },
    xAxis: { ...axis, type: "time", min: begin, max: end, splitNumber: 5, axisLabel: { ...axis.axisLabel, hideOverlap: true,
      formatter: (n: number) => new Date(n + 8 * 3600_000).toISOString().slice(11, 16) } },
    yAxis: { ...axis, type: "value", name: impact.unit, nameTextStyle: { color: "#607995", fontSize: 10 },
      min: Math.floor((low - pad) * 10) / 10, max: Math.ceil((high + pad) * 10) / 10, splitNumber: 3,
      axisLabel: { ...axis.axisLabel, formatter: (value: number) => Number(value.toFixed(2)).toString() } },
    series: [
      { name: "预测下界", type: "line", stack: "interval", stackStrategy: "all", symbol: "none", silent: true, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 },
        data: impact.predicted.map(p => [toEpoch(p.time), p.lower ?? null]), connectNulls: false, tooltip: { show: false } },
      { name: "预测区间", type: "line", stack: "interval", stackStrategy: "all", symbol: "none", silent: true, lineStyle: { opacity: 0 }, areaStyle: { color: "#d8e7ff", opacity: 0.7 },
        data: impact.predicted.map(p => [toEpoch(p.time), p.lower != null && p.upper != null ? p.upper - p.lower : null]), connectNulls: false, tooltip: { show: false } },
      { name: "历史观测", type: "line", showSymbol: false, connectNulls: false, data: history, lineStyle: { color: "#2779e9", width: 2 }, itemStyle: { color: "#2779e9" },
        markLine: { silent: true, symbol: "none", lineStyle: { color: "#8a9eb6", type: "dashed" }, label: { color: "#607995", fontSize: 10 }, data: [
          { xAxis: base, label: { formatter: "预测起点", position: "insideEndTop" } },
          ...(impact.threshold ? [{ yAxis: impact.threshold.value, lineStyle: { color: "#e6a23c", type: "dashed" as const }, label: { formatter: `${result.source === "demo" ? "示例" : "预警"}阈值 ${impact.threshold.value}`, position: "insideEndTop" as const } }] : []),
        ] }, markArea: { silent: true, itemStyle: { color: "rgba(255,185,120,0.08)" }, data: [[{ xAxis: base }, { xAxis: end }]] } },
      { name: "未来预测", type: "line", showSymbol: false, connectNulls: false, data: predicted, lineStyle: { color: "#2779e9", width: 2, type: "dashed" }, itemStyle: { color: "#2779e9" } },
    ],
  };
}
