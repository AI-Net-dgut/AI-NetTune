import type { DashboardData, NetworkNode } from "../types/backend";

// 场景静态几何配置，不从尚未播放的 UE 位置或指标推断边界。
// 当前七天校园演示的活动区域为 x=20~480、y=20~430m，四周保留20m边界。
const SCENE_AREAS: Record<string, DashboardData["area"]> = {
  校园: { xMin: 0, yMin: 0, xMax: 500, yMax: 450 },
};

export function sceneArea(scenario: string, nodes: NetworkNode[]): DashboardData["area"] {
  if (SCENE_AREAS[scenario]) return { ...SCENE_AREAS[scenario] };
  // 未配置场景按静态 AP 部署留边；不随 UE 移动收缩或放大。
  const aps = nodes.filter(node => node.type === "ap" && Number.isFinite(node.x) && Number.isFinite(node.y));
  if (!aps.length) return { xMin: 0, yMin: 0, xMax: 100, yMax: 100 };
  const xs = aps.map(node => node.x), ys = aps.map(node => node.y);
  const padding = Math.max(100, (Math.max(...xs) - Math.min(...xs)) / 2, (Math.max(...ys) - Math.min(...ys)) / 2);
  return {
    xMin: Math.floor((Math.min(...xs) - padding) / 20) * 20,
    yMin: Math.floor((Math.min(...ys) - padding) / 20) * 20,
    xMax: Math.ceil((Math.max(...xs) + padding) / 20) * 20,
    yMax: Math.ceil((Math.max(...ys) + padding) / 20) * 20,
  };
}
