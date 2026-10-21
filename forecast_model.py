# forecast_model.py —— 区域信号/频谱态势预测（P1 任务1：程思源）
# 职责：1小时聚合 → Prophet 时序预测（含置信区间）→ 契约兼容输出
# 设计：
#   - 输入：filter_rows 过滤后的原始行（与 main.py 同一数据源）
#   - 聚合：UE × 指标 × 1 小时桶（rsrp/sinr/throughput 均值）
#   - 训练/测试：前 80% 训练 Prophet，后 20% 测试 → true/predicted/MAPE
#   - 未来预测：测试段后再延伸 forecast_horizon 小时，输出 yhat/lower/upper
#   - 容错：序列过短或 Prophet 失败 → 自动降级 moving_average，保证接口永不崩
# 答辩口径：预测模块统一表述为"区域信号/频谱态势预测"，页面标注"未来预测"
import logging
import os
import threading
import time
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger("forecast_model")
logger.setLevel(logging.ERROR)

DEFAULT_METRICS = ["rsrp", "sinr", "throughput"]
TRAIN_RATIO = 0.8          # 前 80% 训练
FORECAST_HORIZON = 24      # 未来延伸 24 小时（1 天）

# Prophet 单次拟合的时间上限（秒）。**Prophet 本身是能用的**——本项目演示机
# 启动预热实测 2.1 秒即成功，接口10 全程走 prophet。这个超时是给"环境不在状态"兜底：
# 受限沙箱、首次 Stan 模型编译、缺 C++ 工具链等情况下，首次拟合可能长时间不返回
# （只读沙箱里实测过 400+ 秒无响应），而前端 45 秒就超时。
# 超时后本进程改用 moving_average，并在 limitations 里如实说明原因。
PROPHET_TIMEOUT_SEC = float(os.getenv("NETTUNE_PROPHET_TIMEOUT", "20"))
_prophet_state = {"disabled": False, "reason": ""}
# 预热状态：warming=True 期间到达的请求直接用移动平均，避免和预热线程抢着等超时
_warm_state = {"warming": False, "done": False, "ok": None}
_warm_lock = threading.Lock()

# Prophet 拟合失败时禁止输出优化日志刷屏
# 【Windows 修复】prophet 依赖 cmdstanpy，其 Windows 路径会执行 where.exe tbb.dll 探测；
# 在 detached/沙箱启动的进程（如 uvicorn -WindowStyle Hidden）里该探测输出解码异常导致
# CmdStanPyBackend 加载失败（Prophet object has no attribute 'stan_backend'）。
# 对策：导入 prophet 前先把自带 TBB 目录预置进 PATH，让探测直接命中、不走异常分支。
try:
    import importlib.resources

    _local_cmdstan = importlib.resources.files("prophet") / "stan_model" / "cmdstan-2.37.0"
    _tbb = _local_cmdstan / "stan" / "lib" / "stan_math" / "lib" / "tbb"
    if _tbb.is_dir():
        import os as _os
        _os.environ["PATH"] = str(_tbb) + _os.pathsep + _os.environ.get("PATH", "")
except Exception:
    pass

try:
    from prophet import Prophet
    _HAS_PROPHET = True
except Exception:
    _HAS_PROPHET = False


def _hour_bin(ts: str) -> str | None:
    """时间戳 → 1 小时桶（"2026-01-01 01:00"），解析失败返回 None"""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(minute=0, second=0).strftime("%Y-%m-%d %H:%M")


def aggregate_hourly(rows: list[dict], metrics: list[str]) -> dict:
    """rows → {ue: {metric: (bins[], vals[])}}，按 1 小时桶聚合均值，时间升序"""
    buckets = {}
    for r in rows:
        k = _hour_bin(r["timestamp"])
        if k is None:
            continue
        ue = r["user_id"]
        for m in metrics:
            try:
                v = float(r[m])
            except (KeyError, TypeError, ValueError):
                continue
            b = buckets.setdefault(ue, {}).setdefault(m, {})
            b.setdefault(k, []).append(v)
    out = {}
    for ue, by_metric in buckets.items():
        out[ue] = {}
        for m, grouped in by_metric.items():
            items = sorted(grouped.items())
            out[ue][m] = ([k for k, _ in items], [round(sum(vs) / len(vs), 2) for _, vs in items])
    return out


