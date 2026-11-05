import type { ChatResult } from "../types/backend";

export interface QueryState {
  requestId: number;
  pending: boolean;
  error: string;
  question: string;
  result: ChatResult | null;
  diagnosisResult: ChatResult | null;
  diagnosisId: number;
  diagnosisQuestion: string;
  contextRequest: boolean;
}
export const initialQueryState: QueryState = {
  requestId: 0, pending: false, error: "", question: "",
  result: null, diagnosisResult: null, diagnosisId: 0, diagnosisQuestion: "", contextRequest: false,
};
export type QueryAction =
  | { type: "start"; id: number; question: string; preserveDiagnosis?: boolean }
  | { type: "success"; id: number; result: ChatResult }
  | { type: "failure"; id: number; error: string }
  | { type: "reset"; id: number };

// 仅证据区的明确辅助查询保留研判；新提问清空旧结论，迟到响应不覆盖新任务。
export function queryReducer(state: QueryState, action: QueryAction): QueryState {
  if (action.type === "reset") return { ...initialQueryState, requestId: action.id };
  if (action.type === "start") return {
    ...state, requestId: action.id, pending: true, error: "", result: null, question: action.question,
    contextRequest: Boolean(action.preserveDiagnosis),
    diagnosisResult: action.preserveDiagnosis ? state.diagnosisResult : null,
    diagnosisId: action.preserveDiagnosis ? state.diagnosisId : 0,
    diagnosisQuestion: action.preserveDiagnosis ? state.diagnosisQuestion : "",
  };
  if (action.id !== state.requestId || !state.pending) return state;
  if (action.type === "failure") return { ...state, pending: false, error: action.error };
  return {
    ...state, pending: false, error: "", result: action.result,
    diagnosisResult: (action.result.diagnosis || action.result.subject) ? action.result : state.diagnosisResult,
    diagnosisId: (action.result.diagnosis || action.result.subject) ? action.id : state.diagnosisId,
    diagnosisQuestion: (action.result.diagnosis || action.result.subject) ? state.question : state.diagnosisQuestion,
  };
}
