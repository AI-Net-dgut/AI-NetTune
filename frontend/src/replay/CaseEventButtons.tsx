import { useEffect, useState } from "react";
import type { QueryScope } from "../types/backend";
import { fetchFaultEvents, toEpoch } from "../services/backend";
import type { FaultEvent } from "../services/backend";
import type { ReplayAction, ReplayState } from "./replayState";
import { formatReplayTime, viewTime } from "./replayState";

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

  // 主时钟（数据已知到的时刻）与查看时钟分开（10-30 清单第 2 点）：
  //   可用性按**主时钟**判断，否则"固定分析时查看时刻停在 14:59"会让已结束案例也永远不可点；
  //   进行中的案例只选到 min(主时钟, 事件结束)，绝不越过已知数据（reducer 也会再夹一次）。
  const nowMs = state.currentMs;
  const viewCutoff = viewTime(state);

  return <div className="ain-reference-row" aria-label="预置案例">
    <span className="ain-reference-hint">预置案例</span>
    {events.map(event => {
      const startMs = toEpoch(event.start);
      const endMs = toEpoch(event.end);
      const phase: "pending" | "running" | "ended" =
        startMs > nowMs ? "pending" : endMs <= nowMs ? "ended" : "running";
      const rangeEnd = phase === "running" ? Math.min(nowMs, endMs) : endMs;
      const selectable = state.ready && !state.locked && phase !== "pending" && rangeEnd > startMs;
      const typeLabel = TYPE_LABEL[event.faultType] ?? event.faultType;
      const label = `${event.apId} ${typeLabel} ${event.start.slice(11, 16)}`
        + (phase === "running" ? "（进行中）" : "");
      const phaseText = phase === "pending"
        ? "未开始（主时钟尚未到事件开始时刻）"
        : phase === "running"
          // 时间必须用仓库统一的北京时间格式化：toISOString() 是 UTC，会早 8 小时
          // （独立审计 P3：11:30 会显示成 03:30）
          ? `进行中：只选已发生的 ${event.start} — ${formatReplayTime(rangeEnd)}（不读未来数据）`
          : `已结束：可选全段 ${event.start} — ${event.end}`;
      return <button key={event.eventId} type="button" className="ain-button"
        disabled={disabled || !selectable}
        title={state.locked
          ? `${event.eventId} ${event.start} — ${event.end}（渐变 ${event.rampMinutes} 分钟）\n当前为固定查看，请先退出固定分析（点「实时」）再切换案例`
          : `${event.eventId} ${event.start} — ${event.end}（渐变 ${event.rampMinutes} 分钟）\n${phaseText}`
            + (phase !== "pending" && viewCutoff < endMs ? "\n切换后会按上表范围重新取数。" : "")}
        onClick={() => act({ type: "select-range", startMs, endMs: rangeEnd })}>
        {label}
      </button>;
    })}
  </div>;
}
