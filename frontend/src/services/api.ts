import {
  createMockDashboard,
  createMockQuery,
} from "../mock/network";

import { METRIC_META } from "../types/network";

import type {
  DashboardData,
  QueryRequest,
  QueryResponse,
} from "../types/network";

export interface ApiOptions {
  mode?: "mock" | "api";
  baseUrl?: string;
}

// 旧版示例适配器，当前App没有引用。正式工作台使用services/backend.ts；请勿将此模块当作真实接口入口。
export const API_CONFIG: Required<ApiOptions> = {
  mode: "mock",
  baseUrl: "",
};

// 下列路径是前端暂定约定，必须与 docs/api.md 确认后启用。
const PATHS = {
  dashboard: "/api/dashboard",
  query: "/api/query",
};

// 以下函数检查后端返回的数据，避免异常数据直接进入图表。
function isObject(
  value: unknown,
): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value)
  );
}

function finite(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value)
  );
}

function validTime(value: unknown): value is string {
  return (
    typeof value === "string" &&
    Number.isFinite(Date.parse(value))
  );
}

function strings(value: unknown): boolean {
  return (
    Array.isArray(value) &&
    value.every((item) => typeof item === "string")
  );
}

function validMetrics(value: unknown): boolean {
  return (
    Array.isArray(value) &&
    value.every(
      (record) =>
        isObject(record) &&
        typeof record.ueId === "string" &&
        validTime(record.timestamp) &&
        Object.keys(METRIC_META).every((key) =>
          finite(record[key]),
        ),
    )
  );
}

function isDashboard(
  value: unknown,
): value is DashboardData {
  if (
    !isObject(value) ||
    !isObject(value.area) ||
    !isObject(value.radioMap)
  ) {
    return false;
  }

  const { nodes, links, radioMap, area } = value;

  if (
    !Array.isArray(nodes) ||
    !nodes.length ||
    !Array.isArray(links)
  ) {
    return false;
  }

  const validNodes = nodes.every(
    (node) =>
      isObject(node) &&
      typeof node.id === "string" &&
      (node.type === "ap" || node.type === "ue") &&
      finite(node.x) &&
      finite(node.y),
  );

  if (!validNodes) return false;

  const ids = nodes.map(
    (node) => (node as Record<string, unknown>).id,
  );

  if (new Set(ids).size !== ids.length) return false;

  return (
    (value.source === "simulation" ||
      value.source === "csv" ||
      value.source === "measurement") &&
    validTime(value.generatedAt) &&
    finite(value.sampleIntervalSeconds) &&
    value.sampleIntervalSeconds > 0 &&
    finite(area.width) &&
    area.width > 0 &&
    finite(area.height) &&
    area.height > 0 &&
    validMetrics(value.metrics) &&
    links.every(
      (link) =>
        isObject(link) &&
        typeof link.id === "string" &&
        nodes.some(
          (node) =>
            isObject(node) &&
            node.id === link.source &&
            node.type === "ap",
        ) &&
        nodes.some(
          (node) =>
            isObject(node) &&
            node.id === link.target &&
            node.type === "ue",
        ),
    ) &&
    radioMap.kind === "sampled" &&
    radioMap.metric === "rssiDbm" &&
    validTime(radioMap.timestamp) &&
    Array.isArray(radioMap.points) &&
    radioMap.points.every(
      (point) =>
        isObject(point) &&
        finite(point.x) &&
        finite(point.y) &&
        finite(point.value),
    )
  );
}

function isQuery(
  value: unknown,
): value is QueryResponse {
  if (
    !isObject(value) ||
    typeof value.message !== "string" ||
    typeof value.targetId !== "string"
  ) {
    return false;
  }

  const validMode =
    value.mode === "demo" ||
    value.mode === "llm" ||
    value.mode === "rule";

  const validView =
    value.view === "metrics" ||
    value.view === "analysis" ||
    value.view === "topology";

  if (
    !validMode ||
    !validView ||
    !validMetrics(value.metrics)
  ) {
    return false;
  }

  if (value.analysis === null) {
    return value.view !== "analysis";
  }

  const analysis = value.analysis;

  return (
    isObject(analysis) &&
    analysis.targetId === value.targetId &&
    typeof analysis.observation === "string" &&
    typeof analysis.isExample === "boolean" &&
    Array.isArray(analysis.causes) &&
    analysis.causes.every(
      (cause) =>
        isObject(cause) &&
        typeof cause.id === "string" &&
        typeof cause.title === "string" &&
        strings(cause.support) &&
        strings(cause.against) &&
        strings(cause.missing) &&
        typeof cause.nextAction === "string",
    )
  );
}

// 统一处理请求、超时和 HTTP 错误。
async function fetchJson(
  path: string,
  options: ApiOptions,
  body?: QueryRequest,
): Promise<unknown> {
  const base = (
    options.baseUrl ?? API_CONFIG.baseUrl
  ).replace(/\/$/, "");

  const controller = new AbortController();

  const timer = setTimeout(
    () => controller.abort(),
    15000,
  );

  try {
    const response = await fetch(base + path, {
      method: body ? "POST" : "GET",
      ...(body
        ? {
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify(body),
          }
        : {}),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(
        `接口请求失败（HTTP ${response.status}）。`,
      );
    }

    return await response.json();
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error("请求超时，请稍后重试。", { cause: error });
    }

    throw error instanceof Error
      ? error
      : new Error("网络请求失败。");
  } finally {
    clearTimeout(timer);
  }
}

// 获取工作台数据。
export async function getDashboard(
  options: ApiOptions = {},
): Promise<DashboardData> {
  const mode = options.mode ?? API_CONFIG.mode;

  if (mode === "mock") {
    return createMockDashboard();
  }

  const data = await fetchJson(
    PATHS.dashboard,
    options,
  );

  if (!isDashboard(data)) {
    throw new Error(
      "工作台数据格式不符合前端契约，请检查接口返回值。",
    );
  }

  return data;
}

// 发送自然语言查询。
export async function queryNetwork(
  request: QueryRequest,
  options: ApiOptions = {},
): Promise<QueryResponse> {
  const message = request.message.trim();

  if (!message) {
    throw new Error("请输入查询内容。");
  }

  if (message.length > 1000) {
    throw new Error(
      "查询内容请控制在 1000 字以内。",
    );
  }

  const payload = {
    ...request,
    message,
  };

  const mode = options.mode ?? API_CONFIG.mode;

  if (mode === "mock") {
    return createMockQuery(payload);
  }

  const data = await fetchJson(
    PATHS.query,
    options,
    payload,
  );

  if (!isQuery(data)) {
    throw new Error(
      "查询结果格式不符合前端契约，请检查接口返回值。",
    );
  }

  return data;
}