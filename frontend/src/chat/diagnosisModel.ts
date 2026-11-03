import type { Diagnosis, MetricKey, PerformanceHint, QueryScope } from "../types/backend";
import { evidenceText } from "../components/analysis/diagnosisPresentation";
import { displayMetric } from "../services/metrics";

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

export function performanceHintText(hint: PerformanceHint) {
  const labels: Record<string, [string, string]> = {
    rsrp: ["RSRP", "dBm"], sinr: ["SINR", "dB"], throughput: ["吞吐量", "Mbps"],
    prb_usage: ["PRB占用率", "%"], packet_loss: ["丢包率", "%"],
    sinr_low_share: ["低SINR采样占比", "%"], weak_coverage_ratio: ["弱覆盖采样占比", "%"],
    congestion_ratio: ["拥塞采样占比", "%"],
  };
  const [label, unit] = labels[hint.metric] ?? [hint.metric || "性能指标", ""];
  const ratio = /(?:_ratio|_share)$/.test(hint.metric);
  const value = (n: number) => Number.isFinite(n) ? displayMetric(ratio ? n * 100 : n) : "未提供";
  return `${hint.apId || "未指定对象"} · ${label} ${value(hint.value)}${unit ? ` ${unit}` : ""}；提示阈值 ${value(hint.threshold)}${unit ? ` ${unit}` : ""}`;
}

export function diagnosisParagraphs(result: Diagnosis) {
  const anomaly = result.state === "anomaly";
  const hints = result.performanceHints ?? [];
  const performance = result.state !== "empty" && !anomaly && (result.currentState === "performance_warning" || hints.length > 0);
  const objects = result.affectedAps.join("、") || "尚未明确的异常对象";
  const alarms = result.alarms.filter(item => item.time && result.affectedAps.includes(item.ap));
  const timing = result.anomalySpans?.length
    ? ""
    : alarms.length
      ? `有 ${alarms.length} 条相关告警；告警时刻不能直接确定连续故障时长。`
      : "尚无明确的故障起止时间，当前结论针对所选分析范围。";
  const candidate = result.candidates[0];
  const reasons = [candidate?.cause || result.cause, ...(candidate?.against || []).slice(0, 2)].filter(Boolean);
  const finding = result.currentState === "recovered_in_window"
    ? `${objects} 在所选窗口内出现过异常，截至分析时刻已恢复。`
    : `${objects} 在所选窗口内被判定存在异常。`;
  const hintReasons = hints.map(h => `${h.apId || "未指定对象"}：${h.cause || "尚无具体原因"}`).join("；");
  const hintAdvice = [...new Set(hints.map(h => h.suggestion).filter(Boolean))].join("；");
  return [
    { title: "研判结论", text: result.state === "empty" ? "此范围没有可分析数据，请更换数据范围。"
      : anomaly ? `${finding}主异常概括为${result.anomalyType || "网络异常"}${result.level ? `（主异常等级：${result.level}）` : ""}。${timing}`
        : performance ? `当前范围未确诊故障，但存在性能提示${hints.length ? `，涉及${[...new Set(hints.map(h => h.apId).filter(Boolean))].join("、") || "部分对象"}` : ""}，需要结合下方指标继续观察。`
          : "当前范围未发现明显异常；结论限于已返回的指标和诊断规则。" },
    { title: "判断依据", text: result.evidence.length
      ? result.evidence.slice(0, 4).map(item => `${evidenceText(item)}${item.status ? `（${item.status}）` : ""}`).join("；") + "。"
      : performance && hints.length ? hints.map(performanceHintText).join("；") + "。"
        : "尚无逐项指标证据，可结合下方真实采样趋势核查。" },
    { title: "原因与限制", text: anomaly
      ? `${reasons.length ? reasons.join("；") : "当前证据不足以确认具体原因"}。候选原因仍需验证，不能直接视为已确认根因。`
      : performance ? `${hintReasons || "现有结果仅提示性能变化，尚无具体原因"}。性能提示不等于确诊故障。`
        : "现有结果不足以排除短时异常或尚未采集的因素。" },
    { title: "处理与预估", text: anomaly && result.suggestion.trim()
      ? `${result.suggestion}。预计效果需要对应的仿真数据支持。`
      : performance ? `${hintAdvice || "持续观察提示涉及的指标，必要时补充采样或人工检查"}。本项建议不代表已完成配置或效果仿真。`
        : "继续观察关键指标；出现异常后再确定调整方案。" },
  ];
}