def _ma_fallback(vals: list[float], horizon: int, reason: str = "") -> dict:
    """降级：移动平均(win=3) 预测测试段均值 + 未来恒定预测，返回契约结构。

    reason 用于如实说明"为什么没用 Prophet"（超时/拟合失败/未安装），
    由接口 10 的 limitations 透出，避免"降级了却看不出原因"。
    """
    n = len(vals)
    if n < 4:
        return None
    split = max(2, int(n * TRAIN_RATIO))
    train, test = vals[:split], vals[split:]
    ma = train[-3:] if len(train) >= 3 else train
    pred_c = sum(ma) / len(ma)
    true_c = sum(test) / len(test)
    # 降级时也给出同一套误差指标：预测是常数，MAE/RMSE 就是测试段相对该常数的离散度
    metrics = _error_metrics(test, [pred_c] * len(test))
    return {
        "method": "moving_average",
        "true_mean": round(true_c, 2),
        "pred_mean": round(pred_c, 2),
        **metrics,
        # 测试段的常数预测：前端"真实 vs 预测"对比图需要它画预测线，
        # 缺了这条线图上就只剩实测，看不到对比（此前降级路径漏了这个字段）。
        "predicted": [round(pred_c, 2)] * len(test),
        "forecast": [round(pred_c, 2)] * horizon,
        "lower": [round(pred_c, 2)] * horizon,
        "upper": [round(pred_c, 2)] * horizon,
        "degrade_reason": reason,
    }


def _error_metrics(y_true, y_pred) -> dict:
    """测试段上的真实误差指标。

    * MAE / RMSE 有量纲（dBm / dB / Mbps），是对外展示的主指标；
    * MAPE 对 dBm 这类"负值且绝对值大"的指标基本没有意义（分母 -90 会让百分比极小），
      所以只在真值远离 0 时计算，并在文档里注明它不是主要依据。

    历史问题：旧实现把 method 里的 mape 算成"测试段均值差 ÷ 均值"，
    那是"两个均值的相对差"，不是逐点误差，却被前端标注为 MAPE 展示。
    """
    t = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    n = min(len(t), len(p))
    if n == 0:
        return {"mae": None, "rmse": None, "mape": None}
    t, p = t[:n], p[:n]
    diff = t - p
    mae = float(np.mean(np.abs(diff)))
    rmse = float(np.sqrt(np.mean(diff ** 2)))
    mask = np.abs(t) > 1e-6
    mape = float(np.mean(np.abs(diff[mask] / t[mask])) * 100) if mask.any() else None
    return {
        "mae": round(mae, 2),
        "rmse": round(rmse, 2),
        "mape": round(min(mape, 999.0), 2) if mape is not None else None,
    }


def _run_with_timeout(fn, timeout_sec: float):
    """在可丢弃的守护线程里执行 fn，超过 timeout 就放弃。

    Prophet 底层是 Stan（C++），Python 侧无法中断；但请求不能无限等——
    正常环境首次拟合 2 秒级即可返回，可受限沙箱/首次 Stan 编译等情况下
    可能长时间不返回（实测过 400+ 秒），而前端 45 秒就超时了。
    这里超时即放弃等待，并让上层降级。
    被放弃的线程是 daemon，不会阻止进程退出。
    """
    box: dict = {}

    def runner():
        try:
            box["value"] = fn()
        except BaseException as exc:      # noqa: BLE001 - 原样抛回调用方
            box["error"] = exc

    th = threading.Thread(target=runner, name="prophet-fit", daemon=True)
    th.start()
    th.join(timeout_sec)
    if th.is_alive():
        raise TimeoutError(f"Prophet 拟合超过 {timeout_sec:.0f} 秒仍未返回")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def prophet_series(bins: list[str], vals: list[float], horizon: int) -> dict:
    """单个 UE × 指标序列的 Prophet 预测。
    返回 {true_mean, pred_mean, mae, rmse, mape, predicted(测试段), forecast, lower, upper}"""
    n = len(vals)
    if n < 24:  # 至少 24 个点（1 天）才训练
        return _ma_fallback(vals, horizon)

    split = max(int(n * TRAIN_RATIO), n - horizon)
    train_df = pd.DataFrame({"ds": pd.to_datetime(bins[:split]), "y": vals[:split]})
    test_df = pd.DataFrame({"ds": pd.to_datetime(bins[split:]), "y": vals[split:]})

    if not _HAS_PROPHET or len(train_df) < 12:
        return _ma_fallback(vals, horizon)

    # 一旦本进程内某次拟合超时，后续全部直接降级：
    # 否则每个 UE×指标都要再等一次超时，接口 10 会变成"永远很慢"。
    if _prophet_state["disabled"]:
        return _ma_fallback(vals, horizon, _prophet_state["reason"])
    # 启动预热正在进行：本次先给移动平均，避免用户和预热线程一起等超时
    if _warm_state["warming"]:
        return _ma_fallback(vals, horizon, "模型预热中，本次先用移动平均")

    def fit_and_predict():
        m = Prophet(
            seasonality_mode="additive",
            daily_seasonality=True,
            weekly_seasonality=True,
            yearly_seasonality=False,   # 30 天数据无年周期
        )
        m.fit(train_df)
        pred = m.predict(test_df)
        # 未来延伸：从最后一个训练点起 +horizon 小时
        future = m.make_future_dataframe(periods=len(test_df) + horizon, freq="h")
        ffull = m.predict(future)
        return pred, ffull.iloc[-horizon:]

    try:
        pred, f_future = _run_with_timeout(fit_and_predict, PROPHET_TIMEOUT_SEC)

        y_true = np.array(vals[split:])
        y_pred = pred["yhat"].to_numpy()
        metrics = _error_metrics(y_true, y_pred)

        return {
            "method": "prophet",
            "true_mean": round(float(np.mean(y_true)), 2),
            "pred_mean": round(float(np.mean(y_pred)), 2),
            **metrics,
            "predicted": [round(float(v), 2) for v in y_pred],
            "forecast": [round(float(v), 2) for v in f_future["yhat"]],
            "lower": [round(float(v), 2) for v in f_future["yhat_lower"]],
            "upper": [round(float(v), 2) for v in f_future["yhat_upper"]],
        }
    except TimeoutError as e:
        _prophet_state["disabled"] = True
        _prophet_state["reason"] = str(e)
        logger.error("%s；本进程后续请求将直接使用 moving_average", e)
        return _ma_fallback(vals, horizon, str(e))
    except Exception as e:
        import traceback
        logger.error("prophet fit failed: %s\n%s", e, traceback.format_exc())
        return _ma_fallback(vals, horizon, f"Prophet 拟合失败：{e}")


