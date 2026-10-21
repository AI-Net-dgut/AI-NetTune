/** 旧页面兼容入口。新代码统一使用services/backend，并明确传入查询范围。 */
import { analyzeNetwork, DEFAULT_SCOPE, fetchRecords } from './services/backend';
import type { QueryScope } from './types/backend';

export interface KpiData { timestamp: string; sinr: number; throughput: number }
export async function fetchKpiData(scenario = '校园', scope: QueryScope = { ...DEFAULT_SCOPE, scenario }): Promise<KpiData[]> {
  return (await fetchRecords({ ...scope, scenario })).map(record => ({
    timestamp: record.backendTime, sinr: record.sinrDb, throughput: record.throughputMbps,
  }));
}
export interface AnomalyData { anomaly_type: string; cause: string; suggestion: string; affected_aps: string[] }
export async function fetchAnomalyData(scope: QueryScope = DEFAULT_SCOPE): Promise<AnomalyData[]> {
  const diagnosis = await analyzeNetwork(scope);
  return diagnosis.state === 'empty' ? [] : [{ anomaly_type: diagnosis.anomalyType, cause: diagnosis.cause,
    suggestion: diagnosis.suggestion, affected_aps: diagnosis.affectedAps }];
}
