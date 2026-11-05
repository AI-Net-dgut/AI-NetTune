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
const QUICK_CASE_LIMIT = 4;

export default function CaseEventButtons({ state, act, scope, disabled = false, onPrefill }: {
  state: ReplayState;
  act: (action: ReplayAction) => void;
  scope: QueryScope;
  disabled?: boolean;
  /** 点案例时把"要问的问题"交给上层填进输入框（**只填不发送**）。
   *  为什么需要：案例原本只改时间范围，用户以为点了就会出结论，实际右侧仍是空的（实测踩到）。 */
  onPrefill?: (question: string) => void;
}) {
  const [events, setEvents] = useState<FaultEvent[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    fetchFaultEvents(scope.scenario)
      .then(list => { if (alive) { setEvents([...list].sort((a, b) => a.start.localeCompare(b.start))); setError(""); } })
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
    {events.slice(0, QUICK_CASE_LIMIT).map(event => {
      const startMs = toEpoch(event.start);
      const endMs = toEpoch(event.end);
      const phase: "pending" | "running" | "ended" =
        startMs > nowMs ? "pending" : endMs <= nowMs ? "ended" : "running";
      const rangeEnd = phase === "running" ? Math.min(nowMs, endMs) : endMs;
      const selectable = state.ready && !state.locked && phase !== "pending" && rangeEnd > startMs;
      const typeLabel = TYPE_LABEL[event.faultType] ?? event.faultType;
      // 复查（用户反馈"越阈"太术语）：按钮只留"AP + 类型 + 时刻 + 状态"，
      // 时刻统一取真实越阈时间，含义在悬浮提示里说明（避免把注入开始误当成异常时刻）。
      const crossingText = event.crossingAt
        ? ` ${event.crossingAt.slice(5, 16)}`
        : ` ${event.start.slice(5, 16)}`;
      // 三种状态都标出来，便于从早到晚看完整条时间线。
      const phaseTag = phase === "running" ? "（进行中）" : phase === "ended" ? "（已结束）" : "（未开始）";
      const label = `${event.apId} ${typeLabel}${crossingText} ${phaseTag}`;
      const phaseText = phase === "pending"
        ? "未开始（主时钟尚未到事件开始时刻）"
        : phase === "running"
          // 时间必须用仓库统一的北京时间格式化：toISOString() 是 UTC，会早 8 小时
          // （独立审计 P3：11:30 会显示成 03:30）
          ? `进行中：只选已发生的 ${event.start} — ${formatReplayTime(rangeEnd)}（不读未来数据）`
          : `已结束：可选全段 ${event.start} — ${event.end}`;
      // 固定查看（viewMode=analysis/history）时并不是"禁用"，而是允许切换但会退出固定视图；
      // 提示文案必须说清实际按钮名（界面上是「退出分析，返回当前」，不是「实时」）。
      const fixedView = state.viewMode !== "live";
      return <button key={event.eventId} type="button" className="ain-button"
        disabled={disabled || !selectable}
        title={`${event.eventId} 注入 ${event.start} — ${event.end}（渐变 ${event.rampMinutes} 分钟）`
          + (event.crossingAt
            ? `\n真实越阈：${event.crossingAt}（${event.crossingBands ?? "?"}/50 频段，连续 3 分钟）`
            : "\n真实越阈：未达到判据（≥40/50 频段且连续 3 分钟）")
          + `\n${phaseText}`
          // 实际行为：切换案例会把分析范围改成该事件，但视图仍停在固定查看；
          // 要回到跟随主时钟需点 QueryPanel 里的「退出分析，返回当前」。
          + (fixedView ? "\n当前是固定查看：切换后仍停留在固定查看，需要时可点「退出分析，返回当前」。" : "")
          + (phase !== "pending" && viewCutoff < endMs ? "\n切换后会按上表范围重新取数。" : "")}
        onClick={() => {
          act({ type: "select-range", startMs, endMs: rangeEnd });
          // 只预填问题，不发送：用户仍可改写后再点「发送」
          onPrefill?.(`分析 ${event.apId} 在 ${event.start} 到 ${formatReplayTime(rangeEnd)} 的网络状态，是否存在${typeLabel}，说明依据与可能原因`);
        }}>
        {label}
      </button>;
    })}
    <span className="ain-replay-note">案例开始包含渐变阶段，是否异常以监测研判为准。</span>
  </div>;
}
