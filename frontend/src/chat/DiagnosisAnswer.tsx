import type { ChatResult } from "../types/backend";
import { scopeLabel } from "../services/backend";
import { diagnosisParagraphs } from "./diagnosisModel";
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
  return <>
    <p className="ain-ai-result-scope">研判范围：{scopeLabel(diagnosis.scope)}</p>
    <div className="ain-diagnosis-prose">{diagnosisParagraphs(diagnosis).map(item =>
      <div key={item.title}><h4>{item.title}</h4><p>{item.text}</p>
        {onEvidence && item.title === "判断依据" && <div className="ain-answer-evidence-links">
          <button type="button" className="ain-ai-text-button" onClick={() => onEvidence({ view: "trend", objectId })}>查看指标证据</button>
          {/覆盖/.test(diagnosis.anomalyType) && <button type="button" className="ain-ai-text-button" onClick={() => onEvidence({ view: "coverage", objectId, metric: "rsrpDbm" })}>查看覆盖证据</button>}
          <button type="button" className="ain-ai-text-button" onClick={() => onEvidence({ view: "connections", objectId })}>查看连接关系</button>
        </div>}
      </div>)}</div>
    {/* 证据链可视化：指标证据+阈值 → 判断链路 → 候选原因卡片（按 AP 分组）→ AP 对比 → 告警历史。
        这块原先是一个纯文字折叠区；换成 EvidencePanel 后内容不减，多了"判断链路"流程条与分组切换。
        默认折叠（与改造前一致），避免把 AI 区撑得太长。 */}
    <details className="ain-ai-details">
      <summary>详细证据与候选原因</summary>
      <EvidencePanel result={diagnosis} pending={false} error="" title="故障研判与证据" />
    </details>
  </>;
}
