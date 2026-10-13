import { useState } from "react";
import type { QueryScope } from "../types/backend";
import type { ReplayAction, ReplayState } from "./replayState";
import { formatReplayTime, viewTime } from "./replayState";
import { toEpoch } from "../services/backend";
import "./ReplayControls.css";

const PRESETS = [15, 30, 60, 120];
export default function ReferenceWindowControls({ state, act, scope, disabled, idPrefix = "ain-reference" }: {
  state: ReplayState; act: (action: ReplayAction) => void;
  scope: QueryScope; disabled: boolean; idPrefix?: string;
}) {
  const [customOpen, setCustomOpen] = useState(state.customStartMs !== null);
  const [draft, setDraft] = useState(scope.start.replace(" ", "T").slice(0, 16));
  const [error, setError] = useState("");
  const cutoff = Math.floor(viewTime(state) / 1000) * 1000;
  const earliest = state.startMs;
  const blocked = disabled || state.locked || !state.ready;
  const clipped = state.customStartMs === null && cutoff - state.referenceMinutes * 60_000 < state.startMs;
  function apply() {
    if (blocked || state.viewMode === "live") return;
    try {
      const normalized = draft.replace("T", " ") + (draft.length === 16 ? ":00" : "");
      const timeMs = toEpoch(normalized);
      if (timeMs < state.startMs || timeMs > cutoff) throw new Error("开始时间必须位于已有数据起点与分析截止时刻之间。");
      setError(""); act({ type: "reference-start", timeMs });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "请输入有效开始时间。"); }
  }
  return <div className="ain-reference-controls" aria-label="分析参考范围">
    <div className="ain-reference-row"><label htmlFor={`${idPrefix}-window`}>参考范围</label>
      <select id={`${idPrefix}-window`} value={customOpen || state.customStartMs !== null ? "custom" : String(state.referenceMinutes)} disabled={blocked}
        onChange={event => { setError(""); if (event.target.value === "custom") {
          if (state.viewMode === "live") return;
          setDraft(scope.start.replace(" ", "T").slice(0, 16)); setCustomOpen(true);
        } else { setCustomOpen(false); act({ type: "reference", minutes: Number(event.target.value) }); } }}>
        {PRESETS.map(minutes => <option key={minutes} value={minutes}>近{minutes}分钟{minutes === 60 ? "（默认）" : ""}</option>)}
        <option value="custom" disabled={state.viewMode === "live"}>自定义开始时间</option>
      </select>
      <span className="ain-reference-actual">参考数据：{scope.start} — {scope.end}（北京时间）</span>
      {clipped && <span className="ain-reference-hint">已按数据起点截断</span>}
    </div>
    {customOpen && state.viewMode !== "live" && <form className="ain-reference-row ain-reference-custom" onSubmit={event => { event.preventDefault(); apply(); }}>
      <label htmlFor={`${idPrefix}-start`}>开始时间</label><input id={`${idPrefix}-start`} type="datetime-local" step="1" value={draft} disabled={blocked}
        min={formatReplayTime(earliest).replace(" ", "T")} max={formatReplayTime(cutoff).replace(" ", "T")}
        onChange={event => { setDraft(event.target.value); setError(""); }} />
      <span>结束固定为 {scope.end}</span><button type="submit" className="ain-button" disabled={blocked}>更新参考范围</button>
    </form>}
    {error && <p className="ain-reference-error" role="alert">{error}</p>}
    <p className="ain-reference-hint">{state.viewMode === "live" ? "先固定当前时刻，或在数据区用双滑块选择历史范围。" : "快捷时长按选定结束时间向前计算开始；拖动开始滑块会切换为自定义范围。调整会清除当前研判，需重新提问。"} 两处参考设置同步，主时钟继续运行。</p>
  </div>;
}
