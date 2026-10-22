"""区域电磁态势的帧序列预测（空间维度）。

背景
----
原来的预测只有频率维度（逐频段），呈现成一条横纹带。申报书要的是「预测覆盖区域
电磁**频谱态势**」+「序列态势图」，参考同类系统的呈现是"过去N帧 → 未来N帧"的
二维区域图。所以这里补上空间维度的帧序列。

做法
----
1. 每个时间窗内，把 UE 采样点 (x, y, 指标值) 用 IDW（反距离加权）插成 grid×grid 的场；
2. 过去帧直接由真实数据算出；
3. 未来帧对**每个格点**分别外推：斜率取最近若干帧的最小二乘，再走饱和曲线，
   所以整片"恶化区域"会随时间扩张/加深，而不是整场平移。

为什么用 IDW 而不是 Kriging：3 个采样点做 Kriging 需要拟合变差函数，样本太少且
每帧都要拟合 11 次；IDW 无参数、必定有输出、结果连续平滑，足够表达"哪个区域更差"。
"""
from __future__ import annotations

import math

GRID = 26          # 每边格点数：26×26=676，足够看出区域形状，传输也不大
POWER = 2.0        # IDW 幂次：越大越"局地"，越小越平滑
TAU_FRAMES = 3.0   # 饱和外推的时间常数（帧）
MAX_IDW_POINTS = 400  # 单帧参与插值的采样点上限（超了就等距下采样，把耗时钉成常数）


def _idw(points: list[tuple[float, float, float]], xs: list[float], ys: list[float],
         power: float = POWER) -> list[list[float | None]]:
    """反距离加权插值：points=[(x, y, value)]，返回 len(ys)×len(xs) 的值场。

    采样点与格点重合时直接取该点值，避免除零。没有可用点时该格为 None。
    """
    if not points:
        return [[None] * len(xs) for _ in ys]
    grid: list[list[float | None]] = []
    for gy in ys:
        row: list[float | None] = []
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
            if exact is not None:
                row.append(exact)
            elif den > 0:
                row.append(num / den)
            else:
                row.append(None)
        grid.append(row)
    return grid


def build_grid(rows: list[dict], metric: str = "rsrp", grid: int = GRID) -> dict:
    """把一组采样行插成二维场，返回 {values, x_range, y_range, grid, points}。"""
    pts = [(float(r["ue_x"]), float(r["ue_y"]), float(r[metric]))
           for r in rows if r.get(metric) is not None]
    if not pts:
        return {"values": [], "x_range": [0, 0], "y_range": [0, 0], "grid": grid, "points": 0}

    # IDW 的复杂度是 O(输入点 × 格数)，格数固定 676，所以耗时只随采样点线性增长。
    # 历史窗口拉长到十几小时时点数会到上万，实测 1920 分钟要 1.5 秒（拖动会卡）。
    # 这里做等距下采样把点数钉在 MAX_IDW_POINTS：空间覆盖基本不变（点本身高度冗余），
    # 但耗时从线性无界变成常数级。
    total_points = len(pts)
    if total_points > MAX_IDW_POINTS:
        step = total_points / MAX_IDW_POINTS
        pts = [pts[int(i * step)] for i in range(MAX_IDW_POINTS)]

    xs_all = [p[0] for p in pts]
    ys_all = [p[1] for p in pts]
    # 采样点分布范围外留 5% 边距，避免边缘插值外推
    pad_x = max(1.0, (max(xs_all) - min(xs_all)) * 0.05)
    pad_y = max(1.0, (max(ys_all) - min(ys_all)) * 0.05)
    x0, x1 = min(xs_all) - pad_x, max(xs_all) + pad_x
    y0, y1 = min(ys_all) - pad_y, max(ys_all) + pad_y
    xs = [x0 + (x1 - x0) * i / (grid - 1) for i in range(grid)]
    ys = [y0 + (y1 - y0) * i / (grid - 1) for i in range(grid)]
    values = _idw(pts, xs, ys)
    return {
        "values": values,
        "x_range": [round(x0, 1), round(x1, 1)],
        "y_range": [round(y0, 1), round(y1, 1)],
        "grid": grid,
        "points": total_points,
        "points_used": len(pts),
    }


def _cell_slope(series: list[float | None]) -> float:
    """单个格点在若干帧上的斜率（单位/帧）。"""
    pts = [(i, v) for i, v in enumerate(series) if v is not None]
    if len(pts) < 3:
        return 0.0
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    den = sum((p[0] - mx) ** 2 for p in pts)
    if den <= 1e-9:
        return 0.0
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / den


def predict_frames(grids: list[list[list[float | None]]], future: int = 3,
                   max_drift: float | None = None,
                   bounds: tuple[float, float] | None = None) -> list[list[list[float | None]]]:
    """逐格点外推未来帧：返回 future 帧，每帧与输入同形状。

    斜率按格点各自计算 —— 这是"恶化区域扩张"能被看出来的关键：
    故障 AP 周边格点变化率大，远处接近 0，于是未来帧的差异在空间上分布开。
    """
    if not grids or future <= 0:
        return []
    rows, cols = len(grids[0]), len(grids[0][0])
    out: list[list[list[float | None]]] = []
    slopes = [[_cell_slope([g[r][c] for g in grids]) for c in range(cols)] for r in range(rows)]
    last = grids[-1]

    # 若任一格点的外推会超出 max_drift，按峰值等比缩放，避免全场撞上限被夹平
    scale = 1.0
    if max_drift is not None:
        total = TAU_FRAMES * (1.0 - math.exp(-future / TAU_FRAMES))
        peak = max((abs(slopes[r][c]) for r in range(rows) for c in range(cols)), default=0.0) * total
        if peak > max_drift * 0.8 > 0:
            scale = (max_drift * 0.8) / peak

    for k in range(1, future + 1):
        factor = TAU_FRAMES * (1.0 - math.exp(-k / TAU_FRAMES))
        frame: list[list[float | None]] = []
        for r in range(rows):
            row: list[float | None] = []
            for c in range(cols):
                base = last[r][c]
                if base is None:
                    row.append(None)
                    continue
                value = base + slopes[r][c] * factor * scale
                if max_drift is not None:
                    value = max(base - max_drift, min(base + max_drift, value))
                if bounds is not None:
                    value = max(bounds[0], min(bounds[1], value))
                row.append(round(value, 2))
            frame.append(row)
        out.append(frame)
    return out
