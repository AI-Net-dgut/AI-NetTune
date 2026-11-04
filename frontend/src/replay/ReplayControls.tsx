import type { ReplayConfig } from "./config";
import { Link } from "react-router-dom";
import { REPLAY_SPEEDS } from "./config";
import type { ReplayAction, ReplayState } from "./replayState";
import { formatReplayTime } from "./replayState";
import "./ReplayControls.css";

export default function ReplayControls({ state, act, loading, onReload, config, buffering = false }: {
  state: ReplayState; act: (action: ReplayAction) => void; loading: boolean;
  onReload: () => void; config: ReplayConfig; buffering?: boolean;
}) {
  const disabled = state.locked || loading || !state.ready;
  const status = loading ? "正在读取历史数据" : !state.ready ? "等待数据" : buffering ? "正在缓冲回放数据" : state.locked
    ? `分析时刻已锁定 · 主时钟${state.playing ? '继续运行' : '已暂停'}` : state.playing ? "主时钟运行中" : state.currentMs >= state.endMs ? "主时钟已到末尾" : "主时钟已暂停";
  return <section className="ain-replay-controls" aria-label="历史数据回放控制">
    <div className="ain-replay-main">
      <div className="ain-replay-clock"><span>历史数据回放 · 主时钟</span>
        <strong><time dateTime={formatReplayTime(state.currentMs).replace(" ", "T") + "+08:00"}>
          {formatReplayTime(state.currentMs)}</time><small>北京时间</small></strong></div>
      <span className="ain-replay-status" data-playing={state.playing} role="status">{status}</span>
      <div className="ain-replay-actions">
        <button className="ain-button" type="button" disabled={loading || !state.ready}
          onClick={() => act({ type: state.playing ? 'pause' : 'play' })}>{state.playing ? '暂停回放' : '继续回放'}</button>
        <div className="ain-replay-speed-control">
          <label>倍速<select aria-label="回放倍速" value={state.speed} disabled={loading || !state.ready}
            onChange={event => act({ type: "speed", value: Number(event.target.value) })}>
            {REPLAY_SPEEDS.map(speed => <option key={speed} value={speed}>{speed}×</option>)}</select></label>
          <span className="ain-replay-speed-hint">每秒推进 {Math.round((60 / config.realSecondsPerMinute) * state.speed)} 秒</span>
          <Link className="ain-replay-speed-guide" to="/#replay-guide">速度说明</Link>
        </div>
        <button className="ain-button" type="button" disabled={disabled} onClick={() => act({ type: "restart" })}>重新开始</button>
        <label className="ain-replay-loop"><input type="checkbox" checked={state.loop} disabled={loading || !state.ready}
          onChange={event => act({ type: "loop", value: event.target.checked })} />循环</label>
        <button className="ain-button" type="button" disabled={loading || state.locked} onClick={onReload}>重载数据</button>
      </div>
    </div>
  </section>;
}
