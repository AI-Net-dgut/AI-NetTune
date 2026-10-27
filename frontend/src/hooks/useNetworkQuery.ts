import { record } from "../history/store";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { chatNetwork } from "../services/backend";
import type { ChatResult, QueryScope } from "../types/backend";
import { initialQueryState, queryReducer } from "../chat/queryState";

export interface QueryEntry {
  id: number; question: string; answer: string; failed: boolean;
  scope: string; createdAt: string; analysisTime?: string; analysisMode?: "analysis" | "history";
}
export default function useNetworkQuery() {
  const [state, dispatch] = useReducer(queryReducer, initialQueryState);
  const [archiveId,setArchiveId] = useState<string | undefined>();
  const [history, setHistory] = useState<QueryEntry[]>([]);
  const controller = useRef<AbortController | null>(null);
  const revision = useRef(0);
  const reset = useCallback(() => {
    revision.current++; controller.current?.abort(); controller.current = null;
    setArchiveId(undefined);
    dispatch({ type: "reset", id: revision.current });
  }, []);
  useEffect(() => () => { revision.current++; controller.current?.abort(); }, []);
  async function submit(message: string, scenario: string, window?: QueryScope, options?: { preserveDiagnosis?: boolean; analysisMode?: "analysis" | "history"; currentTime?: string; workspaceSnapshot?: unknown; validateResult?: (response: ChatResult) => void }) {
    if (!message.trim() || controller.current) return;
    const token = ++revision.current, abort = new AbortController();
    const createdAt = new Date().toISOString();
    controller.current = abort;
    const parentId = options?.preserveDiagnosis ? archiveId : undefined;
    const role = options?.preserveDiagnosis ? "evidence" as const : "question" as const;
    if (!options?.preserveDiagnosis) setArchiveId(undefined);
    const recordId=record({parentId,role,kind:"analysis",question:message.trim(),scope:window??{scenario,start:"",end:""},analysisTime:window?.end??options?.currentTime??"",masterTime:options?.currentTime,mode:options?.analysisMode,status:"pending",payload:{workspace:options?.workspaceSnapshot}});
    dispatch({ type: "start", id: token, question: message.trim(), preserveDiagnosis: options?.preserveDiagnosis });
    try {
      const response = await chatNetwork(message.trim(), scenario, window, abort.signal, options?.currentTime);
      options?.validateResult?.(response);
      record({id:recordId || undefined,parentId,role,kind:"analysis",question:message.trim(),scope:response.scope,analysisTime:response.scope.end,masterTime:options?.currentTime,mode:options?.analysisMode,status:"completed",payload:{response,workspace:options?.workspaceSnapshot}});
      for(const plan of response.plans??[])if(plan.simulation && recordId && !options?.preserveDiagnosis)record({kind:"simulation",parentId:recordId,question:`${plan.name} · 初始预计效果`,scope:response.scope,analysisTime:response.scope.end,status:"completed",payload:{plan,values:Object.fromEntries(plan.parameters.map(p=>[p.id,String(p.suggestedValue??'')])),result:plan.simulation}});
      if (revision.current !== token) return;
      setArchiveId(options?.preserveDiagnosis && parentId ? parentId : response.diagnosis ? recordId || undefined : undefined);
      dispatch({ type: "success", id: token, result: response });
      setHistory(previous => [{ id: token, question: message.trim(), answer: response.reply,
        failed: false, scope: `${response.scope.scenario} · ${response.scope.start} — ${response.scope.end}`,
        createdAt, analysisTime: window?.end ?? response.scope.end, analysisMode: options?.analysisMode ?? "analysis" }, ...previous].slice(0, 6));
    } catch (reason) {
      record({id:recordId || undefined,parentId,role,kind:"analysis",question:message.trim(),scope:window??{scenario,start:"",end:""},analysisTime:window?.end??"",masterTime:options?.currentTime,mode:options?.analysisMode,status:abort.signal.aborted?"cancelled":"failed",payload:{error:reason instanceof Error?reason.message:"分析失败",workspace:options?.workspaceSnapshot}});
      if (revision.current !== token) return;
      const text = reason instanceof Error ? reason.message : "对话请求失败";
      dispatch({ type: "failure", id: token, error: text });
      setHistory(previous => [{ id: token, question: message.trim(), answer: text,
        failed: true, scope: window ? `${scenario} · ${window.start} — ${window.end}` : scenario, createdAt, analysisTime: window?.end, analysisMode: options?.analysisMode ?? "analysis" }, ...previous].slice(0, 6));
    } finally {
      if (revision.current === token) controller.current = null;
    }
  }
  return { ...state, archiveId, history, submit, reset, clearHistory: () => setHistory([]) };
}
