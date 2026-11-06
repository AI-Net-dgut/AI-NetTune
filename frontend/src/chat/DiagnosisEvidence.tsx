import type { Candidate, Diagnosis } from "../types/backend";
import { apCandidateGroups, evidenceText } from "../components/analysis/diagnosisPresentation";
import { displayMetric } from "../services/metrics";
import type { EvidenceSelection } from "./DiagnosisCharts";
import { causeEvidenceSelection } from "./causeEvidence";

function CandidateProof({ candidate, object, onEvidence }: { candidate: Candidate; object: string; onEvidence?: (selection: Omit<EvidenceSelection, 'id'>) => void }) {
  return <details>
    <summary>{object} · {candidate.cause || "候选原因待补充"}</summary>
    <dl className="ain-compact-proof">
      {([
        ["支持证据", candidate.support], ["反证与限制", candidate.against], ["待检查项", candidate.checks],
      ] as const).filter(([, lines]) => lines.length > 0).map(([label, lines]) =>
        <div key={label}><dt>{label}</dt><dd><ul>{lines.map((line, i) => <li key={i}>{line}</li>)}</ul></dd></div>)}
    </dl>
    {onEvidence && <button type="button" className="ain-ai-text-button" onClick={() => onEvidence(causeEvidenceSelection(candidate, /^AP-\d+$/.test(object) ? object : undefined))}>查看该原因的数据证据图</button>}
  </details>;
}

// 只补充正文未展开的依据，不再重复结论、评分、指标卡与判断链路。
export default function DiagnosisEvidence({ diagnosis, summarizedEvidenceCount = 4, onEvidence }: { diagnosis: Diagnosis; summarizedEvidenceCount?: number; onEvidence?: (selection: Omit<EvidenceSelection, 'id'>) => void }) {
  const groups = apCandidateGroups(diagnosis).filter(group => group.candidates.length > 0);
  const mainObject = diagnosis.affectedAps.join("、") || "整体网络";
  const remainingEvidence = diagnosis.evidence.slice(summarizedEvidenceCount);
  const hasContent = diagnosis.candidates.length || groups.length || remainingEvidence.length || diagnosis.comparison.length || diagnosis.alarms.length;
  if (!hasContent) return null;
  return <details className="ain-ai-details">
    <summary>补充证据与候选原因</summary>
    <div className="ain-compact-evidence">
      {diagnosis.candidates.map((candidate, i) => <CandidateProof key={`main-${i}`} candidate={candidate} object={mainObject} onEvidence={onEvidence} />)}
      {groups.map((group, i) => group.candidates.map((candidate, j) => <CandidateProof key={`${group.id}-${i}-${j}`} candidate={candidate} object={group.id} onEvidence={onEvidence} />))}
      {remainingEvidence.length > 0 && <details><summary>{summarizedEvidenceCount ? "其他指标证据" : "指标明细"}</summary><ul>
        {remainingEvidence.map((item, i) => <li key={i}>{evidenceText(item)}{item.status && ` · 状态：${item.status}`}</li>)}
      </ul></details>}
      {diagnosis.comparison.length > 0 && <details><summary>{diagnosis.answerFocus && diagnosis.answerFocus.kind !== "overview" ? "同窗 AP 主判定 · 背景对照" : diagnosis.comparison.length > 1 ? "AP 指标对照 · 时段均值" : "AP 指标 · 时段均值"}</summary><div className="ain-compact-table"><table>
        <thead><tr><th scope="col">AP</th><th scope="col">判定</th><th scope="col">RSRP / dBm</th><th scope="col">SINR / dB</th><th scope="col">吞吐 / Mbps</th><th scope="col">PRB / %</th></tr></thead>
        <tbody>{diagnosis.comparison.map((item, i) => <tr key={`${item.id}-${i}`} data-affected={diagnosis.affectedAps.includes(item.id)}>
          <th scope="row">{item.id}</th><td>{item.type} · {item.level}</td><td>{displayMetric(item.rsrp)}</td><td>{displayMetric(item.sinr)}</td><td>{displayMetric(item.throughput)}</td><td>{displayMetric(item.prb)}</td>
        </tr>)}</tbody>
      </table></div></details>}
      {diagnosis.alarms.length > 0 && <details><summary>相关告警（{diagnosis.alarms.length} 条）</summary><ul>
        {diagnosis.alarms.map((item, i) => <li key={i}>{item.time} · {item.ap} · {item.type} · {item.level}{item.cause && `：${item.cause}`}</li>)}
      </ul></details>}
    </div>
  </details>;
}
