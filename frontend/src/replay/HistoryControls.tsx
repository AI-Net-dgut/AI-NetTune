import type { ReplayConfig } from "./config";
import type { ReplayAction, ReplayState } from "./replayState";
import { formatReplayTime, viewScope } from "./replayState";
import { toEpoch } from "../services/backend";
import "./ReplayControls.css";

export default function HistoryControls({ state, act, loading, config }: {
  state: ReplayState; act: (action: ReplayAction) => void; loading: boolean; config: ReplayConfig;
}) {
  const disabled = state.locked || loading || !state.ready;
  const reached = Math.max(0, Math.floor((state.currentMs - state.startMs) / 1000));
  const scope = viewScope(config, state);
  const startMs = toEpoch(scope.start), endMs = toEpoch(scope.end);
  const startProgress = Math.max(0, Math.floor((startMs - state.startMs) / 1000));
  const endProgress = Math.max(startProgress, Math.floor((endMs - state.startMs) / 1000));
  const startPercent = reached ? startProgress / reached * 100 : 0;
  const endPercent = reached ? endProgress / reached * 100 : 0;
  const historical = state.viewMode === "history";
  function changeRange(start: number, end: number) {
    if (disabled || !historical) return;
    act({ type: "history-range", startMs: state.startMs + start * 1000, endMs: state.startMs + end * 1000 });
  }
  return <section className="ain-history-controls" data-mode={historical ? "history" : "live"} aria-label="历史数据查看控制">
    {!historical ? <div className="ain-replay-main ain-live-history-entry"><span className="ain-replay-note">手动选历史：拖动两个滑块直接选择开始与结束时间；AI与图表同步。</span>
      <div className="ain-replay-actions"><button className="ain-button" type="button" disabled={disabled}
        onClick={() => act({ type: "history" })}>查看历史数据</button></div></div>
      : <div className="ain-replay-history"><div className="ain-replay-main"><strong>历史范围：{scope.start} — {scope.end}</strong>
        <span className="ain-replay-note">主时钟继续运行 · 历史模式不提供未来预测</span>
        <div className="ain-replay-actions"><button type="button" className="ain-button" disabled={disabled || endProgress === 0}
          title="将开始与结束时间同时向前移动5分钟，超出数据起点的部分截断"
          onClick={() => changeRange(Math.max(0, startProgress - 300), Math.max(0, endProgress - 300))}>回退5分钟</button>
          <button type="button" className="ain-button" onClick={() => act({ type: "live" })}>退出历史，返回当前</button></div>
      </div>
      <div className="ain-history-range-legend"><span>开始（上方滑块）：{scope.start}</span><span>结束（下方滑块）：{scope.end}</span></div>
      <div className="ain-history-range-slider" role="group" aria-label="历史起止时间范围">
        <div className="ain-history-range-track" aria-hidden="true" />
        <div className="ain-history-range-selected" aria-hidden="true" style={{
          left: `calc(${startPercent}% + ${10 - startPercent * .2}px)`,
          right: `calc(${100 - endPercent}% + ${10 - (100 - endPercent) * .2}px)`,
        }} />
        <input className="ain-history-range-input ain-history-range-start" aria-label="历史开始时间" aria-describedby="ain-history-range-help"
          type="range" min="0" max={reached} step="1" value={Math.min(startProgress, reached)} disabled={disabled || reached === 0}
          aria-valuemin={0} aria-valuemax={endProgress} aria-valuetext={scope.start}
          onChange={event => changeRange(Math.max(0, Math.min(Number(event.target.value), endProgress)), endProgress)} />
        <input className="ain-history-range-input ain-history-range-end" aria-label="历史结束时间" aria-describedby="ain-history-range-help"
          type="range" min="0" max={reached} step="1" value={Math.min(endProgress, reached)} disabled={disabled || reached === 0}
          aria-valuemin={startProgress} aria-valuemax={reached} aria-valuetext={scope.end}
          onChange={event => changeRange(startProgress, Math.min(reached, Math.max(startProgress, Number(event.target.value))))} />
      </div>
      <div className="ain-history-range-bounds"><span>数据起点：{config.start}</span><span>已到达：{formatReplayTime(state.currentMs)}</span></div>
      <p className="ain-history-range-help" id="ain-history-range-help">拖动左侧／上方滑块调开始，右侧／下方滑块调结束；可用Tab选中后按方向键精调。修改范围后重新提问；下方参考时长可快速重选开始时间。</p>
      </div>}
  </section>;
}
