"""信号导航：位置感知 + 覆盖分析 + 移动建议。

分工文档 P1 的原始要求是「信号导航决策规则（位置感知、覆盖分析与移动建议）」。
本模块只做一件事：给定用户（UE）当前位置，在区域信号场上寻找附近更好的位置，
给出**方向 + 距离 + 预期增益**。

为什么用"区域场"而不是"逐个 AP 距离"来算：
  覆盖好坏是多个 AP 叠加的结果，单看最近 AP 会误判（近但被遮挡 vs 远但视距好）。
  这里用与其他模块一致的 IDW 插值场，保证"导航建议"与"区域态势图"口径一致——
  否则用户会看到"图上这里是绿的，导航却让我走开"的矛盾。

注意：这是**建议**不是指令。移动后的实际信号还受遮挡、负载、终端能力影响，
所以输出带 assumptions 字段，前端应原样展示，不要包装成确定性结论。
"""
from __future__ import annotations

import math

GRID = 26
POWER = 2.0
RADII = (10, 20, 30, 50)          # 候选移动距离（米）
DIRECTIONS = ("东", "东北", "北", "西北", "西", "西南", "南", "东南")


def _idw(points, xs, ys, power: float = POWER):
    grid = []
    for gy in ys:
        row = []
        for gx in xs:
            num = den = 0.0
            exact = None
            for px, py, pv in points:
                d2 = (gx - px) ** 2 + (gy - py) ** 2
                if d2 < 1e-6:
                    exact = pv
                    break
                w = 1.0 / (d2 ** (power / 2.0))
                num += w * pv
                den += w
            row.append(exact if exact is not None else (num / den if den > 0 else None))
        grid.append(row)
    return grid


def _axes(lo: float, hi: float, grid: int):
    return [lo + (hi - lo) * i / (grid - 1) for i in range(grid)]


def _cell(grid, xs, ys, x: float, y: float):
    """返回 (value, row, col)。取最近格点——场的分辨率是米级，无需双线性。"""
    ci = min(range(len(xs)), key=lambda i: abs(xs[i] - x))
    ri = min(range(len(ys)), key=lambda i: abs(ys[i] - y))
    return grid[ri][ci], ri, ci


def suggest(rows: list[dict], ue_id: str | None = None, metric: str = "rsrp",
            grid: int = GRID) -> dict:
    """给出移动建议。rows 需含 ue_x/ue_y/ue_id + metric 列。"""
    usable = [r for r in rows if r.get(metric) is not None]
    if not usable:
        return {"available": False, "reason": "该时段没有可用的采样点"}

    # 网络表的用户字段叫 user_id（数据字典口径）；ue_id 作为兜底，便于调用方沿用图形口径
    def _uid(row: dict):
        return row.get("user_id") or row.get("ue_id")

    target = ue_id
    if target is None:
        ids = sorted({_uid(r) for r in usable if _uid(r)})
        if not ids:
            return {"available": False, "reason": "样本缺少 user_id"}
        target = ids[0]

    mine = [r for r in usable if _uid(r) == target]
    if not mine:
        return {"available": False, "reason": f"该时段没有 {target} 的采样"}

    cur_x = sum(float(r["ue_x"]) for r in mine) / len(mine)
    cur_y = sum(float(r["ue_y"]) for r in mine) / len(mine)
    measured = sum(float(r[metric]) for r in mine) / len(mine)

    pts = [(float(r["ue_x"]), float(r["ue_y"]), float(r[metric])) for r in usable]
    xs_all = [p[0] for p in pts]
    ys_all = [p[1] for p in pts]
    pad = 5.0
    x0, x1 = min(xs_all) - pad, max(xs_all) + pad
    y0, y1 = min(ys_all) - pad, max(ys_all) + pad
    xs, ys = _axes(x0, x1, grid), _axes(y0, y1, grid)
    field = _idw(pts, xs, ys)

    current, _, _ = _cell(field, xs, ys, cur_x, cur_y)
    if current is None:
        return {"available": False, "reason": "当前位置无法从场中取值"}

    best = None
    for radius in RADII:
        for i, name in enumerate(DIRECTIONS):
            angle = math.radians(i * 45.0)
            tx = cur_x + radius * math.cos(angle)
            ty = cur_y + radius * math.sin(angle)
            if not (x0 <= tx <= x1 and y0 <= ty <= y1):
                continue          # 越出采样覆盖范围，不外推
            value, _, _ = _cell(field, xs, ys, tx, ty)
            if value is None:
                continue
            gain = value - current
            if best is None or gain > best["gain_db"]:
                best = {"gain_db": gain, "direction": name, "distance_m": radius,
                        "target_x": round(tx, 1), "target_y": round(ty, 1),
                        "expected": value}

    result = {
        "available": True,
        "ue_id": target,
        "metric": metric,
        "current": {"x": round(cur_x, 1), "y": round(cur_y, 1),
                    "field_value": round(current, 2), "measured_value": round(measured, 2)},
        "grid": {"grid": grid, "x_range": [round(x0, 1), round(x1, 1)],
                 "y_range": [round(y0, 1), round(y1, 1)]},
        "samples": len(mine),
        "assumptions": [
            "建议基于该时段区域信号场的空间分布，不是实时信道测量",
            "移动后的实际信号还受遮挡、邻区负载、终端能力影响",
            "仅在该时段采样覆盖范围内寻找位置，不外推到未知区域",
        ],
    }
    if best is None or best["gain_db"] < 1.0:
        result["advice"] = {
            "action": "stay",
            "text": "当前位置已接近该时段最优，不建议移动",
            "gain_db": round(max(0.0, (best or {}).get("gain_db", 0.0)), 2),
        }
    else:
        unit = "dBm" if metric == "rsrp" else ""
        result["advice"] = {
            "action": "move",
            "direction": best["direction"],
            "distance_m": best["distance_m"],
            "target_x": best["target_x"],
            "target_y": best["target_y"],
            "expected": round(best["expected"], 2),
            "gain_db": round(best["gain_db"], 2),
            "text": (f"建议向{best['direction']}移动约 {best['distance_m']} m，"
                     f"该位置信号场估值更高，预计提升 {best['gain_db']:.1f} dB"
                     f"（当前 {current:.1f}{unit} → 预计 {best['expected']:.1f}{unit}）"),
        }
    return result
