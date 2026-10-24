import type { EChartsOption } from "echarts";
import type { ChatResult } from "../../types/backend";
import { scopeLabel } from "../../services/backend";
import ChartPanel, {
  AXIS_STYLE,
  CHART_BACKGROUND,
} from "../charts/ChartPanel";
import { toolLineMetadata } from "./toolChartMetadata";
import "./ToolResultPanel.css";

export default function ToolResultPanel({
  result,
  compact = false,
}: {
  result: ChatResult | null;
  compact?: boolean;
}) {
  if (!result?.chart) return null;

  const raw = result.chart.option;

  let option: EChartsOption | null = null;
  let title = "";
  let description = "";
  let hasData = false;
  let provenance = "";
  let unavailableReason = "";

  if (raw && result.chart.type === "heatmap") {
    title = "信号热力图";
    description = "UE 采样网格均值；空白网格无数据";
    hasData = Array.isArray(raw.series) && raw.series.some(item => {
      const values = (item as { data?: unknown })?.data;
      return Array.isArray(values) && values.some(value => Array.isArray(value) && value[2] !== null && value[2] !== '' && Number.isFinite(Number(value[2])));
    });

    option = {
      ...raw,
      animation: false,
      backgroundColor: CHART_BACKGROUND,
      tooltip: {
        trigger: "item",
        renderMode: "richText",
        formatter: (item: unknown) => {
          const values = (item as { value?: unknown }).value;

          if (!Array.isArray(values)) return "无数据";

          const value = Number(values[2]);

          return `网格索引 (${values[0]}, ${values[1]})\nRSRP ${
            Number.isFinite(value) ? value.toFixed(1) : "—"
          } dBm`;
        },
      },
      xAxis: {
        ...(raw.xAxis as object),
        ...AXIS_STYLE,
      },
      yAxis: {
        ...(raw.yAxis as object),
        ...AXIS_STYLE,
      },
      visualMap: {
        ...(raw.visualMap as object),
        textStyle: { color: "#b5c7d5" },
      },
    } as EChartsOption;
  
  } else if (raw && result.chart.type === "line") {
    const metadata = toolLineMetadata(raw);
    title = `助手原始趋势 · ${metadata.label}`;
    description = "按小时聚合的网络指标变化 · 历史数据";
    provenance = "对象：后端未明确各趋势点对应的 AP 或 UE；此图仅作原始辅助观察，不随对象和指标选择变化。";
    if (metadata.hasMixedUnits) unavailableReason = "原始图包含不同单位，暂不共用纵轴展示；请切回已加载采样，分别选择指标。";

    // 读取后端返回的横轴时间
    const rawXAxis = raw.xAxis as
      { data?: unknown } | undefined;

    const hours = Array.isArray(rawXAxis?.data)
      ? rawXAxis.data.map(String)
      : [];

    // 读取后端返回的指标数据
    const rawSeries = Array.isArray(raw.series)
      ? raw.series
      : [];

    const lines = rawSeries.map(item => {
      const s = (item && typeof item === "object"
        ? item
        : {}) as {
          name?: unknown;
          data?: unknown;
        };

      return {
        name: typeof s.name === "string"
          ? s.name
          : "网络指标",
        type: "line" as const,
        smooth: false,
        showSymbol: true,
        symbolSize: 5,
        lineStyle: {
          width: 2,
          color: "#72e6f3",
        },
        itemStyle: {
          color: "#72e6f3",
        },
        data: Array.isArray(s.data)
          ? s.data.map(value => {
              if (
                value === null ||
                value === undefined ||
                value === ""
              ) {
                return null;
              }
              const n = Number(value);
              return Number.isFinite(n) ? n : null;
            })
          : [],
      };
    });
    hasData = lines.some(line => line.data.some(value => value !== null));

    // 将数据转换成 ECharts 折线图配置
    option = {
      animation: false,
      backgroundColor: CHART_BACKGROUND,
      grid: {
        left: 24,
        right: 30,
        top: 35,
        bottom: 30,
        // ECharts 6：containLabel 已弃用，等价新写法；'all' 同时兜住轴名（单位）
        outerBoundsMode: "same",
        outerBoundsContain: "all",
      },
      tooltip: {
        trigger: "axis",
        renderMode: "richText",
      },
      xAxis: {
        type: "category",
        data: hours,
        ...AXIS_STYLE,
        axisLabel: {
          ...AXIS_STYLE.axisLabel,
          hideOverlap: true,
          formatter: (value: string) =>
            value.length >= 13 ? value.slice(5) : value,
        },
      },
      yAxis: {
        type: "value",
        scale: true,
        name: metadata.unit,
        nameTextStyle: {
          color: "#c3d5ee",
        },
        ...AXIS_STYLE,
      },
      series: lines,
    } as EChartsOption;

  } else if (raw && result.chart.type === "graph") {

    title = "连接关系汇总";
    description =
      "全工具时间段内出现过的服务连接；非同一时刻拓扑";

    const nodes = Array.isArray(raw.nodes)
      ? (raw.nodes as { id: string; category: string }[])
      : [];
    hasData = nodes.length > 0;

    option = {
      animation: false,
      backgroundColor: CHART_BACKGROUND,
      tooltip: { renderMode: "richText" },
      series: [
        {
          type: "graph",
          layout: "force",
          roam: true,
          force: { repulsion: 220 },
          label: {
            show: true,
            color: "#d8e9ed",
            position: "right",
          },
          lineStyle: {
            color: "#718b9d",
            width: 2,
          },
          data: nodes.map((node) => ({
            id: node.id,
            name: node.id,
            symbolSize: node.category === "AP" ? 28 : 18,
            itemStyle: {
              color:
                node.category === "AP"
                  ? "#7ec7bc"
                  : "#8eb8d0",
            },
          })),
          links: Array.isArray(raw.links)
            ? (raw.links as {
                source: string;
                target: string;
              }[])
            : [],
        },
      ],
    };
  }

  return (
    <section
      className="ain-tool-result ain-tool-result-v2"
      id="ain-tool-result"
      aria-labelledby={compact ? undefined : "ain-tool-result-title"}
      aria-label={compact ? `工具返回：${title || result.chart.type || "未知图表"}` : undefined}
    >
      {!compact && <div className="ain-tool-heading">
        <h2 id="ain-tool-result-title">工具返回图表</h2>
        <span>
          {title || result.chart.type || "未知图表"}
        </span>
      </div>}

      <p className="ain-tool-scope">
        返回范围：{scopeLabel(result.scope)}
      </p>
      {provenance && <p className="ain-tool-scope">{provenance}</p>}

      {option && hasData && !unavailableReason ? (
        <ChartPanel
          title={title}
          description={description}
          option={option}
          height={compact ? result.chart.type === "line" ? 190 : 220 : result.chart.type === "line" ? 220 : 340}
        />

      ) : unavailableReason ? <p className="ain-tool-unsupported" role="status">{unavailableReason}</p>
        : option ? <p className="ain-tool-unsupported" role="status">助手返回暂无有效图表数据，请切回已加载采样或更换范围。</p> : (
        <p className="ain-tool-unsupported" role="status">
          已收到图表数据，当前页面暂不支持展示此类型：
          {result.chart.type}
        </p>
      )}
    </section>
  );
}
