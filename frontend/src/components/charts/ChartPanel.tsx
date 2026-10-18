/* eslint-disable react-refresh/only-export-components -- 共用图表组件沿用统一导出的轴样式常量。 */
import { useEffect, useRef, type CSSProperties } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, HeatmapChart, GraphChart } from "echarts/charts";
import { GridComponent, TooltipComponent, VisualMapComponent, MarkLineComponent, MarkAreaComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import type { EChartsOption } from "echarts";
import "./ChartPanel.css";

echarts.use([
  BarChart,
  LineChart,
  HeatmapChart,
  GraphChart,
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
  MarkLineComponent,
  MarkAreaComponent,
  SVGRenderer,
]);

interface Props {
  title: string;
  description: string;
  option: EChartsOption;
  height?: number;
  className?: string;
  onSelect?: (ueId: string) => void;
}
export default function ChartPanel({
  title,
  description,
  option,
  height = 190,
  className = "",
  onSelect,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<ReturnType<typeof echarts.init> | null>(null);
  useEffect(() => {
    if (!container.current) return;
    const instance = echarts.init(container.current, null, { renderer: "svg" });
    chart.current = instance;
    const observer = new ResizeObserver(() => {
      const element = container.current;
      if (!instance.isDisposed() && element?.isConnected && element.clientWidth > 0 && element.clientHeight > 0)
        instance.resize();
    });
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);
  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true });
  }, [option]);
  useEffect(() => {
    const instance = chart.current;
    if (!instance || !onSelect) return;
    const handleClick = (event: { name?: string }) => {
      if (event.name) onSelect(event.name);
    };
    instance.on("click", "series.bar", handleClick);
    return () => {
      if (!instance.isDisposed()) instance.off("click", handleClick);
    };
  }, [onSelect]);
  return (
    <figure className={`ain-chart-slot ain-real-chart ${className}`}>
      <figcaption className="ain-slot-heading">
        <h2>{title}</h2>
        <p>{description}</p>
      </figcaption>
      <div
        ref={container}
        className="ain-echart"
        style={{ height, "--ain-chart-height": `${height}px` } as CSSProperties}
        role="img"
        aria-label={`${title}：${description}`}
      />
    </figure>
  );
}
// 第二批：统一图表字体，分别提供深色画布与白底画布的轴样式。
export const CHART_FONT = '"Microsoft YaHei", "PingFang SC", Arial, sans-serif';
export const AXIS_STYLE = {
  axisLine: { lineStyle: { color: "#4b6383" } },
  axisTick: { show: false },
  axisLabel: { color: "#c3d5ee", fontSize: 11, fontFamily: CHART_FONT },
  splitLine: { lineStyle: { color: "#293b55", width: 1 } },
};
export const LIGHT_AXIS_STYLE = {
  axisLine: { lineStyle: { color: "#bfd4ed" } },
  axisTick: { show: false },
  axisLabel: { color: "#47658e", fontSize: 11, fontFamily: CHART_FONT },
  splitLine: { lineStyle: { color: "#e8f0fa", width: 1 } },
};
export const CHART_BACKGROUND = "#102036";
