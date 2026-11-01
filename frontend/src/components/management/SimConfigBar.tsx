/**
 * 仿真内闭环的操作条：把当前选中方案的参数**应用到仿真网络**，或一键回滚。
 *
 * 设计为**纯新增组件**（核查清单第 1 条 / 申报书创新点④"闭环优化"）：
 * - 不接管管理模块既有按钮，只在顶部加一条状态 + 两个动作；
 * - 应用前必须二次确认（列出 AP / 参数 / 生效时刻），避免误点；
 * - 任何失败只在本条内提示，不改动上层状态，因此挂上它是零风险。
 */
import { useCallback, useEffect, useState } from "react";
import { applySimConfig, fetchSimConfig, fetchSimVersions, rollbackSimConfig, type SimConfigStatus, type SimConfigVersion } from "../../services/backend";

export default function SimConfigBar({ scenario, apIds, params, baseline, effectiveFrom, note, planId, disabled = false }: {
  scenario: string;
  apIds: string[];
  params: Record<string, number>;
  baseline: Record<string, number>;
  effectiveFrom: string;
  note?: string;
  planId?: string;
  disabled?: boolean;
}) {
  const [status, setStatus] = useState<SimConfigStatus | null>(null);
  const [history, setHistory] = useState<SimConfigVersion[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);

  const reload = useCallback(async () => {
    try {
      setStatus(await fetchSimConfig());
    } catch {
      // 后端未起/接口暂不可用：静默降级为"未知"，不影响页面其它功能
      setStatus(null);
    }
    try {
      setHistory(await fetchSimVersions());
    } catch {
      setHistory([]);   // 版本历史只是展示，失败就为空，绝不抛给界面
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
        · 生效时刻 {active.effectiveFrom}；该时刻之后的数据、图表与告警都按此配置计算（原始历史未被改写）。
      </p>
      : <p className="ain-sim-note">
        应用后：{effectiveFrom ? `${effectiveFrom} 之后` : "指定时刻之后"}的数据按新参数偏移，研判、预测与告警同步变化，可随时回滚。
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
    {/* 版本历史（只读展示，切片 4 的零风险部分）：让人看到"这次改了什么、改过几次"，
        不参与任何计算，接口失败也只是列表为空。 */}
    <details className="ain-sim-history">
      <summary>配置版本（{history.length || status?.versions || 0}）</summary>
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
