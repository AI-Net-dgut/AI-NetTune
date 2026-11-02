"""仿真效果计算引擎 —— **唯一**的参数→指标转换实现。

为什么必须有这个模块（第二轮复查 P0）：
    plan_model 用 `(cur - new) * 1.2` 算下倾角收益，sim_config 当时用 `(new - cur) * 0.5`，
    方向相反、量级不同 —— 于是"评估改善 3.6 dB、应用反而恶化 1.5 dB"。
    只要存在两套系数，这种自相矛盾就一定会复发。因此：

    评估（方案预计效果）与生效（配置应用到仿真网络）**都只能调用本模块**。

契约（逐字遵守）：
    view_row = transform_network_row(raw_row, target, baseline)

- 不修改 raw_row，也不读取"上一次转换结果"或任何隐藏状态；
- 相同 (raw_row, target, baseline, ALGO_VERSION) 必然得到相同数值；
- target 必须是**完整目标状态**（缺失字段由 DEFAULT_PARAMS 补齐），不是增量；
- 幂等：同一 target 连续应用 N 次，偏移量恒等于一次的效果（不累加）；
- 缺失指标保持缺失，不自动变成 0。

模型范围（如实声明，不许夸大）：
- 建模：`rsrp`（受 tilt / tx_power / beam_gain 影响）与 `prb_usage`（受 capacity / offload_pct 影响）。
- **不建模**：sinr、throughput、latency、packet_loss、频谱 —— 与 plan_model 的既有假设保持一致。
  早期版本曾给 throughput 拍过一组系数，导致生效后出现负吞吐（复查 P0-2），已移除。
- 系数（1.2 / 1.0 / 0.8 / 18）是**演示用经验假设**，不是现场标定结果。
"""
from __future__ import annotations

import math

# 算法版本：进缓存键、进结果、进导出，避免"同一窗口不同算法结果混用"
ALGO_VERSION = "sim-v1"

# 演示用工参基线（非现场标定）。接入真实数据后应改为按 AP 保存的原始工参。
DEFAULT_PARAMS: dict = {
    "tilt": 8.0,
    "tx_power": 33.0,
    "beam_gain": 0.0,
    "capacity": "none",
    "offload_pct": 0.0,
}

# 枚举参数到数值档位的显式映射（禁止用 Number() 硬转，否则 "one_carrier" → NaN）
CARRIER_COUNT = {"none": 0, "one_carrier": 1, "two_carriers": 2}

# 与 plan_model 完全同源的系数（唯一来源，禁止在别处再定义一套）
TILT_DB_PER_DEGREE = 1.2      # 下倾角每减小 1° → RSRP +1.2 dB
POWER_DB_PER_DB = 1.0         # 发射功率每 +1 dB → RSRP +1 dB
PRB_PER_OFFLOAD_PCT = 0.8     # 分流比例每 +1% → 本区 PRB −0.8 个百分点
CAPACITY_PRB_DROP = 18.0      # 每新增 1 个载波 → PRB −18 个百分点

# 参数合法范围（校验用；与方案契约一致）
PARAM_RANGES: dict = {
    "tilt": (0.0, 15.0, 0.5),
    "tx_power": (10.0, 46.0, 0.5),
    "beam_gain": (-10.0, 20.0, 0.5),
    "offload_pct": (0.0, 100.0, 1.0),
}

# 指标合法边界：转换后必须落在此范围内（不是"前端隐藏负值"，而是源头不出非法值）
METRIC_BOUNDS: dict = {
    "rsrp": (-140.0, -20.0),
    "sinr": (-20.0, 40.0),
    "prb_usage": (0.0, 100.0),
    "packet_loss": (0.0, 100.0),
    "throughput": (0.0, None),
    "latency": (0.0, None),
}


class ParamError(ValueError):
    """参数非法：调用方应转成明确的接口错误，而不是静默忽略。"""


# 兼容别名：早期接口/脚本用 power，规范化到 tx_power（避免"缺参数"导致按钮永远禁用）
PARAM_ALIASES = {"power": "tx_power"}


def normalize_target(params: dict | None, current: dict | None = None) -> dict:
    """把"部分参数"补全成**完整目标状态**：默认值 ∪ 当前目标 ∪ 本次改动。

    语义（与前端约定一致）：缺省字段沿用 current（没有则用 DEFAULT_PARAMS），
    因此"只改倾角"不会把功率意外重置回默认。
    """
    merged = dict(DEFAULT_PARAMS)
    for source in (current or {}, params or {}):
        for key, value in source.items():
            if key in DEFAULT_PARAMS:
                merged[key] = value
    return merged


