import type { Diagnosis, MetricKey, QueryScope } from "../types/backend";
import { evidenceText } from "../components/analysis/diagnosisPresentation";

export function sameQueryScope(a: QueryScope, b: QueryScope) {
  return a.scenario === b.scenario && a.start === b.start && a.end === b.end;
}
export function diagnosisMetric(result: Diagnosis): MetricKey {
  const keys: Record<string, MetricKey> = {
    rsrp: "rsrpDbm", sinr: "sinrDb", throughput: "throughputMbps",
    prb_usage: "resourceUtilizationPct", packet_loss: "packetLossPct",
  };
  const evidenceMetric = keys[result.evidence[0]?.metric];
  if (evidenceMetric) return evidenceMetric;
  if (/拥塞|负载|congestion/i.test(result.anomalyType)) return "resourceUtilizationPct";
  if (/覆盖|coverage/i.test(result.anomalyType)) return "rsrpDbm";
  return "throughputMbps";
}
export function diagnosisParagraphs(result: Diagnosis) {
  const anomaly = result.state === "anomaly";
  const objects = result.affectedAps.join("、") || "后端未明确异常对象";
  const alarms = result.alarms.filter(item => item.time && result.affectedAps.includes(item.ap));
  const timing = alarms.length
    ? `后端返回 ${alarms.length} 条相关告警，时间详见完整依据；这些告警不能直接确定连续故障时长。`
    : "后端未提供明确的故障起止时间，当前结论针对所选分析范围。";
  const candidate = result.candidates[0];
  const reasons = [candidate?.cause || result.cause, ...(candidate?.against || []).slice(0, 2)].filter(Boolean);
  // affectedAps 可能包含不同故障，主类型/等级只概括后端选出的主要异常。
  const finding = `${objects} 存在异常。主异常概括为${result.anomalyType || "网络异常"}${result.level ? `（主异常等级：${result.level}）` : ""}。`;
  return [
    { title: "发现问题", text: result.state === "empty" ? "此范围没有可分析数据，请更换数据范围。"
      : anomaly ? `${finding}${timing}`
        : "当前范围未发现明显异常；结论限于已返回的指标和诊断规则。" },
    { title: "判断依据", text: result.evidence.length
      ? result.evidence.slice(0, 4).map(evidenceText).join("；") + "。"
      : "后端未返回逐项指标证据，可结合下方真实采样趋势核查。" },
    { title: "原因与限制", text: anomaly
      ? `${reasons.length ? reasons.join("；") : "当前证据不足以确认具体原因"}。候选原因仍需验证，不能直接视为已确认根因。`
      : "现有结果不足以排除短时异常或尚未采集的因素。" },
    { title: "处理与预估", text: anomaly && result.suggestion.trim()
      ? `${result.suggestion}。${candidate?.checks.length ? `优先核查：${candidate.checks.slice(0, 2).join("；")}。` : ""}可在右侧核对方案；预计效果需要对应的仿真数据支持。`
      : "继续观察关键指标；出现异常后再确定调整方案。" },
  ];
}
