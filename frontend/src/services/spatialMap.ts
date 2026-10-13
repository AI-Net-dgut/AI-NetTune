import type { DashboardData, QueryScope, SpatialMapData, SpatialPoint } from "../types/backend";

function finite(value: unknown, label: string): number {
  if (typeof value !== "number" && (typeof value !== "string" || !value.trim())) {
    throw new Error(`空间接口 ${label} 无效`);
  }
  const number = Number(value);
  if (!Number.isFinite(number)) throw new Error(`空间接口 ${label} 无效`);
  return number;
}
function count(value: unknown, label: string) {
  const number = finite(value, label);
  if (!Number.isInteger(number) || number < 0) throw new Error(`空间接口 ${label} 无效`);
  return number;
}
const key = (x: number, y: number) => `${x},${y}`;

// Kriging 点位按规则格点中心绘制；从返回坐标推断格距。
// 单一坐标轴无法推断格距时沿用本接口20m默认值，需与空间模型确认。
function spacing(values: number[]) {
  const sorted = [...new Set(values)].sort((a, b) => a - b);
  let step = Infinity;
  for (let i = 1; i < sorted.length; i++) {
    const difference = sorted[i] - sorted[i - 1];
    if (difference > 1e-6) step = Math.min(step, difference);
  }
  return Number.isFinite(step) ? step : 20;
}

export function parseSpatialMap(value: unknown, scope: QueryScope): SpatialMapData {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("空间接口数据结构无效");
  const data = value as Record<string, unknown>;
  if (data.metric !== "rsrp") throw new Error("空间接口返回指标与请求的 RSRP 不一致");
  if (data.method !== "kriging" && data.method !== "grid_mean") throw new Error("空间接口缺少有效的 method（kriging / grid_mean）");
  if (!Array.isArray(data.values)) throw new Error("空间接口缺少 values 数组");
  const seen = new Set<string>();
  const points: SpatialPoint[] = data.values.map((point: unknown) => {
    if (!Array.isArray(point) || point.length !== 3) throw new Error("空间 values 应为 [x, y, 值] 数组");
    const x = finite(point[0], "x"), y = finite(point[1], "y"), signal = finite(point[2], "RSRP");
    if (seen.has(key(x, y))) throw new Error("空间接口存在重复坐标");
    seen.add(key(x, y));
    return { x, y, value: signal, variance: null };
  });
  const gridCount = count(data.grid_count, "grid_count");
  if (gridCount !== points.length) throw new Error("空间接口 grid_count 与 values 点数不一致");
  const samplePoints = count(data.sample_points, "sample_points");
  let varianceNotice = data.method === "grid_mean"
    ? "后端返回采样网格均值，未提供 Kriging 估计方差。"
    : "后端未返回方差，当前只能查看 RSRP 空间估计。";

  // 支持与values同序的一维方差，或带坐标的[x,y,方差]数组。
  // 二维矩阵的轴顺序未在上传代码中定义，不能猜测后将方差配错位置。
  if (data.method === "kriging" && data.variance != null && points.length) {
    try {
      const raw = data.variance;
      if (!Array.isArray(raw) || raw.length !== points.length) throw new Error("variance 点数不匹配");
      const variance = (v: unknown): number | null => {
        if (v == null) return null;
        const n = finite(v, "variance");
        if (n < 0) throw new Error("variance 含负值");
        return n;
      };
      let numbers: (number | null)[];
      if (raw.every(v => !Array.isArray(v))) {
        numbers = raw.map(variance);
      } else if (raw.every(v => Array.isArray(v) && v.length === 3)) {
        const indexed = new Map<string, number | null>();
        for (const item of raw as unknown[][]) {
          const coordinate = key(finite(item[0], "variance.x"), finite(item[1], "variance.y"));
          if (indexed.has(coordinate) || !seen.has(coordinate)) throw new Error("variance 坐标与 values 不匹配");
          indexed.set(coordinate, variance(item[2]));
        }
        numbers = points.map(point => indexed.get(key(point.x, point.y)) ?? null);
      } else throw new Error("variance 应为一维数组或 [x,y,方差] 数组");
      points.forEach((point, i) => { point.variance = numbers[i]; });
      varianceNotice = numbers.some(n => n === null) ? "部分点位缺少方差，方差图中留白。" : "";
    } catch (reason) {
      varianceNotice = `方差数据无法匹配：${reason instanceof Error ? reason.message : "格式无效"}。RSRP 估计仍可查看。`;
    }
  }
  return {
    scope: { ...scope }, metric: "rsrp", method: data.method, gridCount, samplePoints, points,
    cellSize: data.method === "grid_mean" ? { x: 20, y: 20 }
      : { x: spacing(points.map(p => p.x)), y: spacing(points.map(p => p.y)) },
    varianceNotice,
  };
}

export function getSpatialArea(spatial: SpatialMapData, base: DashboardData["area"]): DashboardData["area"] {
  const area = { ...base };
  for (const point of spatial.points) {
    area.xMin = Math.min(area.xMin, point.x - spatial.cellSize.x / 2);
    area.yMin = Math.min(area.yMin, point.y - spatial.cellSize.y / 2);
    area.xMax = Math.max(area.xMax, point.x + spatial.cellSize.x / 2);
    area.yMax = Math.max(area.yMax, point.y + spatial.cellSize.y / 2);
  }
  return area;
}
