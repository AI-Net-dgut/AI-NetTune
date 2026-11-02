import type { ChatResult } from "../types/backend";
import { scopeLabel } from "../services/backend";
import { diagnosisParagraphs, performanceHintText } from "./diagnosisModel";
import EvidencePanel from "../components/analysis/EvidencePanel";
import type { EvidenceSelection } from "./DiagnosisCharts";

// 仅渲染文字与加粗，不把助手文本作为 HTML 执行。
export function ReplyText({ text }: { text: string }) {
  return <div className="ain-reply-text">{text.split(/\n\s*\n/).filter(Boolean).map((paragraph, i) =>
    <p key={i}>{paragraph.split(/(\*\*[^*]+\*\*)/g).map((part, j) =>
      part.startsWith("**") && part.endsWith("**") ? <strong key={j}>{part.slice(2, -2)}</strong> : part)}</p>)}</div>;
}

export default function DiagnosisAnswer({ result, onEvidence }: { result: ChatResult; onEvidence?: (selection: Omit<EvidenceSelection, "id">) => void }) {
  const diagnosis = result.diagnosis;
  if (!diagnosis) return <ReplyText text={result.reply} />;
  const objectId = diagnosis.affectedAps.length === 1 ? diagnosis.affectedAps[0] : undefined;
  // 保留默认两段的紧凑摘要，其余区间可展开；完整日期避免跨日同钟点混淆。
  const allSpans = [...(diagnosis.anomalySpans ?? [])].sort((a, b) => b.minutes - a.minutes);
  const spans = allSpans.slice(0, 2);
  const formatTime = (value: string) => value ? value.replace("T", " ").slice(0, 16) : "未提供";
  const spanText = (span: typeof allSpans[number]) => `${span.apId} ${formatTime(span.start)} — ${formatTime(span.end)}（${span.minutes} 分钟）`;
  const scopeNote = diagnosis.anomalyScope === "hour"
    ? "（整窗均值正常，仅个别片段越阈）"
    : "";
  // 模型写的整段回答：只有**通过校验**才展示（ok=true 且文本非空），否则退回规则文案
  const answerText = diagnosis.answerCheck?.ok ? (diagnosis.answerLlm ?? "").trim() : "";
  // 未通过校验时把原因**显示出来**：否则用户只看到"规则文案"，不知道是模型没通、还是被校验拦下
  const answerProblem = !answerText && diagnosis.answerCheck && !diagnosis.answerCheck.ok
    ? (diagnosis.answerCheck.violations?.length ? diagnosis.answerCheck.violations : ["未返回原因"])
    : [];
  return <>
    <p className="ain-ai-result-scope">研判范围：{scopeLabel(diagnosis.scope)}</p>
    {spans.length > 0 && <p className="ain-ai-result-scope">
      异常区间：{spans.map(spanText).join("；")}{scopeNote}
    </p>}
    {allSpans.length > 2 && <details className="ain-ai-details">
      <summary>查看剩余 {allSpans.length - 2} 段异常区间</summary>
      <ul>{allSpans.slice(2).map((span, i) => <li key={`${span.apId}-${span.start}-${span.end}-${i}`}>{spanText(span)}</li>)}</ul>
    </details>}
    {/* 回答主体：优先展示**模型写的整段回答**（已通过 answer_llm 校验器逐条核对数字/对象/断言）；
        未通过校验时退回规则拼装的四段话，并把规则原文留在折叠区，绝不隐藏"到底谁写的"。 */}
    {answerText && <div className="ain-diagnosis-prose">
      <div>
        <h4>AI 回答（由大模型生成 · 已通过证据校验）</h4>
        <p>{answerText}</p>
      </div>
    </div>}
    {answerText && <details className="ain-ai-details">
      <summary>查看规则生成的原文（证据式表述）</summary>
      <div className="ain-diagnosis-prose">{diagnosisParagraphs(diagnosis).map(item =>
        <div key={item.title}><h4>{item.title}</h4><p>{item.text}</p></div>)}</div>
    </details>}
    {!answerText && <div className="ain-diagnosis-prose">{diagnosisParagraphs(diagnosis).map(item =>
      <div key={item.title}><h4>{item.title}</h4><p>{item.text}</p>
        {onEvidence && item.title === "判断依据" && <div className="ain-answer-evidence-links">
          <button type="button" className="ain-ai-text-button" onClick={() => onEvidence({ view: "trend", objectId })}>查看指标证据</button>
          {/覆盖/.test(diagnosis.anomalyType) && <button type="button" className="ain-ai-text-button" onClick={() => onEvidence({ view: "coverage", objectId, metric: "rsrpDbm" })}>查看覆盖证据</button>}
          <button type="button" className="ain-ai-text-button" onClick={() => onEvidence({ view: "connections", objectId })}>查看连接关系</button>
        </div>}
      </div>)}</div>}
    {answerProblem.length > 0 && <details className="ain-ai-details">
      <summary>为什么这段不是大模型写的（{diagnosis.answerCheck?.source === "rule" ? "已回退规则文案" : "未启用"}）</summary>
      <ul>{answerProblem.map((line, i) => <li key={i}>{line}</li>)}</ul>
      <p className="ain-ai-note">校验规则：数字与对象必须来自证据、无证据时不得使用确定性断言、必须有【结论】【依据】【方案】三节。</p>
    </details>}
    {diagnosis.state === "anomaly" && Boolean(diagnosis.performanceHints?.length) && <details className="ain-ai-details">
      <summary>同时存在的性能提示（未确诊故障）</summary>
      <ul>{diagnosis.performanceHints.map((hint, i) => <li key={`${hint.apId}-${hint.metric}-${i}`}>
        <strong>{performanceHintText(hint)}</strong><p>{hint.cause || "未提供具体原因"}</p><p>建议：{hint.suggestion || "持续观察相关指标"}</p>
      </li>)}</ul>
    </details>}
    {/* 证据链可视化：指标证据+阈值 → 判断链路 → 候选原因卡片（按 AP 分组）→ AP 对比 → 告警历史。
        这块原先是一个纯文字折叠区；换成 EvidencePanel 后内容不减，多了"判断链路"流程条与分组切换。
        默认折叠（与改造前一致），避免把 AI 区撑得太长。 */}
    <details className="ain-ai-details">
      <summary>详细证据与候选原因</summary>
      <EvidencePanel result={diagnosis} pending={false} error="" title="故障研判与证据" />
    </details>
    {/* LLM 的"第二处用武之地"必须在界面上可见（否则用户会以为大模型没起作用）：
        后端 `plan_narrative` 是模型基于**已验证指标**写的方案解读，`plan_narrative_source`
        如实标明是 llm 还是 template。规则给结构化证据，模型给自然语言解读，两者并列。 */}
    {diagnosis.planNarrative && <details className="ain-ai-details" open>
      <summary>AI 方案解读（{diagnosis.planNarrativeSource === "llm" ? "由大模型生成" : "模板生成"}）</summary>
      <p>{diagnosis.planNarrative}</p>
    </details>}
  </>;
}
