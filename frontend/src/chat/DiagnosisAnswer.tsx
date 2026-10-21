import type { ChatResult } from "../types/backend";
import { displayMetric } from "../services/metrics";
import { scopeLabel } from "../services/backend";
import { evidenceText } from "../components/analysis/diagnosisPresentation";
import { diagnosisParagraphs } from "./diagnosisModel";
import { candidateGroups } from "../components/management/planWorkspace";
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
  const groups = candidateGroups(diagnosis);
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
    <details className="ain-ai-details">
      <summary>详细证据与候选原因</summary>
      {diagnosis.evidence.length > 0 && <><h4>指标证据</h4><ul>{diagnosis.evidence.map((item, i) =>
        <li key={i}>{evidenceText(item)}{item.status ? ` · ${item.status}` : ""}</li>)}</ul></>}
      {groups.filter(group => group.candidates.length).map((group, i) => <section key={`${group.id}-${i}`}>
        <h4>{group.id} · 原因核查（未确认）</h4>{group.candidates.map((candidate, j) => <div className="ain-candidate-note" key={j}>
          <strong>{candidate.cause || "未提供原因名称"}</strong>
          {candidate.support.length > 0 && <p>支持：{candidate.support.join("；")}</p>}
          {candidate.against.length > 0 && <p>反证与限制：{candidate.against.join("；")}</p>}
          {candidate.checks.length > 0 && <p>待核查：{candidate.checks.join("；")}</p>}
        </div>)}</section>)}
      {diagnosis.affectedAps.length > 1 && diagnosis.comparison.length > 1 && <div className="ain-ai-table-wrap"><table>
        <caption>AP 窗口判定与指标对照 · 服务用户采样均值；窗口均值可能掩盖小时异常</caption>
        <thead><tr><th>AP</th><th>窗口判定 / 等级</th><th>RSRP / dBm</th><th>SINR / dB</th><th>吞吐量 / Mbps</th><th>PRB（后端值）</th></tr></thead>
        <tbody>{diagnosis.comparison.map((ap, i) => <tr key={i}><th>{ap.id}</th>
          <td>{ap.type || "未提供"} / {ap.level || "未提供"}</td><td>{displayMetric(ap.rsrp)}</td><td>{displayMetric(ap.sinr)}</td><td>{displayMetric(ap.throughput)}</td><td>{displayMetric(ap.prb)}</td></tr>)}</tbody>
      </table></div>}
      {diagnosis.alarms.length > 0 && <><h4>后端告警记录（北京时间）</h4><ul>{diagnosis.alarms.map((alarm, i) =>
        <li key={i}>{alarm.time} · {alarm.ap} · {alarm.type}{alarm.cause ? ` · ${alarm.cause}` : ""}</li>)}</ul></>}
      {!diagnosis.evidence.length && !groups.some(group => group.candidates.length) && !diagnosis.alarms.length && <p>暂无更多证据明细。</p>}
    </details>
  </>;
}
