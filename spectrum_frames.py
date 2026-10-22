"""频谱态势的帧序列预测（路线 A：事件驱动帧演化）。

为什么需要这个模块
------------------
原来接口 11 的频谱预测是**逐频段标量趋势外推**：对每个 band 拟合历史斜率，
过 `_gated_slope` 显著性门控后外推。问题在于：

* 门控按单 band 判定，噪声一大就把斜率判成 0 → 未来帧 = 最后一帧的 K 次复制，
  时频图上表现为一整块纯色（用户实测截图确实如此）；
* 即便斜率存活，逐 band 独立外推也预测不出频谱的**形态演化**
  （干扰局部凹陷随时间扩展/移动）。

申报书要的是「预测覆盖区域电磁频谱态势」+「序列态势图」（见申报书 L17/L19），
参考形态是"过去N帧 → 未来N帧"的离散帧序列。所以这里改按**场**预测：

1. 对每一帧内的 (AP × band) 场求差分，得到"变化率场"，再做**跨频段平滑**，
   避免单频段噪声把整个场判成静止；
2. 用场的能量决定阶段（stable / degrading / recovering）；
3. 用**饱和外推**（指数趋近）推进未来帧，斜率来自平滑后的变化率场，
   因此保持并延续频谱形态，而不是压成常数。

这是"物理演化模板"而不是学习模型：优点是无需训练、可解释、必定有输出；
后续若接入时空预测模型（ConvLSTM 等），本模块可作为其不可用时的降级路径。
"""
from __future__ import annotations

import math

# 变化率场低于该值（dB/分钟）就认为场是静止的，未来帧沿用当前形态
ENERGY_EPS = 0.02
# 饱和外推的时间常数（分钟）：与数据生成器的渐变时长（20 分钟）对齐
TAU_MINUTES = 20.0
# 变化率场的跨频段平滑窗口（频段数，奇数）
SMOOTH_BANDS = 5
# 细节回填强度：平滑只用来压噪声，频段间原有差异要按比例加回去，
# 否则整场被压成"齐平下降"，图上仍是均匀变色而看不出形态演化。
DETAIL_GAIN = 0.6
# 形态放大：当前偏离场均值的频段，其变化率再放大一点，
# 让已有的凹陷/凸起随时间继续扩展（对应参考图里干扰斑块的生长）。
SHAPE_GAIN = 0.35


def _smooth(values: list[float], window: int = SMOOTH_BANDS) -> list[float]:
    """跨频段滑动平均：保留主导形态，压掉单频段噪声。"""
    n = len(values)
    if n == 0:
        return []
    half = max(0, window // 2)
    out = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        chunk = values[lo:hi]
        out.append(sum(chunk) / len(chunk))
    return out


def field_from_slopes(slopes: list[float], values: list[float] | None = None) -> list[float]:
    """把逐频段斜率整理成"变化率场"。

    三步（这是形态能否演化的关键）：
      1. 跨频段平滑压噪声；
      2. 把原始斜率相对平滑均值的**细节按 DETAIL_GAIN 加回去**，
         避免整场被压成齐平下降；
      3. 若给了当前帧数值，按"离场均值的偏离度"再放大（SHAPE_GAIN），
         让已经存在的凹陷/凸起随时间继续生长。

    没有第 3 步时，未来帧只是整体平移，视觉上与"整块变色"差别不大。
    """
    if not slopes:
        return []
    smoothed = _smooth(slopes, SMOOTH_BANDS if len(slopes) > SMOOTH_BANDS else 3)
    mean_sm = sum(smoothed) / len(smoothed)
    field = [smoothed[i] + (slopes[i] - mean_sm) * DETAIL_GAIN for i in range(len(slopes))]

    if values:
        n = min(len(field), len(values))
        mean_v = sum(values[:n]) / n
        spread = max((abs(v - mean_v) for v in values[:n]), default=0.0)
        if spread > 1e-6:
            for i in range(n):
                deviation = (values[i] - mean_v) / spread      # [-1, 1]
                field[i] *= (1.0 + SHAPE_GAIN * deviation)
    return field


def _slope_per_minute(times_min: list[float], values: list[float]) -> float:
    """最小二乘斜率（单位/分钟）。点不足或时间为常数时返回 0。"""
    n = min(len(times_min), len(values))
    if n < 3:
        return 0.0
    xs, ys = times_min[:n], values[:n]
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 1e-9:
        return 0.0
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom


def detect_phase(frames: list[list[float]]) -> dict:
    """判定当前阶段，供前端展示与预测强度调节。

    frames: 按时间升序的若干帧，每帧是该 AP 各频段的数值列表。
    返回 {phase, energy, note}，phase ∈ stable / degrading / recovering。
    """
    valid = [f for f in frames if f and all(v is not None for v in f)]
    if len(valid) < 4:
        return {"phase": "stable", "energy": 0.0, "note": "历史帧不足，按静止场处理"}

    n_bands = min(len(f) for f in valid)
    half = len(valid) // 2
    first = [sum(f[b] for f in valid[:half]) / half for b in range(n_bands)]
    last = [sum(f[b] for f in valid[half:]) / (len(valid) - half) for b in range(n_bands)]

    deltas = _smooth([last[b] - first[b] for b in range(n_bands)])
    energy = max((abs(d) for d in deltas), default=0.0)
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0

    if energy < ENERGY_EPS * max(1.0, len(valid)):
        phase = "stable"
        note = "变化率场接近静止，未来帧基本沿用当前形态"
    elif mean_delta < 0:
        phase = "degrading"
        note = "字段整体走低，按饱和外推延续下降趋势"
    else:
        phase = "recovering"
        note = "字段整体回升，按饱和外推延续回升趋势"
    return {"phase": phase, "energy": round(energy, 3), "note": note}


def predict_frames(times_min: list[float], frames: list[list[float]],
                   step_minutes: int = 1, horizon_minutes: int = 30,
                   bounds: tuple[float, float] | None = None) -> list[list[float]]:
    """帧序列预测：返回未来 (horizon_minutes // step_minutes) 帧的完整场。

    核心：变化率场 d_b 由"每频段斜率 → 跨频段平滑"得到，
    再按饱和曲线 v_k = v_last + d_b * τ * (1 - e^(-k/τ)) 推进。
    饱和项保证不会线性外推到荒谬值，同时保留各频段的相对差异（形态）。
    """
    valid = [f for f in frames if f and all(v is not None for v in f)]
    if not valid or not times_min:
        return []

    n_bands = min(len(f) for f in valid)
    # 每频段斜率（单位/分钟）
    slopes = [_slope_per_minute(times_min, [f[b] for f in valid]) for b in range(n_bands)]
    last = list(valid[-1][:n_bands])
    # 变化率场：平滑 + 细节回填 + 结构放大（见 field_from_slopes 注释）
    field = field_from_slopes(slopes, last)

    # 场能量太小时直接保持当前形态（不制造假趋势）
    energy = max((abs(d) for d in field), default=0.0)
    if energy < ENERGY_EPS:
        field = [0.0] * n_bands

    steps = max(1, int(horizon_minutes // step_minutes))
    out: list[list[float]] = []
    for k in range(1, steps + 1):
        minutes = k * step_minutes
        factor = TAU_MINUTES * (1.0 - math.exp(-minutes / TAU_MINUTES))
        row = []
        for b in range(n_bands):
            value = last[b] + field[b] * factor
            if bounds is not None:
                value = max(bounds[0], min(bounds[1], value))
            row.append(round(value, 2))
        out.append(row)
    return out