def validate_parameters(params: dict | None) -> dict:
    """校验类型、范围、枚举与非有限值；返回规整后的 dict。非法即抛 ParamError。"""
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise ParamError("参数必须是键值对象")
    out: dict = {}
    for key, raw in params.items():
        key = PARAM_ALIASES.get(key, key)
        if key not in DEFAULT_PARAMS:
            raise ParamError(f"不支持的参数：{key}（支持 {', '.join(sorted(DEFAULT_PARAMS))}）")
        if isinstance(DEFAULT_PARAMS[key], str):     # 枚举
            allowed = set(CARRIER_COUNT)
            text = str(raw)
            if text not in allowed:
                raise ParamError(f"{key} 取值必须是 {', '.join(sorted(allowed))}，收到 {raw!r}")
            out[key] = text
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ParamError(f"{key} 必须是数值，收到 {raw!r}") from None
        if not math.isfinite(value):
            raise ParamError(f"{key} 不能是 NaN 或无穷大")
        lo, hi, step = PARAM_RANGES.get(key, (None, None, None))
        if lo is not None and value < lo:
            raise ParamError(f"{key} 不能小于 {lo}（收到 {value}）")
        if hi is not None and value > hi:
            raise ParamError(f"{key} 不能大于 {hi}（收到 {value}）")
        if step:
            steps = round((value - (lo if lo is not None else 0.0)) / step)
            snapped = (lo if lo is not None else 0.0) + steps * step
            if abs(snapped - value) > 1e-9:
                raise ParamError(f"{key} 必须以 {step} 为步长（收到 {value}）")
        out[key] = value
    return out


def calculate_effects(baseline: dict | None, target: dict | None) -> dict:
    """参数变化 → 指标偏移。**评估与生效共用**，返回 {指标: 偏移}。

    只返回被建模的指标（rsrp / prb_usage）；未建模的指标不出现在结果里，
    调用方不得自行假设它们会变化。
    """
    base = normalize_target(None, baseline)
    targ = normalize_target(None, target or {})
    d_rsrp = 0.0
    d_prb = 0.0
    # 下倾角：减小 → 覆盖变好（方向取自 plan_model 的既有假设）
    d_rsrp += (base["tilt"] - targ["tilt"]) * TILT_DB_PER_DEGREE
    # 发射功率 / 波束增益：增大 → RSRP 增大
    d_rsrp += (targ["tx_power"] - base["tx_power"]) * POWER_DB_PER_DB
    d_rsrp += (targ["beam_gain"] - base["beam_gain"]) * POWER_DB_PER_DB
    # 载波数：每多一个载波，本区 PRB 下降一个档位
    d_prb -= (CARRIER_COUNT[targ["capacity"]] - CARRIER_COUNT[base["capacity"]]) * CAPACITY_PRB_DROP
    # 分流比例：按**目标值**折算（沿用 plan_model 假设）
    d_prb -= targ["offload_pct"] * PRB_PER_OFFLOAD_PCT
    d_prb += base["offload_pct"] * PRB_PER_OFFLOAD_PCT
    return {"rsrp": d_rsrp, "prb_usage": d_prb}


def _clamp(metric: str, value: float) -> float:
    lo, hi = METRIC_BOUNDS.get(metric, (None, None))
    if lo is not None and value < lo:
        value = lo
    if hi is not None and value > hi:
        value = hi
    return value


def _as_float(value) -> float | None:
    """CSV 解析出来的是字符串；缺失/非法一律返回 None（保持缺失，不变成 0）。"""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def transform_network_row(raw_row: dict, target: dict | None, baseline: dict | None = None) -> dict:
    """对单条原始采样应用目标配置，返回**新行**（不改 raw_row）。

    纯函数：同输入必得同输出；幂等：在同一 raw_row 上重复调用结果不变。
    """
    effects = calculate_effects(baseline, target)
    if not effects:
        return dict(raw_row)
    view = dict(raw_row)
    for metric, delta in effects.items():
        if metric not in view:
            continue
        base_value = _as_float(view[metric])
        if base_value is None:
            continue                      # 缺失保持缺失
        view[metric] = round(_clamp(metric, base_value + delta), 2)
    return view


def transform_spectrum_frame(frame: dict, target: dict | None = None, baseline: dict | None = None) -> dict:
    """频谱帧转换 —— 本轮为**恒等**。

    如实声明：配置生效模拟只覆盖网络 KPI；频谱预测与预警继续使用原始频谱，
    不模拟本次配置对频谱的影响。此处返回原帧并标记 simulated=False，
    避免"看起来变了其实没建模"的误导。
    """
    view = dict(frame)
    view["simulated"] = False
    view["configNote"] = "频谱未随配置变化（本轮未建模）"
    return view
