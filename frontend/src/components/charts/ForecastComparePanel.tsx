import { record } from '../../history/store';
import { useEffect, useRef, useState } from "react";
import ForecastCompareChart from "./ForecastCompareChart";
import { fetchForecast } from "../../services/backend";
import type { ForecastResult, QueryScope } from "../../types/backend";

/**
 * 「真实 vs 预测对比」面板：自己负责取数与状态，放进任何有 scope 的位置即可。
 *
 * 为什么取数放在这里：`/api/analyze/forecast`（接口10）可能因 Prophet 预热/降级而变慢，
 * 做成自包含组件后，上层不必关心它的加载状态。
 *
 * 为什么默认不自动加载：接口10 首次调用理论上要等预热（服务端已做后台预热，通常 0.2s；
 * 万一没预热成功最多 20s）。改成点「运行回测」再请求，避免打开工作台就出现转圈的面板。
 *
 * 范围变化时由父组件用 ``key`` 重建本组件，因此这里不需要"在 effect 里重置 state"
 * （那会触发 react-hooks/set-state-in-effect）。
 */
export default function ForecastComparePanel({ scope, disabled = false, archiveParentId }: {
  scope: QueryScope | null; disabled?: boolean; archiveParentId?: string;
}) {
  const [forecast, setForecast] = useState<ForecastResult | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);

  // 卸载时取消在途请求
  useEffect(() => () => controller.current?.abort(), []);

  async function run() {
    if (!scope || pending) return;
    controller.current?.abort();
    const ac = new AbortController();
    controller.current = ac;
    setPending(true); setError("");
    try {
      const result = await fetchForecast(scope, ac.signal);
      setForecast(result);
      record({kind:'backtest',parentId:archiveParentId,question:'真实与预测对比（历史回测）',scope,analysisTime:scope.end,status:'completed',payload:{forecast:result}});
    } catch (e) {
      if (ac.signal.aborted) return;
      setError(e instanceof Error ? e.message : "预测对比数据获取失败");
    } finally {
      if (!ac.signal.aborted) setPending(false);
    }
  }

  return <details className="ain-ai-details" id="ain-forecast-compare">
    <summary>真实 vs 预测对比（历史回测）</summary>
    <div className="ain-ai-chart-actions">
      <button type="button" className="ain-button" disabled={!scope || disabled || pending} onClick={run}>
        {pending ? "正在回测…" : forecast ? "重新回测" : "运行回测"}
      </button>
      <span className="ain-management-muted">
        {scope ? `范围：${scope.start} — ${scope.end}` : "先加载数据"}
      </span>
    </div>
    {forecast?.limitations?.length ? (
      <ul className="ain-ai-note">
        {forecast.limitations.map((text, i) => <li key={i}>{text}</li>)}
      </ul>
    ) : null}
    <ForecastCompareChart forecast={forecast} pending={pending} error={error} />
    <p className="ain-ai-note">
      口径：按 1 小时桶聚合，前 80% 训练、后 20% 测试；MAE/RMSE 是测试段逐点误差（有量纲），
      MAPE 仅作参考（对 dBm 类指标意义有限）。降级模式下预测为常数，MAE 即测试段离散度。
    </p>
  </details>;
}
