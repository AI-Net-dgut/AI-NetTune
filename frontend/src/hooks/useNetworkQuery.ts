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
  const [history, setHistory] = useState<QueryEntry[]>([]);
  const controller = useRef<AbortController | null>(null);
  const revision = useRef(0);
  const reset = useCallback(() => {
    revision.current++; controller.current?.abort(); controller.current = null;
    dispatch({ type: "reset", id: revision.current });
  }, []);
  useEffect(() => () => { revision.current++; controller.current?.abort(); }, []);
  async function submit(message: string, scenario: string, window?: QueryScope, options?: { preserveDiagnosis?: boolean; analysisMode?: "analysis" | "history"; currentTime?: string; validateResult?: (response: ChatResult) => void }) {
    if (!message.trim() || controller.current) return;
    const token = ++revision.current, abort = new AbortController();
    const createdAt = new Date().toISOString();
    controller.current = abort;
    dispatch({ type: "start", id: token, question: message.trim(), preserveDiagnosis: options?.preserveDiagnosis });
    try {
      const response = await chatNetwork(message.trim(), scenario, window, abort.signal, options?.currentTime);
      if (revision.current !== token) return;
      options?.validateResult?.(response);
      dispatch({ type: "success", id: token, result: response });
      setHistory(previous => [{ id: token, question: message.trim(), answer: response.reply,
        failed: false, scope: `${response.scope.scenario} · ${response.scope.start} — ${response.scope.end}`,
        createdAt, analysisTime: window?.end ?? response.scope.end, analysisMode: options?.analysisMode ?? "analysis" }, ...previous].slice(0, 6));
    } catch (reason) {
      if (revision.current !== token) return;
      const text = reason instanceof Error ? reason.message : "对话请求失败";
      dispatch({ type: "failure", id: token, error: text });
      setHistory(previous => [{ id: token, question: message.trim(), answer: text,
        failed: true, scope: window ? `${scenario} · ${window.start} — ${window.end}` : scenario, createdAt, analysisTime: window?.end, analysisMode: options?.analysisMode ?? "analysis" }, ...previous].slice(0, 6));
    } finally {
      if (revision.current === token) controller.current = null;
    }
  }
  return { ...state, history, submit, reset, clearHistory: () => setHistory([]) };
}