def build_series(rows: list[dict], metrics: list[str] | None = None,
                 horizon: int = FORECAST_HORIZON) -> dict:
    """总入口：rows → 契约兼容的 series 结构。
    返回 {ues, metrics, bin_size_min, series:{ue:{m:{...}}}}"""
    metrics = metrics or DEFAULT_METRICS
    agg = aggregate_hourly(rows, metrics)
    ues = sorted(agg.keys())
    series = {}
    for ue, by_metric in agg.items():
        series[ue] = {}
        for m, (bins, vals) in by_metric.items():
            r = prophet_series(bins, vals, horizon)
            if r is None:
                series[ue][m] = {"method": "none", "true_mean": None, "pred_mean": None,
                                 "mae": None, "rmse": None, "mape": None,
                                 "bins": [], "true": [], "predicted": [],
                                 "forecast_bins": [], "forecast": [], "lower": [], "upper": []}
                continue
            # bins/true 全量输出（前端对比图用）；predicted 仅测试段
            series[ue][m] = {
                **r,
                "bins": bins,
                "true": vals,
                "forecast_bins": make_future_bins(bins[-1], horizon),
            }
    return {"ues": ues, "metrics": metrics, "bin_size_min": 60, "series": series}


def make_future_bins(last_bin: str, horizon: int) -> list[str]:
    """未来桶时间标签（供 forecast_bins 使用）"""
    dt = pd.to_datetime(last_bin)
    return [(dt + pd.Timedelta(hours=h + 1)).strftime("%Y-%m-%d %H:%M") for h in range(horizon)]


def warm_up() -> None:
    """启动时在后台预热一次 Prophet，把首次拟合的开销从"用户请求"挪到"服务启动"。

    背景：Prophet 首次拟含有一次性开销（Stan 模型编译），演示机实测预热 2.1 秒成功；
    若环境不在状态（受限沙箱、缺 C++ 工具链等）则可能长时间不返回（实测过 400+ 秒）。
    若不预热，用户第一次打开"预测对比"要干等一个超时周期；预热失败则本进程永久
    降级为 moving_average，之后的请求都是毫秒级。

    预热期间到达的请求会直接走移动平均（见 prophet_series 里的 _warm_state 判断），
    不会和预热线程抢着等超时。
    """
    if not _HAS_PROPHET:
        print("[forecast] 未安装 prophet：接口10 始终使用 moving_average")
        _warm_state["done"] = True
        return

    def run():
        with _warm_lock:
            _warm_state["warming"] = True
        started = time.time()
        try:
            ds = pd.date_range("2026-01-01", periods=48, freq="h")
            y = 10.0 + np.sin(np.arange(48) / 5.0)
            frame = pd.DataFrame({"ds": ds, "y": y})

            def fit_once():
                model = Prophet(daily_seasonality=True, weekly_seasonality=False,
                                yearly_seasonality=False)
                model.fit(frame)
                model.predict(frame.tail(2))

            _run_with_timeout(fit_once, PROPHET_TIMEOUT_SEC)
            _warm_state["ok"] = True
            print(f"[forecast] Prophet 预热成功（{time.time() - started:.1f}s），接口10 使用 prophet")
        except Exception as exc:      # noqa: BLE001 - 任何失败都只降级，不影响启动
            _prophet_state["disabled"] = True
            _prophet_state["reason"] = f"启动预热失败：{exc}"
            _warm_state["ok"] = False
            print(f"[forecast] Prophet 预热未通过（{time.time() - started:.1f}s）：{exc}")
            print("[forecast] 本进程内接口10 直接使用 moving_average；"
                  "如需再试可调大 NETTUNE_PROPHET_TIMEOUT 后重启")
        finally:
            with _warm_lock:
                _warm_state["warming"] = False
                _warm_state["done"] = True

    threading.Thread(target=run, name="prophet-warmup", daemon=True).start()
