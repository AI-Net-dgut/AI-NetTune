/**
 * 仿真内闭环的操作条：把当前选中方案的参数**应用到仿真网络**，或一键回滚。
 *
 * 设计为**纯新增组件**（核查清单第 1 条 / 申报书创新点④"闭环优化"）：
 * - 不接管管理模块既有按钮，只在顶部加一条状态 + 两个动作；
 * - 应用前必须二次确认（列出 AP / 参数 / 生效时刻），避免误点；
 * - 任何失败只在本条内提示，不改动上层状态，因此挂上它是零风险。
 */
import { useCallback, useEffect, useState } from "react";
import { applySimConfig, fetchSimCompare, fetchSimConfig, fetchSimVersions, rollbackSimConfig, toEpoch, type SimConfigStatus, type SimConfigVersion, type SimCompareResult } from "../../services/backend";
import { formatReplayTime } from "../../replay/replayState";

/** 配置变更事件名：工作台据此重新取数（复查 P0-3）。
 *  用事件而不是逐层传 props，是为了不改动 QueryPanel 已有的参数签名（三层透传风险更高）。 */
export const SIM_CONFIG_CHANGED = "nettune:sim-config-changed";
export interface SimConfigChangeDetail { revision: number; active: boolean }

function notifyConfigChanged(status: SimConfigStatus | null, active: boolean) {
  if (typeof window === "undefined") return;
  const detail: SimConfigChangeDetail = { revision: status?.revision ?? 0, active };
  window.dispatchEvent(new CustomEvent<SimConfigChangeDetail>(SIM_CONFIG_CHANGED, { detail }));
}

