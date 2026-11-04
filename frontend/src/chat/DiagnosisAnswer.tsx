import type { ChatResult } from "../types/backend";
import { scopeLabel } from "../services/backend";
import { diagnosisParagraphs, performanceHintText } from "./diagnosisModel";
import AnalysisNarrative, { ReplyText } from "./AnalysisNarrative";
import DiagnosisEvidence from "./DiagnosisEvidence";
import { analysisSections } from "./analysisText";
import type { EvidenceSelection } from "./DiagnosisCharts";

// 保持分析记录等现有调用的导出路径兼容。
export { ReplyText } from "./AnalysisNarrative";

export default function DiagnosisAnswer({ result, onEvidence, showScope = true }: { result: ChatResult; onEvidence?: (selection: Omit<EvidenceSelection, "id">) => void; showScope?: boolean }) {
  const diagnosis = result.diagnosis;
  if (!diagnosis) return <ReplyText text={result.reply} />;
  const targetAps = diagnosis.answerFocus?.targetAps.length ? diagnosis.answerFocus.targetAps
    : diagnosis.answerFocus?.observedAps ?? diagnosis.affectedAps;
  const objectId = targetAps.length === 1 ? targetAps[0] : undefined;
  // 模型写的整段回答：只有**通过校验**才展示（ok=true 且文本非空），否则退回规则文案
  const answerText = diagnosis.answerCheck?.ok ? (diagnosis.answerLlm ?? "").trim() : "";
  return <>
    {showScope && <>
      <p className="ain-ai-result-scope">研判范围：{scopeLabel(diagnosis.scope)}</p>
      {diagnosis.anomalyScope === "hour" && <p className="ain-ai-result-scope">整窗均值正常，仅个别片段越阈</p>}
      {diagnosis.anomalySpans.length > 0 && <details className="ain-ai-details"><summary>异常发生区间（{diagnosis.anomalySpans.length} 段）</summary><ul>
        {diagnosis.anomalySpans.map((span, i) => <li key={i}>{span.apId} · {span.start} — {span.end}（{span.minutes} 分钟）</li>)}
      </ul></details>}
    </>}
    <AnalysisNarrative source={answerText ? "AI 研判 · 数字已核对" : "规则研判"}
      sections={answerText ? analysisSections(answerText) : diagnosisParagraphs(diagnosis)}
      afterSection={title => onEvidence && title === "判断依据" ? <div className="ain-answer-evidence-links">
        <button type="button" className="ain-ai-text-button" onClick={() => onEvidence({ view: "trend", objectId })}>查看指标证据</button>
        {/覆盖/.test(diagnosis.anomalyType) && <button type="button" className="ain-ai-text-button" onClick={() => onEvidence({ view: "coverage", objectId, metric: "rsrpDbm" })}>查看覆盖证据</button>}
        <button type="button" className="ain-ai-text-button" onClick={() => onEvidence({ view: "connections", objectId })}>查看连接关系</button>
      </div> : null} />
    {diagnosis.state === "anomaly" && Boolean(diagnosis.performanceHints?.length) && <details className="ain-ai-details">
      <summary>同时存在的性能提示（未确诊故障）</summary>
      <ul>{diagnosis.performanceHints.map((hint, i) => <li key={`${hint.apId}-${hint.metric}-${i}`}>
        <strong>{performanceHintText(hint)}</strong><p>{hint.cause || "未提供具体原因"}</p><p>建议：{hint.suggestion || "持续观察相关指标"}</p>
      </li>)}</ul>
    </details>}
    <DiagnosisEvidence diagnosis={diagnosis} summarizedEvidenceCount={answerText || diagnosis.answerFocus ? 0 : 4} />
    {/* 诊断：为什么没有 AI 文本（不猜，直接显示后端返回的校验状态）。
        answerCheck 缺失 = 后端进程还是旧代码（没有 answer_llm/answer_check 字段）。 */}
    {!answerText && <details className="ain-ai-details">
      <summary>AI 文本状态（排查用）</summary>
      {diagnosis.answerCheck ? <>
        <p>是否通过校验：{diagnosis.answerCheck.ok ? "通过" : "未通过"}；来源：{diagnosis.answerCheck.source}
          {diagnosis.answerCheck.attempts ? `；重写次数：${diagnosis.answerCheck.attempts}` : ""}</p>
        {diagnosis.answerCheck.violations.length > 0 && <ul>
          {diagnosis.answerCheck.violations.map((line, i) => <li key={i}>{line}</li>)}
        </ul>}
        {!diagnosis.answerCheck.violations.length && <p>后端未给出违规原因：可能是模型未启用或调用失败。</p>}
      </> : <p>后端**未返回** answer_check 字段 —— 当前后端进程仍是旧代码（没有加载 answer_llm.py），请重启后端后再试。</p>}
    </details>}
  </>;
}
