import { useEffect, useState } from "react";
import type { QueryScope } from "../types/backend";
import { fetchFaultEvents, toEpoch } from "../services/backend";
import type { FaultEvent } from "../services/backend";
import type { ReplayAction, ReplayState } from "./replayState";
import { viewTime } from "./replayState";

/**
 * 预置案例快捷按钮：事件清单来自后端 GET /api/data/events（读 data/fault_events.csv）。
 *
 * 为什么不做成手写常量：审查报告 B-4 指出原来的 CASE_WINDOWS 是三个写死的时间戳，
 * 数据一换就失效；而 data_generator.py 每次运行都会同步输出 fault_events.csv。
 * 这里直接消费事件表，"事件"只有一份事实来源。
 *
 * 只做"跳转"一件事：把回放的分析范围设成事件的起止时间（select-range）。
 * 不改动任何既有面板与样式，复用 ReplayControls.css 里已有的类名。
 */

const TYPE_LABEL: Record<string, string> = {
  weak_coverage: "弱覆盖",
  congestion: "拥塞",
};

export default function CaseEventButtons({ state, act, scope, disabled = false }: {
  state: ReplayState;
  act: (action: ReplayAction) => void;
  scope: QueryScope;
  disabled?: boolean;
}) {
  const [events, setEvents] = useState<FaultEvent[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    fetchFaultEvents(scope.scenario)
      .then(list => { if (alive) { setEvents(list.slice(0, 6)); setError(""); } })
      .catch(reason => { if (alive) setError(reason instanceof Error ? reason.message : "事件清单获取失败"); });
    return () => { alive = false; };
  }, [scope.scenario]);

  if (error) return <p className="ain-reference-error" role="alert">{error}</p>;
  if (!events.length) return null;

  const cutoff = viewTime(state);

  return <div className="ain-reference-row" aria-label="预置案例">
    <span className="ain-reference-hint">预置案例</span>
    {events.map(event => {
      const startMs = toEpoch(event.start);
      const endMs = toEpoch(event.end);
      const reachable = state.ready && endMs <= cutoff;
      const label = `${event.apId} ${TYPE_LABEL[event.faultType] ?? event.faultType} ${event.start.slice(11, 16)}`;
      return <button key={event.eventId} type="button" className="ain-button"
        disabled={disabled || state.locked || !reachable}
        title={reachable
          ? `${event.eventId} ${event.start} — ${event.end}（渐变 ${event.rampMinutes} 分钟）`
          : "该事件晚于当前查看截止时刻，请先把时钟推进到事件结束之后"}
        onClick={() => act({ type: "select-range", startMs, endMs })}>
        {label}
      </button>;
    })}
  </div>;
}