export default function SimConfigBar({ scenario, apIds, params, baseline, effectiveFrom, note, planId, disabled = false }: {
  scenario: string;
  apIds: string[];
  params: Record<string, number | string>;
  baseline: Record<string, number | string>;
  effectiveFrom: string;
  note?: string;
  planId?: string;
  disabled?: boolean;
}) {
  const [status, setStatus] = useState<SimConfigStatus | null>(null);
  const [history, setHistory] = useState<SimConfigVersion[]>([]);
  const [compare, setCompare] = useState<SimCompareResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);

  const reload = useCallback(async () => {
    let activeNow: SimConfigStatus["active"] = null;
    try {
      const status = await fetchSimConfig();
      setStatus(status);
      activeNow = status.active;
    } catch {
      // 后端未起/接口暂不可用：静默降级为"未知"，不影响页面其它功能
      setStatus(null);
    }
    try {
      setHistory(await fetchSimVersions());
    } catch {
      setHistory([]);   // 版本历史只是展示，失败就为空，绝不抛给界面
    }
    // 同一时段"原始 vs 生效后"对比：只有生效中才有意义；窗口取生效时刻起 10 分钟，
    // 不用父组件的窗口，避免多一层 props 耦合。
    // 时间必须走项目统一的北京时间换算 —— 原实现用 `new Date(无时区字符串)` + `toISOString()`，
    // 相当于把北京时间当本地时间再转成 UTC，11:30 的"10 分钟后"会变成 03:40（复查 P1-6）。
    if (activeNow) {
      try {
        const start = activeNow.effectiveFrom;
        const end = formatReplayTime(toEpoch(start) + 10 * 60_000);
        setCompare(await fetchSimCompare({ scenario: activeNow.scenario, apId: activeNow.apIds[0] ?? "", windowStart: start, windowEnd: end }));
      } catch (reason) {
        setCompare(null);
        // 不再静默：对比失败要把原因说出来，否则用户以为"没生效"
        setError(reason instanceof Error ? `效果对比暂不可用：${reason.message}` : "效果对比暂不可用");
      }
    } else {
      setCompare(null);
      setError("");
    }
  }, []);

  // 挂载时读一次当前生效状态；异步 setState 在 await 之后，不会造成渲染抖动
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void reload(); }, [reload]);

  const active = status?.active ?? null;
  const paramText = Object.entries(params).map(([key, value]) => {
    const base = baseline[key];
    return `${key} ${base == null ? "→" : `${base} →`} ${value}`;
  }).join(" · ");
  const canApply = !disabled && !busy && apIds.length > 0 && Object.keys(params).length > 0 && Boolean(effectiveFrom);

  async function doApply() {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await applySimConfig({ scenario, apIds, params, baseline, effectiveFrom, note, planId });
      setStatus(result.status);
      setNotice(result.notice || "已应用到仿真网络。");
      notifyConfigChanged(result.status, true);
      void reload();          // 刷新状态/版本历史/效果对比（不能只 setStatus）
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "应用失败");
    } finally {
      setBusy(false); setConfirming(false);
    }
  }

  async function doRollback() {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await rollbackSimConfig(active?.id);
      setStatus(result.status);
      setNotice(result.notice || "已恢复原始历史。");
      notifyConfigChanged(result.status, false);
      void reload();          // 回滚后清掉旧对比并重读状态
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "回滚失败");
    } finally {
      setBusy(false);
    }
  }

  return <section className="ain-sim-config" aria-label="仿真配置生效">
    <div className="ain-sim-config-head">
      <strong>闭环：应用至仿真网络</strong>
      {active
        ? <span className="ain-sim-config-badge is-active">生效中 · {active.id}</span>
        : <span className="ain-sim-config-badge">未应用</span>}
    </div>
    {active
      ? <p className="ain-sim-note">
        {active.apIds.join("、")} · {Object.entries(active.params).map(([k, v]) => `${k} ${v}`).join(" · ")}
        · 生效时刻 {active.effectiveFrom}；该时刻之后的<strong>网络 KPI</strong>按此配置计算，研判与趋势随之更新
        （原始历史未被改写）。<strong>频谱预警仍基于原始频谱，本轮未模拟配置对频谱的影响。</strong>
      </p>
      : <p className="ain-sim-note">
        应用后：{effectiveFrom ? `${effectiveFrom} 之后` : "指定时刻之后"}的<strong>网络 KPI</strong>按新参数偏移，
        研判、趋势与热力图随之更新；<strong>频谱预警不随配置变化</strong>。可随时回滚。
        {!apIds.length && " 当前未选中方案或无 AP，暂不可应用。"}
      </p>}
    {confirming && <p className="ain-sim-confirm">确认应用到 <strong>{apIds.join("、") || "（无 AP）"}</strong>？
      参数：{paramText || "（无）"}；生效时刻：<strong>{effectiveFrom}</strong>。应用后该时刻之后的数据会改变。</p>}
    <div className="ain-sim-actions">
      {active
        ? <button type="button" className="ain-button" disabled={busy} onClick={doRollback}>{busy ? "处理中…" : "恢复原配置"}</button>
        : confirming
          ? <>
            <button type="button" className="ain-button ain-primary" disabled={!canApply} onClick={doApply}>{busy ? "应用中…" : "确认应用"}</button>
            <button type="button" className="ain-button" disabled={busy} onClick={() => setConfirming(false)}>取消</button>
          </>
          : <button type="button" className="ain-button" disabled={!canApply} onClick={() => setConfirming(true)}>应用到仿真网络</button>}
    </div>
    {notice && <p className="ain-sim-ok" role="status">{notice}</p>}
    {error && <p className="ain-sim-error" role="alert">{error}</p>}
    {/* 同一时段开/关配置的对比（闭环可观测性）：只有生效中且取到数据才显示 */}
    {compare && <table className="ain-sim-compare">
      <caption>同段对比（{compare.apId} · 生效后 10 分钟）</caption>
      <thead><tr><th>指标</th><th>原始</th><th>生效后</th><th>变化</th></tr></thead>
      <tbody>
        {([["rsrp", "RSRP dBm"], ["sinr", "SINR dB"], ["prb_usage", "PRB %"], ["throughput", "吞吐 Mbps"]] as const).map(([key, label]) => {
          const before = compare.before[key], after = compare.after[key], delta = compare.delta[key];
          return <tr key={key}>
            <th scope="row">{label}</th>
            <td>{Number.isFinite(before) ? before.toFixed(2) : "—"}</td>
            <td>{Number.isFinite(after) ? after.toFixed(2) : "—"}</td>
            <td className={delta > 0 ? "is-up" : delta < 0 ? "is-down" : ""}>
              {Number.isFinite(delta) ? `${delta > 0 ? "+" : ""}${delta.toFixed(2)}` : "—"}
            </td>
          </tr>;
        })}
      </tbody>
    </table>}
    {/* 版本历史（只读展示，切片 4 的零风险部分）：让人看到"这次改了什么、改过几次"，
        不参与任何计算，接口失败也只是列表为空。 */}
    <details className="ain-sim-history">
      {/* 摘要必须带状态：否则"未应用"旁边出现"配置版本（1）"会让人以为配置还在生效。
          历史里可能存在已回滚/被替代的版本，active 为空才是"当前无生效配置"。 */}
      <summary>配置版本（{history.length || status?.versions || 0}{active ? " · 生效中" : " · 当前无生效"}）</summary>
      {history.length
        ? <ul>
          {history.slice(0, 6).map(item => (
            <li key={item.id}>
              <strong>{item.id}</strong>
              <span>{item.apIds.join("、")} · {Object.entries(item.params).map(([k, v]) => `${k} ${v}`).join(" · ")}</span>
              <span>生效 {item.effectiveFrom} · 应用 {item.appliedAt}</span>
            </li>
          ))}
        </ul>
        : <p className="ain-sim-note">暂无历史版本（或接口暂不可用）。</p>}
    </details>
  </section>;
}
