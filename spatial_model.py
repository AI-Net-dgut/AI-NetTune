# spatial_model.py - 区域信号空间估计（P1 任务1：程思源）
# 职责：用 UE 采样点 (x, y, 指标值) 做 Kriging 空间插值，估计整片区域内未测位置的指标
# 方法：OrdinaryKriging（球面变差函数模型，参考 radiomap.online / cell-free 仓库 baseline/Kriging.py）
# 输出：全网格预测值 + 方差（方差 = 插值置信度，数值越小越可信）
# 降级：采样点不足或 Kriging 拟合失败返回 None，调用方退回"网格均值"（原热力图逻辑），接口永不崩
import numpy as np

# 与 forecast_model.py 对 prophet 的处理保持一致：缺包或二进制不兼容时，
# 只降级本模块（kriging_map 返回 None，调用方退回"网格均值"），
# 绝不让 from spatial_model import build_spatial 把整个后端拖崩。
try:
    from pykrige.ok import OrdinaryKriging
    _HAS_PYKRIGE = True
except Exception:  # ImportError；以及 numpy/scipy ABI 不匹配等
    OrdinaryKriging = None
    _HAS_PYKRIGE = False

# 与 main.py heatmap 口径保持一致
X_MIN, X_MAX, Y_MIN, Y_MAX = 20.0, 480.0, 20.0, 430.0
GRID = 20.0
MIN_SAMPLES = 30       # 少于 30 个采样点不做插值
MAX_SAMPLES = 2500     # 插值计算量较大，超过则均匀降采样（参考仓库 UE_train 2000 点）


def _extract_points(rows, metric):
    """从过滤后的数据行抽取 (x, y, metric) 采样点，坐标非法或指标缺失的丢弃"""
    pts = []
    for r in rows:
        try:
            pts.append((float(r["ue_x"]), float(r["ue_y"]), float(r[metric])))
        except (KeyError, TypeError, ValueError):
            continue
    return pts


def kriging_map(points, x_min=X_MIN, x_max=X_MAX, y_min=Y_MIN, y_max=Y_MAX,
                grid=GRID, max_samples=MAX_SAMPLES, min_samples=MIN_SAMPLES):
    """Kriging 空间插值 -> 全网格预测。
    返回 (cells, variances)：
      cells     = [[cx, cy, pred], ...]   全网格点，cx/cy 为网格中心坐标，pred 为插值预测值
      variances = [[cx, cy, var], ...]    同网格方差（置信度，越小越可信）
    失败返回 None（调用方降级 grid_mean）。
    """
    if not _HAS_PYKRIGE:
        return None
    if len(points) < min_samples:
        return None
    pts = np.asarray(points, dtype=float)
    if len(pts) > max_samples:  # 均匀降采样，保证计算可行
        rng = np.random.default_rng(0)
        idx = rng.choice(len(pts), max_samples, replace=False)
        pts = pts[idx]
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    try:
        ok = OrdinaryKriging(x, y, z, variogram_model="spherical",
                             verbose=False, enable_plotting=False)
        nx = int((x_max - x_min) // grid) + 1
        ny = int((y_max - y_min) // grid) + 1
        gridx = x_min + grid * np.arange(nx) + grid / 2   # 网格中心（与旧接口 cx 口径一致）
        gridy = y_min + grid * np.arange(ny) + grid / 2
        z_pred, ss = ok.execute("grid", gridx, gridy)      # (Ny, Nx)
    except Exception:
        return None

    cells = []
    variances = []
    for iy in range(ny):
        for ix in range(nx):
            cx = round(float(gridx[ix]), 1)
            cy = round(float(gridy[iy]), 1)
            cells.append([cx, cy, round(float(z_pred[iy, ix]), 2)])
            variances.append([cx, cy, round(float(ss[iy, ix]), 2)])
    return cells, variances


def build_spatial(rows, metric):
    """总入口：rows -> heatmap 契约扩展结构。
    返回 {"method": "kriging"|"grid_mean", "values": [...], "variance": [...]|None,
          "grid_count": n, "sample_points": n}"""
    points = _extract_points(rows, metric)
    kr = kriging_map(points)
    if kr is not None:
        cells, variances = kr
        return {
            "method": "kriging",
            "grid_count": len(cells),
            "values": cells,
            "variance": variances,
            "sample_points": len(points),
        }
    return {
        "method": "grid_mean",
        "grid_count": 0,
        "values": [],
        "variance": None,
        "sample_points": len(points),
    }
