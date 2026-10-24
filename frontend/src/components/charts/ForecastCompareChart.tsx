import type { EChartsOption } from "echarts";
import type {
  ForecastResult,
  ForecastMetric,
} from "../../types/backend";
import ChartPanel, {
  AXIS_STYLE,
  CHART_BACKGROUND,
  CHART_FONT,
} from "./ChartPanel";
import "./ForecastCompareChart.css";

const METRICS: {
  key: ForecastMetric;
  label: string;
  unit: string;
}[] = [
  { key: "rsrp", label: "RSRP · 信号强度", unit: "dBm" },
  { key: "sinr", label: "SINR · 信号质量", unit: "dB" },
  { key: "throughput", label: "吞吐量", unit: "Mbps" },
];

function finiteValue(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : null;
}

export default function ForecastCompareChart({
  forecast,
  pending,
  error,
}: {
  forecast: ForecastResult | null;
  pending: boolean;
  error: string;
}) {
  if (!forecast && !pending && !error) return null;
  
const hasValidForecast = Boolean(
  forecast?.ues.some((ue) =>
    METRICS.some((metric) => {
      const item = forecast.series[ue]?.[metric.key];

      return (
        finiteValue(item?.true_mean) !== null &&
        finiteValue(item?.pred_mean) !== null
      );
    })
  )
);

  return (
    <section
      className="ain-forecast-section"
      id="ain-forecast-result"
      aria-labelledby="ain-forecast-title"
    >
      <div className="ain-forecast-heading">
        <div>
          <h2 id="ain-forecast-title">历史预测效果对比</h2>
          <span className="ain-forecast-badge">历史回测</span>
        </div>

        <div
          className="ain-forecast-legend"
          aria-label="图表图例"
        >
          <span>
            <i className="is-true" />
            实测
          </span>
          <span>
            <i className="is-predicted" />
            预测
          </span>
        </div>
      </div>

      {pending ? (
        <p className="ain-forecast-state" role="status">
          正在获取预测对比数据…
        </p>
      ) : error ? (
        <p className="ain-forecast-error" role="alert">
          {error}
        </p>
      ) : (
        forecast && (
          <>          
           {!forecast.ues.length || !hasValidForecast ? (
             <p className="ain-forecast-state" role="status">
               当前时间范围的历史样本不足，无法生成有效的预测对比结果。
               建议选择1至2天的历史数据后重新加载。
             </p>
           ) : (

              <div className="ain-forecast-grid">
                {METRICS.map((metric) => {
                  const trueData = forecast.ues.map((ue) =>
                    finiteValue(
                      forecast.series[ue]?.[metric.key]?.true_mean,
                    ),
                  );

                  const predData = forecast.ues.map((ue) =>
                    finiteValue(
                      forecast.series[ue]?.[metric.key]?.pred_mean,
                    ),
                  );

                  // 对外展示用有量纲的 MAE/RMSE：MAPE 对 dBm（负值、绝对值大）没有意义，
                  // 吞吐量等指标的分母很小还会炸到几百个百分点，容易误导。
                  const pick = (field: "mae" | "rmse") =>
                    forecast.ues
                      .map((ue) => finiteValue(forecast.series[ue]?.[metric.key]?.[field]))
                      .filter((value): value is number => value !== null);

                  const mean = (values: number[]) =>
                    values.length ? values.reduce((sum, value) => sum + value / values.length, 0) : null;

                  const maeValues = pick("mae"), rmseValues = pick("rmse");
                  const maeAvg = mean(maeValues), rmseAvg = mean(rmseValues);
                  const degraded = forecast.method !== "prophet";

                  const description =
                    maeAvg !== null && Number.isFinite(maeAvg)
                      ? `UE 均值对比 · 平均 MAE ${maeAvg.toFixed(2)} ${metric.unit}`
                        + (rmseAvg !== null ? ` · RMSE ${rmseAvg.toFixed(2)} ${metric.unit}` : "")
                        + (degraded ? " · 降级模式（移动平均）" : " · Prophet 回测")
                      : "UE 均值对比 · 误差未返回";

                  const option: EChartsOption = {
                    animation: false,
                    backgroundColor: CHART_BACKGROUND,
                    textStyle: { fontFamily: CHART_FONT },
                    tooltip: {
                      trigger: "axis",
                      renderMode: "richText",
                      valueFormatter: (value: unknown) =>
                        `${value} ${metric.unit}`,
                    },
                    grid: {
                      left: 12,
                      right: 16,
                      top: 27,
                      bottom: 8,
                      // ECharts 6：containLabel 已弃用，等价新写法；'all' 同时兜住轴名（单位）
                      outerBoundsMode: "same",
                      outerBoundsContain: "all",
                    },
                    xAxis: {
                      type: "category",
                      data: forecast.ues,
                      ...AXIS_STYLE,
                    },
                    yAxis: {
                      type: "value",
                      name: metric.unit,
                      nameTextStyle: {
                        color: "#c3d5ee",
                        fontSize: 11,
                        fontFamily: CHART_FONT,
                      },
                      ...AXIS_STYLE,
                    },
                    series: [
                      {
                        name: "实测",
                        type: "bar",
                        barMaxWidth: 22,
                        itemStyle: { color: "#ffb32c", borderRadius: 2 },
                        data: trueData,
                      },
                      {
                        name: "预测",
                        type: "bar",
                        barMaxWidth: 22,
                        itemStyle: { color: "#1685ff", borderRadius: 2 },
                        data: predData,
                      },
                    ],
                  };

                  return (
                    <ChartPanel
                      key={metric.key}
                      title={metric.label}
                      description={description}
                      option={option}
                      height={135}
                    />
                  );
                })}
              </div>
            )}

            <details className="ain-forecast-method">
              <summary>方法与数据范围</summary>
              <p>
                方法：{forecast.method} · 时间桶：
                {forecast.bin_size_min} 分钟
              </p>
              <p>
                场景：{forecast.scenario} ·{" "}
                {forecast.window_start} 至 {forecast.window_end}
                （北京时间）
              </p>
            </details>
          </>
        )
      )}
    </section>
  );
}