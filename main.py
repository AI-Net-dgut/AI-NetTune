# main.py —— AI-NetTune 后端：4个接口（第2周版：已接入唐苗仿真数据集 v2.3）
# 数据源：data/network_data.csv（12960条，18列，含坐标/network_status/3个辅助字段）
#   - 接口1/接口2：已从 mock 切换为 CSV 真实查询 + 多KPI规则研判
#   - 接口3：LLM 意图解析 + 工具编排
#   - 接口4：报告生成占位（第3周接入真实生成）
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import datetime
import csv as _csv
import statistics
import json as _json
import re
import math
import os
import time
import uuid
import copy as _copy
import bisect as _bisect
from pathlib import Path
from datetime import datetime, timedelta
import llm_agent
from llm_agent import get_intent, rule_intent, extract_time_range, call_llm_chat
import forecast_model as _fm
from forecast_model import build_series, DEFAULT_METRICS
import spatial_model as _sm
from spatial_model import build_spatial
import plan_model
import report_model
import spectrum_frames as _sf
import spatial_frames as _spf
import signal_nav as _sn
# 【已移除】from realtime_simulator import start_realtime_service, get_incremental_data, DATA_COLUMNS
#   B-1 删除 /api/data/realtime 接口后，这三个符号在本文件中的使用数均为 0，
#   这行 import 已无意义；先删导入、再删 realtime_simulator.py（否则删文件会 ModuleNotFoundError）。
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from datetime import datetime, timedelta
from fastapi.staticfiles import StaticFiles
from data_layout import (
    BASE_DIR, DATA_DIR, NETWORK_CANDIDATES, NETWORK_COLUMNS,
    SPECTRUM_CANDIDATES, resolve_data_file,
)

# 项目根目录由 data_layout 统一定义：所有运行时路径都以它为准。
# 不用相对路径，否则启动时 cwd 不同会导致"数据读不到 / 报告写到别处 / 下载 404"。
# NETTUNE_REPORTS_DIR 可把报告目录指到别处：装在 Program Files 或只读容器里时必需。
REPORTS_DIR = Path(os.getenv("NETTUNE_REPORTS_DIR") or (BASE_DIR / "reports")).expanduser()
if not REPORTS_DIR.is_absolute():
    REPORTS_DIR = BASE_DIR / REPORTS_DIR

# 开关：是否启用 LLM 增强。
# 默认"配了密钥才开启"——没有密钥时每次提问都会先发一次无密钥请求，
# 网络不通就要白等 call_llm 的 30 秒超时才降级到规则意图，严重影响演示。
# 需要强制开启/关闭时用环境变量覆盖：NETTUNE_ENABLE_LLM=1 / 0。
_LLM_FLAG = os.getenv("NETTUNE_ENABLE_LLM", "").strip().lower()
if _LLM_FLAG in ("0", "false", "off", "no"):
    ENABLE_LLM = False
elif _LLM_FLAG in ("1", "true", "on", "yes"):
    ENABLE_LLM = True
else:
    ENABLE_LLM = bool(llm_agent.LLM_API_KEY)

# ======================
# 统一错误码定义（与api.md文档严格对齐）
# ======================
# 协议允许的场景枚举。必须在启动自检（data_status）之前定义 —— 自检会读它。
# 注意与 _DATA_SCENARIOS 区分：本常量是"协议允许"，后者是"数据集实际覆盖"。
VALID_SCENARIOS = ("校园", "商场", "体育馆")

ERROR_CODE = {
    "SUCCESS": 0,
    "PARAM_MISSING": 40001,       # 必填参数缺失
    "PARAM_INVALID": 40002,       # 参数格式非法
    "PARAM_OUT_OF_RANGE": 40003,  # 参数值超出合法范围
    "INTERNAL_ERROR": 50001,      # 服务内部异常
}

# ======================
# 统一响应构造工具
# ======================
def success_response(data=None):
    """成功响应，统一格式"""
    return {
        "code": ERROR_CODE["SUCCESS"],
        "message": "success",
        "data": data
    }

def error_response(code: int, message: str, data=None):
    """错误响应，统一格式"""
    return {
        "code": code,
        "message": message,
        "data": data
    }


app = FastAPI()

# ===== 新增：挂载报告下载静态目录 =====
# 自动创建reports目录，避免不存在报错（用绝对路径，与导出时写入的目录一致）
os.makedirs(REPORTS_DIR, exist_ok=True)
# 挂载静态目录，路径和返回的file_url前缀保持一致
# 挂载路径放在 /api 下：否则 vite dev 的代理（只代理 /api）转发不到，报告链接会 404。
app.mount("/api/download/reports", StaticFiles(directory=str(REPORTS_DIR)), name="download_reports")
# ==================================

# 全局参数校验异常处理：统一返回格式
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=400,
        content={
            "code": 40001,
            "message": "参数校验失败",
            "data": None
        }
    )

# 拦截接口不存在、请求方法错误等场景，统一返回JSON
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    if exc.status_code == 404:
        msg = "请求的接口不存在"
    elif exc.status_code == 405:
        msg = "请求方法不支持"
    else:
        msg = exc.detail

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": 40001,
            "message": msg,
            "data": None
        }
    )

# ========== 新增：全局服务端异常兜底 ==========
class UnknownScenarioError(Exception):
    """场景不在支持列表内。由 filter_rows / spectrum_window 抛出，全局映射为 40001。

    为什么用异常而不是每个 handler 各判一次：数据访问是唯一入口，
    在这里判一次就能覆盖全部 17 条路由，避免"某个接口忘了校验"。
    """


@app.exception_handler(UnknownScenarioError)
async def unknown_scenario_handler(request, exc: UnknownScenarioError):
    from fastapi.responses import JSONResponse as _JSON
    return _JSON(status_code=200, content={
        "code": 40001,
        "message": f"场景 {exc} 不受支持；当前支持：{'、'.join(VALID_SCENARIOS)}",
        "data": None,
    })


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={
            "code": 50001,
            "message": "服务内部错误",
            "data": None
        }
    )
# 允许前端(React dev server 5173)跨域调用本后端
# CORS 来源（审查报告 P0-5）：原来写死两个 IP，换机器/换端口就要改代码。
# 现在默认仍只放行这两个，但可用环境变量追加（逗号分隔）：
#   setx NETTUNE_ALLOW_ORIGINS "http://192.168.1.20:5173,http://myhost:5173"
# 保持"默认最小放行"，不改成 ["*"]——否则任何站点都能跨域读数据。
_DEFAULT_ORIGINS = ["http://localhost:5173", "http://10.63.118.174:5173"]
_EXTRA_ORIGINS = [o.strip() for o in os.getenv("NETTUNE_ALLOW_ORIGINS", "").split(",") if o.strip()]
ALLOWED_ORIGINS = _DEFAULT_ORIGINS + [o for o in _EXTRA_ORIGINS if o not in _DEFAULT_ORIGINS]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ 数据源：仿真数据集（路径解析） ============
# 规则（见 DEPLOY.md）：
#   1) 环境变量 NETWORK_DATA_PATH / SPECTRUM_DATA_PATH 优先（相对路径按项目根目录解析）；
#   2) 否则在 NETTUNE_DATA_DIR（默认 <项目根>/data）下按候选文件名探测；
#   3) 探测顺序兼容浏览器重复下载产生的 "(1)" 副本，避免路径失配后静默返回空数据。
# 候选名与解析函数统一定义在 data_layout.py（scripts/doctor.py 复用同一份）。
# 注意：网络表表头首列带 BOM（\ufefftimestamp），必须用 utf-8-sig 读取。

# 启动期收集到的问题（由 /api/health 与启动横幅统一输出，不静默吞掉）
STARTUP_ISSUES = []

DATA_PATH = resolve_data_file("NETWORK_DATA_PATH", NETWORK_CANDIDATES, "网络表", STARTUP_ISSUES)


def load_data():
    if DATA_PATH is None:
        print("[WARN] 网络表缺失：/api/data/*、研判、拓扑、回放相关接口将返回空数据")
        return []
    with open(DATA_PATH, encoding="utf-8-sig", newline="") as f:
        return list(_csv.DictReader(f))


DF = load_data()


def _build_df_index():
    """为网络表建"按场景的时间索引"，把 filter_rows 从线性扫描降到二分查找。

    为什么需要：原先 filter_rows 对 129,600 行做 Python 逐行循环，
    实测 51.5 ms/次；一次对话会调用 1~3 次，是响应时间的主要来源。
    这里在加载时一次性建 {scenario: (排序后的时间戳列表, 对应的 DF 下标列表)}，
    查询时 bisect 取区间，再按**原始下标升序**输出 —— 与线性扫描的行序完全一致。
    """
    idx = {}
    for i, r in enumerate(DF):
        try:
            idx.setdefault(r["scenario"], []).append((r["timestamp"], i))
        except (KeyError, TypeError):
            continue
    out = {}
    for scenario, pairs in idx.items():
        pairs.sort(key=lambda p: (p[0], p[1]))   # 时间相同则按下标，稳定可比
        out[scenario] = ([p[0] for p in pairs], [p[1] for p in pairs])
    return out


_DF_IDX = _build_df_index()
# 数据集里**真正有采样**的场景（当前只有"校园"）。
# VALID_SCENARIOS 是"协议允许的场景枚举"，与"数据是否覆盖"是两件事：
# 前者用于拒绝非法输入（40001），后者用于解释"合法但没数据"。
_DATA_SCENARIOS = tuple(sorted(_DF_IDX.keys()))

# ============ 频谱数据（预测预警 v2 真实数据源） ============
# 7天×1440分钟×3 AP×50频段 = 1,512,000 行；1分钟一帧，与网络数据同时间轴
# 字段：timestamp/observation_id(AP-01..03)/band_id(band-0..49)/lower_mhz/upper_mhz
#       /power_dbm/occupancy_pct/network_status(normal|weak_coverage|congestion)/scenario
import pandas as _pd
SPECTRUM_PATH = resolve_data_file("SPECTRUM_DATA_PATH", SPECTRUM_CANDIDATES, "频谱表", STARTUP_ISSUES)
_SPEC = {"df": None, "max_ts": None, "bands": None}


def load_spectrum():
    if SPECTRUM_PATH is None:
        print("[WARN] 频谱表缺失：/api/forecast/preview 将降级 insufficient_data")
        return
    df = _pd.read_csv(SPECTRUM_PATH, encoding="utf-8-sig")
    df["timestamp"] = _pd.to_datetime(df["timestamp"], format="%Y-%m-%d %H:%M:%S")
    # B-3：加载时按 (AP, 时间) 排序一次，之后按 AP 取子表是 O(1)，
    # 而不是每次请求都对 150 万行做 df[df.observation_id == ap] 全表扫描。
    df = df.sort_values(["observation_id", "timestamp"], kind="stable").reset_index(drop=True)
    _SPEC["df"] = df
    _SPEC["max_ts"] = df["timestamp"].max()
    _SPEC["min_ts"] = df["timestamp"].min()      # 支撑区间起点（网络表 ∩ 频谱表 = 频谱范围）
    # 每个 AP 一份子表 + 一个已排序的时间轴（int64 纳秒），供二分查找做区间切片
    by_ap, ts_index = {}, {}
    for _ap, _part in df.groupby("observation_id", sort=False):
        by_ap[_ap] = _part.reset_index(drop=True)
        ts_index[_ap] = _part["timestamp"].to_numpy(dtype="datetime64[ns]").astype("int64")
    _SPEC["by_ap"] = by_ap
    _SPEC["ts_index"] = ts_index
    bands = (df[["band_id", "lower_mhz", "upper_mhz"]].drop_duplicates()
             .sort_values("lower_mhz").to_dict("records"))
    _SPEC["bands"] = bands
    print(f"[OK] {SPECTRUM_PATH.name} 加载完成：{len(df)} 行，时间至 {_SPEC['max_ts']}, {len(bands)} 频段")


def spectrum_window(ap: str, start_dt: datetime, end_dt: datetime, inclusive_end: bool = False):
    """按 AP + 时间区间取频谱子表（B-3 索引）。

    实现：每个 AP 的时间轴在加载时已排序，用二分查找定位区间端点，
    再做 iloc 位置切片 —— O(log n) 定位 + O(k) 切片，不再做全表布尔筛选。
    返回的 DataFrame 已 reset_index，调用方可直接按位索引。
    """
    part = (_SPEC.get("by_ap") or {}).get(ap)
    axis = (_SPEC.get("ts_index") or {}).get(ap)
    if part is None or axis is None or len(axis) == 0:
        return None
    lo = int(axis.searchsorted(_pd.Timestamp(start_dt).value, side="left"))
    hi = int(axis.searchsorted(_pd.Timestamp(end_dt).value,
                               side="right" if inclusive_end else "left"))
    if hi <= lo:
        return None
    return part.iloc[lo:hi]


load_spectrum()


# ============ 启动自检（部署排查用，对应接口13 /api/health） ============
def data_status():
    """汇总数据/依赖/大模型的就绪状态，供 /api/health 与启动横幅使用。

    设计原则：缺数据不抛异常、不静默，而是把"缺什么、该看哪份文档"直接返回给调用方，
    避免出现"服务起来了但所有接口返回空数组"这种最难排查的状态。
    """
    net = {
        "path": str(DATA_PATH) if DATA_PATH else None,
        "exists": bool(DATA_PATH and DATA_PATH.is_file()),
        "rows": len(DF),
        "size_mb": round(DATA_PATH.stat().st_size / 1024 / 1024, 1) if DATA_PATH else None,
    }
    spec_df = _SPEC["df"]
    spec = {
        "path": str(SPECTRUM_PATH) if SPECTRUM_PATH else None,
        "exists": bool(SPECTRUM_PATH and SPECTRUM_PATH.is_file()),
        "rows": int(len(spec_df)) if spec_df is not None else 0,
        "size_mb": round(SPECTRUM_PATH.stat().st_size / 1024 / 1024, 1) if SPECTRUM_PATH else None,
        "max_timestamp": _SPEC["max_ts"].strftime("%Y-%m-%d %H:%M:%S") if _SPEC["max_ts"] is not None else None,
        "band_count": len(_SPEC["bands"] or []),
    }
    return {
        "ok": bool(net["rows"]) and bool(spec["rows"]),
        "data_dir": str(DATA_DIR),
        "network": net,
        "spectrum": spec,
        "capabilities": {
            "prophet": bool(getattr(_fm, "_HAS_PROPHET", False)),
            "kriging": bool(getattr(_sm, "_HAS_PYKRIGE", False)),
            "llm_enabled": bool(ENABLE_LLM),
            "llm_key_configured": bool(llm_agent.LLM_API_KEY),
            "llm_model": llm_agent.LLM_MODEL,
            # 协议允许的场景 vs 数据集实际覆盖的场景（当前只有校园）
            "scenarios_supported": list(VALID_SCENARIOS),
            "scenarios_with_data": list(_DATA_SCENARIOS),
        },
        "issues": list(STARTUP_ISSUES),
        "hint": "数据文件缺失时请按 DEPLOY.md 第 2 节准备 data/ 目录；"
                "也可用 NETWORK_DATA_PATH / SPECTRUM_DATA_PATH 直接指定文件。",
    }


def _print_startup_report():
    st = data_status()
    print("=" * 72)
    print("[启动自检] AI-NetTune backend")
    print(f"  数据目录 : {st['data_dir']}")
    print(f"  网络表   : {(st['network']['path'] or '缺失')}"
          f" ({st['network']['rows']} 行)")
    print(f"  频谱表   : {(st['spectrum']['path'] or '缺失')}"
          f" ({st['spectrum']['rows']} 行, 至 {st['spectrum']['max_timestamp']})")
    cap = st["capabilities"]
    print(f"  能力     : prophet={'on' if cap['prophet'] else 'off(降级 moving_average)'} "
          f"kriging={'on' if cap['kriging'] else 'off(降级 grid_mean)'} "
          f"llm={'on' if cap['llm_key_configured'] else 'off(降级规则意图)'}")
    for issue in st["issues"]:
        print(f"  [问题] {issue}")
    if not st["ok"]:
        print("  [提醒] 数据不完整，请按 DEPLOY.md 准备 data/ 目录后重启；GET /api/health 可复查。")
    print("=" * 72)


_print_startup_report()

# 后台预热 Prophet：把首次拟合的开销挪到启动阶段；失败则本进程自动降级为移动平均。
# 预热期间到达的接口10 请求直接用移动平均，不会和预热线程抢着等超时。
_fm.warm_up()


# ============ 接口13：部署自检（不依赖任何数据文件，永远可访问） ============
@app.get("/api/health", summary="接口13：部署自检（数据/依赖/大模型就绪状态）")
def health():
    """给部署者和评委用的自检接口：返回数据文件是否到位、行数、时间范围，
    以及 prophet / kriging / LLM 三项能力是否可用（不可用时后端会自动降级）。

    注意：本接口始终返回 HTTP 200 + code 0，就绪与否看 data.ok 字段，
    这样前端/脚本可以直接读字段判断，不需要区分 HTTP 状态码。
    """
    return success_response(data_status())


# 完整 18 列：11 核心 KPI + AP/UE 坐标（4列）+ 故障深度分析辅助字段（3列）
# 列名统一定义在 data_layout.NETWORK_COLUMNS（scripts/doctor.py 用它校验 CSV 表头）
ALL_COLUMNS = list(NETWORK_COLUMNS)

def filter_rows(scenario, start_time, end_time, ap_id=None, user_id=None):
    """按场景/时间/AP/用户过滤 CSV 数据，按时间升序返回。

    用加载时建好的时间索引做二分定位（见 _build_df_index），不再逐行扫描 12.96 万行。
    输出与旧实现逐条一致：时间升序，同一时间戳保持原始行序（稳定）。

    未知场景（如"火星"）抛 UnknownScenarioError，由全局处理器映射为 40001 —— 否则
    调用方拿到空数组会误以为"该窗口没数据"，前端渲染空白页面却看不出是场景写错了。
    """
    if scenario not in VALID_SCENARIOS:
        raise UnknownScenarioError(scenario)
    idx = _DF_IDX.get(scenario)
    if idx is None:
        return []
    ts_list, pos_list = idx
    lo = _bisect.bisect_left(ts_list, start_time)
    hi = _bisect.bisect_right(ts_list, end_time)
    if lo >= hi:
        return []
    rows = []
    for pos in pos_list[lo:hi]:
        r = DF[pos]
        if ap_id and r["ap_id"] != ap_id:
            continue
        if user_id and r["user_id"] != user_id:
            continue
        rows.append(r)
    return rows

def avg(rows, key):
    try:
        return statistics.mean(float(r[key]) for r in rows)
    except Exception:
        return 0.0


# ============ 接口1：查询监测数据 ============
@app.get("/api/data/query")
def data_query(
        scenario: str = Query(..., description="场景，如：校园"),
        start_time: str = Query("2026-01-01 00:00:00", description="开始时间"),
        end_time: str = Query("2026-01-03 23:59:59", description="结束时间"),
        ap_id: str | None = Query(None, description="AP编号，不传查全部"),
        user_id: str | None = Query(None, description="用户编号，不传查全部"),
        metrics: str | None = Query(None, description="要返回的KPI列，逗号分隔，如 rsrp,sinr；不传返回全部"),
        offset: int = Query(0, ge=0, description="分页偏移量，从第几条开始"),
        limit: int = Query(100, ge=1, le=1000, description="最多返回条数"),
):
    # 1. 先按条件全量过滤，得到所有符合条件的数据
    all_rows = filter_rows(scenario, start_time, end_time, ap_id, user_id)
    # 2. 总记录数 = 全量过滤后的数量，不受分页参数影响
    total = len(all_rows)
    # 3. 按分页参数切片，得到当前页数据
    page_rows = all_rows[offset: offset + limit]

    # 字段裁剪逻辑不变
    columns = ALL_COLUMNS
    if metrics:
        wanted = [m.strip() for m in metrics.split(",")]
        columns = ["timestamp"] + wanted
    records = [{k: r[k] for k in columns} for r in page_rows]

    # 合法场景但数据集里没有它的采样时，必须说清楚"这个场景没有数据"，
    # 否则前端只能看到 total=0，与"时间窗口选错了"无法区分（实测 商场/体育馆 均无数据）。
    notice = ""
    if total == 0 and scenario not in _DATA_SCENARIOS:
        notice = (f"场景「{scenario}」当前没有采样数据；数据集实际覆盖："
                  f"{'、'.join(_DATA_SCENARIOS)}")
    return {
        "code": 0,
        "message": notice or "success",
        "data": {
            "total": total,  # 修复：返回符合条件的总记录数
            "columns": columns,
            "records": records,  # 当前页数据
            "scenarios_with_data": list(_DATA_SCENARIOS),
        },
    }


# ============ 接口2：多KPI联合故障研判 ============
class AnomalyRequest(BaseModel):
    scenario: str
    window_start: str
    window_end: str
    metrics: list[str] = ["rsrp", "sinr", "throughput"]
    # 可选：只研判指定 AP。默认 None = 现有行为（整窗、按 AP 逐一研判），
    # 因此前端/报告导出等既有调用完全不受影响。
    ap_id: str | None = None

def build_candidates(ap, base, anomaly_type):
    """为单个AP的故障生成多候选原因与证据链（支持/反对/待验证），rank1 即主原因"""
    rsrp = base["rsrp_avg"]; sinr = base["sinr_avg"]; prb = base["prb_avg"]
    weak_r = base["weak_ratio"]; cong_r = base["cong_ratio"]
    c1 = min(0.99, 0.6 + 0.08 * len(base["evidence"]))

    if anomaly_type == "覆盖故障":
        return [
            {"rank": 1, "cause": f"{ap}天线倾角/发射功率不足，造成覆盖空洞",
             "confidence": round(c1, 2),
             "support_evidence": [f"弱覆盖采样占比{weak_r*100:.0f}%超过30%阈值",
                                  (f"RSRP均值{rsrp:.1f}dBm低于-105dBm" if rsrp < -105
                                   else f"RSRP均值{rsrp:.1f}dBm处于偏低水平")],
             "against_evidence": [f"SINR均值{sinr:.1f}dB正常，可排除强干扰因素" if sinr >= 5
                                  else f"SINR均值{sinr:.1f}dB也偏低，干扰因素暂不能排除"],
             "pending_checks": ["现场核查天线倾角与发射功率配置"]},
            {"rank": 2, "cause": f"{ap}周边新增遮挡物/环境变化导致信号衰减",
             "confidence": round(c1 * 0.7, 2),
             "support_evidence": ["故障呈时段性出现，与遮挡场景变化特征相符"],
             "against_evidence": [f"拥塞采样占比{cong_r*100:.0f}%较低，非负荷因素" if cong_r <= 0.25
                                  else f"拥塞采样占比{cong_r*100:.0f}%偏高，负荷因素需考虑"],
             "pending_checks": ["现场勘查周边遮挡物或建筑变更"]},
            {"rank": 3, "cause": f"{ap}邻区干扰或负荷牵引导致覆盖感知差",
             "confidence": round(c1 * 0.5, 2),
             "support_evidence": [f"PRB利用率{prb:.1f}%处于中高水平" if prb >= 50
                                  else f"PRB利用率{prb:.1f}%正常，干扰证据有限"],
             "against_evidence": [f"RSRP均值{rsrp:.1f}dBm与SINR{sinr:.1f}dB组合更指向覆盖问题"],
             "pending_checks": ["核查邻区PRB占用与切换参数"]},
        ]
    if anomaly_type == "网络拥塞":
        return [
            {"rank": 1, "cause": f"{ap}容量不足/接入用户过多导致拥塞",
             "confidence": round(c1, 2),
             "support_evidence": [f"拥塞采样占比{cong_r*100:.0f}%超过25%阈值",
                                  (f"PRB利用率均值{prb:.1f}%高于90%" if prb > 90
                                   else f"PRB利用率均值{prb:.1f}%处于偏高水平")],
             "against_evidence": [f"RSRP均值{rsrp:.1f}dBm正常，无线环境良好" if rsrp >= -105
                                  else f"RSRP均值{rsrp:.1f}dBm偏低，需叠加覆盖因素"],
             "pending_checks": ["核查活跃用户数与容量规划"]},
            {"rank": 2, "cause": f"{ap}业务突发/流量高峰导致瞬时拥塞",
             "confidence": round(c1 * 0.7, 2),
             "support_evidence": ["拥塞集中在特定时段，符合业务高峰特征"],
             "against_evidence": [f"弱覆盖采样占比{weak_r*100:.0f}%未超阈值，非覆盖因素" if weak_r <= 0.3
                                  else f"弱覆盖采样占比{weak_r*100:.0f}%偏高，覆盖因素需考虑"],
             "pending_checks": ["查看分时段流量分布与业务类型"]},
            {"rank": 3, "cause": f"{ap}回程/传输链路瓶颈",
             "confidence": round(c1 * 0.5, 2),
             "support_evidence": [f"吞吐量均值{base['thr_avg']:.1f}Mbps与PRB高占用不匹配" if base['thr_avg'] < 100
                                  else "吞吐量表现正常，回程瓶颈证据有限"],
             "against_evidence": [],
             "pending_checks": ["核查回程链路带宽与拥塞窗口"]},
        ]
    if anomaly_type == "边缘弱用户":
        return [
            {"rank": 1, "cause": f"{ap}用户位于覆盖边缘，信号质量差",
             "confidence": round(c1, 2),
             "support_evidence": [f"吞吐量均值{base['thr_avg']:.1f}Mbps低于50Mbps",
                                  f"SINR均值{sinr:.1f}dB偏低"],
             "against_evidence": [],
             "pending_checks": ["查看用户位置分布与终端能力"]},
            {"rank": 2, "cause": f"{ap}波束/参数配置未覆盖边缘区域",
             "confidence": round(c1 * 0.6, 2),
             "support_evidence": [f"RSRP均值{rsrp:.1f}dBm处于临界水平"],
             "against_evidence": [f"弱覆盖采样占比{weak_r*100:.0f}%未超30%阈值"],
             "pending_checks": ["核查波束配置与天线倾角"]},
        ]
    return [{"rank": 1, "cause": f"{ap}各项指标正常，无需处理", "confidence": round(0.95, 2),
             "support_evidence": [], "against_evidence": [], "pending_checks": []}]

# 小时切片单独判异常所需的最小样本行数。
# 窗口被截断时最后那个小时可能只有十几行，均值抖动大 → 不单独下"异常"结论。
MIN_HOUR_ROWS = 20
# 单个 (小时, AP) 切片单独下"异常"结论所需的最小行数。
# 为什么需要它：MIN_HOUR_ROWS 盯的是整个小时的总行数（跨 AP 合计）。
# 实测构造"某 AP 只有 1 条采样、其他 AP 凑够总行数"时，那条孤零零的采样
# 仍会被判成"覆盖故障/中等"——样本不足时均值没有统计意义。
MIN_HOUR_ROWS_PER_AP = 5


# 场景坐标范围缓存：同一场景的所有空间帧共用这个范围（见 spatial_frames.build_grid 的说明）。
# 只按场景算一次并缓存，避免每个请求都全表扫描。
_SCENE_EXTENTS: dict[str, tuple[float, float, float, float]] = {}


def _scene_extent(scenario: str) -> tuple[float, float, float, float] | None:
    """该场景的固定坐标范围 (x0, x1, y0, y1)，取自场景内全部采样的包络 + 5% 边距。"""
    if scenario in _SCENE_EXTENTS:
        return _SCENE_EXTENTS[scenario]
    rows = filter_rows(scenario, "2026-01-01 00:00:00", "2026-12-31 23:59:59")
    xs = [float(r["ue_x"]) for r in rows if r.get("ue_x") not in (None, "")]
    ys = [float(r["ue_y"]) for r in rows if r.get("ue_y") not in (None, "")]
    if not xs or not ys:
        return None
    pad_x = max(1.0, (max(xs) - min(xs)) * 0.05)
    pad_y = max(1.0, (max(ys) - min(ys)) * 0.05)
    extent = (min(xs) - pad_x, max(xs) + pad_x, min(ys) - pad_y, max(ys) + pad_y)
    _SCENE_EXTENTS[scenario] = extent
    return extent


def _spec_verdict(ap: str, start: str, end: str) -> dict:
    """频谱维度判定，供「现状研判」使用（与「预测预警」共用同一套门槛）。

    为什么要它（docs/fault_definition.md 第 2.2 节）：研判原先只看网络表
    （RSRP/SINR/吞吐均值 + 标签占比），而预警只看频谱（越阈频段数）。
    两套口径在故障边界窗口上会给出相反结论——实测 56 个边界窗口有 4 个相反：
    频谱已 40/50 越阈但标签还没到（研判漏报），或反过来（研判多报）。
    这里把频谱维度作为研判的**第二个主判据**，任一维满足即判故障。

    返回 {available, weak, cong, max_power_bands, max_occ_bands, min_bands}
    """
    out = {"available": False, "weak": False, "cong": False,
           "max_power_bands": 0, "max_occ_bands": 0, "min_bands": 0}
    if _SPEC.get("df") is None:
        return out
    # 必须 inclusive_end=True：研判窗口与网络表 filter_rows 都是**闭区间**，
    # 而 spectrum_window 默认不含结束那一分钟。实测 EV-006（01-05 20:00~20:20）
    # 只有 20:18/19/20 三分钟达到门槛，被切掉一分钟就退化成 2 分钟 → 漏判。
    part = spectrum_window(ap, start, end, inclusive_end=True)
    if part is None or part.empty:
        return out

    min_bands = _min_crossing_bands(len(_SPEC.get("bands") or []))
    out["available"], out["min_bands"] = True, min_bands

    # 向量化：先算出每行的越阈标记，再按分钟求和（避免逐分钟 Python 循环）
    counted = part.assign(
        _p=(part["power_dbm"] < ALERT_WEAK_POWER_DBM).astype(int),
        _o=(part["occupancy_pct"] > ALERT_CONGESTION_OCC_PCT).astype(int),
    ).groupby("timestamp")[["_p", "_o"]].sum()

    out["max_power_bands"] = int(counted["_p"].max())
    out["max_occ_bands"] = int(counted["_o"].max())
    for col, key in (("_p", "weak"), ("_o", "cong")):
        run = 0
        for hit in (counted[col] >= min_bands):
            run = run + 1 if hit else 0
            if run >= ALERT_MIN_CROSSING_MINUTES:
                out[key] = True
                break
    return out


def judge_ap(ap, ap_rows, spec=None):
    """对单个AP做多KPI联合研判（网络维 + 频谱维 + 状态标签占比）。

    spec：可选的频谱维度判定结果（_spec_verdict 的返回值）。
    传入时，"覆盖故障/网络拥塞"的主判据变为"网络维 或 频谱维 任一满足"，
    两维都满足则升级为"严重" —— 见 docs/fault_definition.md 第 2.2 / 2.3 节。
    """
    rsrp_avg = avg(ap_rows, "rsrp")
    sinr_avg = avg(ap_rows, "sinr")
    thr_avg = avg(ap_rows, "throughput")
    prb_avg = avg(ap_rows, "prb_usage")
    pkt_avg = avg(ap_rows, "packet_loss")

    # 该AP窗口内的状态标签占比（唐苗数据已标注）
    st = {}
    for r in ap_rows:
        st[r["network_status"]] = st.get(r["network_status"], 0) + 1
    weak_ratio = st.get("weak_coverage", 0) / len(ap_rows)
    cong_ratio = st.get("congestion", 0) / len(ap_rows)

    # 逐KPI对照通信阈值，构建证据链
    evidence = []
    if rsrp_avg < -105:
        evidence.append({"metric": "rsrp", "avg_value": round(rsrp_avg, 2), "threshold": -105, "status": "异常"})
    if sinr_avg < 5:
        evidence.append({"metric": "sinr", "avg_value": round(sinr_avg, 2), "threshold": 5, "status": "异常"})
    if prb_avg > 90:
        evidence.append({"metric": "prb_usage", "avg_value": round(prb_avg, 2), "threshold": 90, "status": "异常"})
    if pkt_avg > 5:
        evidence.append({"metric": "packet_loss", "avg_value": round(pkt_avg, 2), "threshold": 5, "status": "异常"})

    base = {
        "ap_id": ap,
        "rsrp_avg": round(rsrp_avg, 1), "sinr_avg": round(sinr_avg, 1),
        "thr_avg": round(thr_avg, 1), "prb_avg": round(prb_avg, 1),
        "weak_ratio": round(weak_ratio, 3), "cong_ratio": round(cong_ratio, 3),
        "evidence": evidence,
    }

    # 规则优先级：覆盖故障 > 网络拥塞 > 边缘弱用户
    # 统一口径（docs/fault_definition.md 2.2/2.3）：覆盖故障与网络拥塞各有两个主判据
    # （网络维 + 频谱维），**任一维满足即判故障**；两维都满足才是"严重"。
    # 这样既堵住"频谱已越阈但标签还没到"的漏报，又给出统一分档。
    _spec = spec or {}
    net_weak = weak_ratio > 0.3 or (rsrp_avg < -105 and sinr_avg < 5)
    spec_weak = bool(_spec.get("weak"))
    if net_weak or spec_weak:
        both = net_weak and spec_weak
        merged = list(evidence)
        if weak_ratio > 0.3:
            merged.append({"metric": "weak_coverage_ratio", "avg_value": round(weak_ratio, 3),
                           "threshold": 0.3, "status": "异常"})
        if spec_weak:
            merged.append({"metric": "spectrum_power_bands",
                           "avg_value": _spec.get("max_power_bands", 0),
                           "threshold": _spec.get("min_bands", 0), "status": "异常"})
        src = "两维均越阈" if both else ("频谱维度越阈" if spec_weak else "网络维度越阈")
        return {**base, "evidence": merged,
                "anomaly_type": "覆盖故障", "level": "严重" if both else "中等",
                "network_status": "weak_coverage",
                "cause": (f"{ap}弱覆盖采样占比{weak_ratio*100:.0f}%、RSRP均值{rsrp_avg:.1f}dBm"
                          f"、频段最大越阈{_spec.get('max_power_bands', 0)}/{_spec.get('min_bands', 0)}，"
                          f"{src}，判定为覆盖故障"),
                "suggestion": "建议检查AP天线倾角/功率或增加补盲AP",
                "candidates": build_candidates(ap, base, "覆盖故障")}
    net_cong = cong_ratio > 0.25 or prb_avg > 90
    spec_cong = bool(_spec.get("cong"))
    if net_cong or spec_cong:
        both = net_cong and spec_cong
        merged = list(evidence)
        if cong_ratio > 0.25:
            merged.append({"metric": "congestion_ratio", "avg_value": round(cong_ratio, 3),
                           "threshold": 0.25, "status": "异常"})
        if spec_cong:
            merged.append({"metric": "spectrum_occupancy_bands",
                           "avg_value": _spec.get("max_occ_bands", 0),
                           "threshold": _spec.get("min_bands", 0), "status": "异常"})
        src = "两维均越阈" if both else ("频谱维度越阈" if spec_cong else "网络维度越阈")
        return {**base, "evidence": merged,
                "anomaly_type": "网络拥塞", "level": "严重" if both else "中等",
                "network_status": "congestion",
                "cause": (f"{ap}拥塞采样占比{cong_ratio*100:.0f}%、PRB均值{prb_avg:.1f}%"
                          f"、频段最大越阈{_spec.get('max_occ_bands', 0)}/{_spec.get('min_bands', 0)}，"
                          f"{src}，判定为容量拥塞"),
                "suggestion": "建议负载均衡或扩容",
                "candidates": build_candidates(ap, base, "网络拥塞")}
    if thr_avg < 50 and rsrp_avg < -100:
        # 「边缘弱用户」= 用户处在覆盖边缘 → **必须有 RSRP 佐证**。
        # 原先只看 thr_avg < 50 就判中等异常，导致正常时段被误报：
        # 实测 2026-01-02 11:01~11:16（16 行、标签全 normal、RSRP -93.4dBm、
        # SINR 6.1dB、吞吐 49.1Mbps）被判"边缘弱用户/中等"，而吞吐只差 0.9。
        # 吞吐偏低既可能是边缘弱覆盖，也可能是拥塞或服务端问题，单凭它不能定性。
        # 统一口径（docs/fault_definition.md 2.3）：这个条件**不属于**弱覆盖的两个
        # 主判据（① 频谱 40/50 越阈 ② RSRP<−105 采样占比 ≥30%），因此只能算
        # "指标偏低但未达主判据" → 归入**提示级**，不再算故障。
        # 语义仍是"边缘弱用户"（用户处在覆盖边缘），只是不升级为故障结论。
        weak_evidence = evidence + [
            {"metric": "rsrp", "avg_value": round(rsrp_avg, 2), "threshold": -100, "status": "提示"},
            {"metric": "throughput", "avg_value": round(thr_avg, 2), "threshold": 50, "status": "提示"},
        ]
        return {**base, "evidence": weak_evidence,
                "anomaly_type": "性能提示", "level": "提示", "network_status": "normal",
                "cause": (f"{ap}RSRP均值{rsrp_avg:.1f}dBm偏低（< -100）且吞吐均值{thr_avg:.1f}Mbps低于50，"
                          f"呈边缘弱用户特征；但未达弱覆盖主判据，属性能提示而非故障"),
                "suggestion": "建议用户靠近信号源或调整波束；若 RSRP 继续下降或频谱越阈，将升级为覆盖故障",
                "candidates": build_candidates(ap, base, "边缘弱用户")}
    # 「性能提示」：整窗标签 normal，但**过半采样**的信号质量已偏低 —— 单独一类，不算异常。
    # 起因：队友指出"CSV 标签正常不代表指标都优良"。
    #
    # 判据为什么用"过半采样 SINR<5"而不是均值区间（两次实测否决）：
    #   · 吞吐 < 50Mbps：正常样本中 53% 都满足（那是中位数）→ 一半窗口会亮提示，等于噪声
    #   · 均值接近阈值（RSRP -105~-95 / PRB 80~90）：全 7 天 1972 个干净窗口里命中 **0 个**
    #     （数据是双峰分布：正常 RSRP≈-92，故障≈-111，中间几乎没有样本）
    #   · 采样级 SINR<5 占比 >50%：命中 14.8% 的干净窗口，稀少但可达，语义是
    #     "过半数采样质量偏低却没被标成故障" —— 这正是"提示而非故障"的定位
    _sinr_low_share = sum(1 for r in ap_rows if float(r["sinr"]) < 5) / len(ap_rows)
    if _sinr_low_share > 0.5:
        return {**base, "evidence": evidence + [
                    {"metric": "sinr_low_share", "avg_value": round(_sinr_low_share, 3),
                     "threshold": 0.5, "status": "提示"}],
                "anomaly_type": "性能提示", "level": "提示", "network_status": "normal",
                "cause": (f"{ap}有 {_sinr_low_share*100:.0f}% 的采样 SINR 低于 5dB，"
                          f"但整窗标签为 normal（均值 RSRP {rsrp_avg:.1f}dBm、SINR {sinr_avg:.1f}dB），"
                          f"属性能偏低而非确诊故障"),
                "suggestion": "持续观察；若弱覆盖采样占比升高或 SINR 继续下降，再升级为覆盖故障",
                "candidates": []}

    return {**base, "anomaly_type": "正常", "level": "低", "network_status": "normal",
            "cause": f"{ap}各项指标均在正常范围", "suggestion": "无需处理",
            "candidates": build_candidates(ap, base, "正常")}

def _anomaly_spans(rows: list[dict], limit: int = 20) -> list[dict]:
    """把分钟级异常行合并成连续区间，回答"异常从几点到几点"。

    为什么需要（审查报告「缺口5 分钟级起止」）：申报书 L17 写的是
    「实时分析定位异常的空间分布与**起止时间**」，但原来只给了小时粒度的
    alarm_history（time 精确到 `2026-01-01 10:00`），说不出"09:47–11:12"。
    数据本身是分钟级的，这里把同一 AP、同一类型、相邻分钟（间隔 ≤ 1 分钟）
    的异常行合并成一段，给出精确到分钟的起止与持续分钟数。
    """
    grouped: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        status = row.get("network_status")
        if status and status != "normal":
            grouped.setdefault((row["ap_id"], status), set()).add(row["timestamp"])

    spans: list[dict] = []
    for (ap_id, status), timestamps in grouped.items():
        # 同一分钟可能有多个 UE 各一行，先按"分钟"去重；再把连续分钟合并。
        # 容忍 2 分钟的空洞（某分钟恰好没有该 AP 的采样），否则区间会被切碎。
        ordered = sorted(timestamps, key=lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M:%S"))
        current: dict | None = None
        last_dt: datetime | None = None
        for ts_str in ordered:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            if current is not None and last_dt is not None and (ts - last_dt).total_seconds() <= 120:
                current["end"] = ts_str
                current["minutes"] += int(round((ts - last_dt).total_seconds() // 60)) or 1
                last_dt = ts
            else:
                if current is not None:
                    spans.append(current)
                current = {"ap_id": ap_id, "type": status,
                           "start": ts_str, "end": ts_str, "minutes": 1}
                last_dt = ts
        if current is not None:
            spans.append(current)

    spans.sort(key=lambda s: (s["start"], s["ap_id"]))
    return spans[:limit]


def _supported_range() -> tuple[str, str] | None:
    """系统实际支撑的数据区间（= 网络表 ∩ 频谱表）。

    为什么需要它：网络表有 30 天、频谱表只有前 7 天，而前端回放区间本来就是
    `2026-01-01 00:00 ~ 2026-01-07 23:59`。此前后端研判对任意 30 天窗口都答，
    于是 01-08 之后的窗口会出现"研判报故障、预测说无数据"的矛盾
    （实测 01-20 10:00：研判=覆盖故障/当前故障中，预测=insufficient_data）。
    统一交集后，"支持范围"在两侧一致。
    """
    if _SPEC.get("max_ts") is None or _SPEC.get("min_ts") is None:
        return None
    # 频谱表 ⊆ 网络表，所以交集就是频谱表的范围
    return (_SPEC["min_ts"].strftime("%Y-%m-%d %H:%M:%S"),
            _SPEC["max_ts"].strftime("%Y-%m-%d %H:%M:%S"))


# 研判结果短缓存（60 秒）：同一窗口被反复请求时（对话 + 报告 + 前端轮询）直接复用。
# 安全性设计（这是"零风险"的关键）：
#   ① 入缓存与出缓存都 deepcopy —— 调用方（agent_chat 会 setdefault("multi_turn")）就地改字典，
#      不深拷贝会把缓存污染成"带上一轮会话字段"的结果。
#   ② key 里带数据文件 mtime，替换数据后缓存自动失效，不会返回旧数据。
#   ③ 可用 NETTUNE_DISABLE_ANOMALY_CACHE=1 彻底关闭。
_ANOMALY_CACHE: dict = {}
_ANOMALY_TTL_SEC = 60.0
_ANOMALY_CACHE_MAX = 128
_ANOMALY_CACHE_ON = os.getenv("NETTUNE_DISABLE_ANOMALY_CACHE", "").strip() not in ("1", "true", "on")

# 热力图（Kriging）结果缓存：同参数重复请求直接复用，数值零变化。
_HEATMAP_CACHE: dict = {}
_HEATMAP_TTL_SEC = 120.0
_HEATMAP_CACHE_MAX = 64


def _data_version() -> tuple:
    """数据文件版本（mtime），用于让缓存随数据替换自动失效。"""
    def _mtime(p):
        try:
            return p.stat().st_mtime if p else 0.0
        except OSError:
            return 0.0
    return (_mtime(DATA_PATH), _mtime(SPECTRUM_PATH))


@app.post("/api/analyze/anomaly")
def analyze_anomaly(req: AnomalyRequest):
    if not _ANOMALY_CACHE_ON:
        return _analyze_anomaly_uncached(req)
    key = (req.scenario, req.window_start, req.window_end,
           getattr(req, "ap_id", None) or "", _data_version())
    hit = _ANOMALY_CACHE.get(key)
    if hit is not None:
        ts, payload = hit
        if time.monotonic() - ts < _ANOMALY_TTL_SEC:
            return _copy.deepcopy(payload)
        _ANOMALY_CACHE.pop(key, None)
    payload = _analyze_anomaly_uncached(req)
    if isinstance(payload, dict) and payload.get("code") == 0:
        if len(_ANOMALY_CACHE) >= _ANOMALY_CACHE_MAX:
            _ANOMALY_CACHE.pop(next(iter(_ANOMALY_CACHE)), None)   # FIFO 淘汰
        _ANOMALY_CACHE[key] = (time.monotonic(), _copy.deepcopy(payload))
    return payload


def _analyze_anomaly_uncached(req: AnomalyRequest):
    # 第2周深化版：CSV真实数据，按AP分组多KPI联合研判（可解释证据链）
    # 新增：hourly_trend 小时趋势 / ap_comparison 多AP对比 / alarm_history 告警历史
    # 窗口先收敛到"支撑区间"：越界部分裁掉并明确告知，完全无交集则如实说无数据，
    # 不再对没有频谱的数据段给出故障结论（否则与预测预警自相矛盾）。
    _range = _supported_range()
    window_start, window_end = req.window_start, req.window_end
    range_notice = ""
    if _range is not None:
        sup_start, sup_end = _range
        if window_end < sup_start or window_start > sup_end:
            return {"code": 0, "message": "success",
                    "data": {"is_anomaly": False, "reason": "请求时段超出数据范围",
                             "cause": f"请求时段 {window_start} ~ {window_end} 超出系统支撑范围 {sup_start} ~ {sup_end}，无可研判数据",
                             "anomaly_type": "正常", "level": "低", "current_state": "normal",
                             "affected_aps": [], "hourly_trend": [], "ap_comparison": [],
                             "alarm_history": [], "data_range": {"start": sup_start, "end": sup_end},
                             "range_notice": f"本次请求超出支撑范围，未做研判；支持范围 {sup_start} ~ {sup_end}"}}
        if window_start < sup_start:
            window_start = sup_start
            range_notice = f"起始时间早于支撑范围，已从 {sup_start} 开始研判"
        if window_end > sup_end:
            window_end = sup_end
            range_notice = (range_notice + "；" if range_notice else "") + \
                f"结束时间晚于支撑范围，已裁剪到 {sup_end}"

    rows = filter_rows(req.scenario, window_start, window_end)
    # 指定 AP 时，只对该 AP 做研判；但"多 AP 横向对比"仍用全部 AP，
    # 否则报告里的 AP 对比表会只剩一行。
    rows_all = rows
    ap_filter = getattr(req, "ap_id", None) or ""
    if ap_filter:
        rows = [r for r in rows if r["ap_id"] == ap_filter]
    if not rows:
        if ap_filter:
            return {"code": 0, "message": "success",
                    "data": {"is_anomaly": False, "reason": f"{ap_filter} 在所选时段无采样",
                             "cause": f"{ap_filter} 在该时间窗口没有采样数据",
                             "anomaly_type": "正常", "level": "低", "current_state": "normal",
                             "affected_aps": [], "ap_filter": ap_filter,
                             "hourly_trend": [], "ap_comparison": [], "alarm_history": []}}
        return {"code": 0, "message": "success",
                "data": {"is_anomaly": False, "reason": "该时间窗口无数据",
                         "cause": "该时间窗口无数据",
                         "hourly_trend": [], "ap_comparison": [], "alarm_history": []}}

    sev_rank = {"严重": 0, "中等": 1, "低": 9}

    # 1) 按 AP 分组研判 → 主结果
    by_ap = {}
    for r in rows:
        by_ap.setdefault(r["ap_id"], []).append(r)
    # 频谱维度判定（与预测预警共用同一套门槛）；频谱表缺失时返回 available=False
    _spec_by_ap = {}
    if _SPEC.get("df") is not None:
        for _ap in by_ap:
            _spec_by_ap[_ap] = _spec_verdict(_ap, req.window_start, req.window_end)
    ap_results = [judge_ap(ap, ap_rows, _spec_by_ap.get(ap))
                  for ap, ap_rows in sorted(by_ap.items())]
    # 故障类 findings（不含"性能提示"）：性能提示只作为 hint 展示，不计入异常
    findings = [res for res in ap_results if res["anomaly_type"] not in ("正常", "性能提示")]
    perf_hints = [res for res in ap_results if res["anomaly_type"] == "性能提示"]

    # 2) 按小时切片 → 趋势 + 时段级异常（回答"什么时候开始变差的"）
    by_hour = {}
    for r in rows:
        by_hour.setdefault(r["timestamp"][:13], []).append(r)  # "2026-01-01 10"
    hourly_trend = []
    hour_findings_all = []
    for hour in sorted(by_hour):
        hour_by_ap = {}
        for r in by_hour[hour]:
            hour_by_ap.setdefault(r["ap_id"], []).append(r)
        hour_res = [judge_ap(ap, ap_rows) for ap, ap_rows in hour_by_ap.items()]
        # 小时切片样本太少时**不单独判异常**（只参与趋势与 AP 对比）。
        # 起因：18:xx 这类被窗口截断的小时只切到十几行，均值抖动大，
        # 曾把 16 行、标签全 normal 的切片判成"边缘弱用户"，与整窗结论矛盾。
        if len(by_hour[hour]) >= MIN_HOUR_ROWS:
            # 除了整小时总行数，还要**逐 AP** 检查样本量：
            # 该 AP 在本小时只有寥寥几条采样时，均值不可信，不单独下异常结论。
            hour_findings = [
                x for x in hour_res
                if x["anomaly_type"] not in ("正常", "性能提示")
                and len(hour_by_ap.get(x["ap_id"], [])) >= MIN_HOUR_ROWS_PER_AP
            ]
        else:
            hour_findings = []
        hour_findings_all.extend(hour_findings)
        if hour_findings:
            hour_findings.sort(key=lambda f: sev_rank.get(f["level"], 9))
            top = hour_findings[0]
        else:
            top = hour_res[0]
        hourly_trend.append({
            "hour": hour,
            "anomaly_type": top["anomaly_type"], "level": top["level"],
            "network_status": top["network_status"],
            "rsrp_avg": top["rsrp_avg"], "sinr_avg": top["sinr_avg"],
            "thr_avg": top["thr_avg"], "prb_avg": top["prb_avg"],
        })

    if findings:
        findings.sort(key=lambda f: sev_rank.get(f["level"], 9))
        m = findings[0]
        is_anomaly, anomaly_type, level = True, m["anomaly_type"], m["level"]
        anomaly_scope = "window"        # 整窗指标本身就异常
        evidence, cause, suggestion = m["evidence"], m["cause"], m["suggestion"]
        nstatus, affected_aps = m["network_status"], [f["ap_id"] for f in findings]
        candidates = m.get("candidates", [])
        ap_candidates = [{"ap_id": f["ap_id"], "candidates": f["candidates"]} for f in findings]
        confidence = round(min(0.99, 0.6 + 0.08 * len(evidence) + (0.1 if len(findings) > 1 else 0)), 2)
    elif hour_findings_all:
        # 整窗均值被正常时段稀释判"正常"，但存在时段性异常（小时级）
        # → 主结果升级为最严重的小时异常，避免"有告警却说正常"的矛盾。
        # 但必须如实标注这是"时段性异常"而不是整窗异常（原实现只加 0.1 置信度、
        # 对外文案与整窗异常完全无法区分，容易被质疑"被 1 个小时带偏整窗结论"）。
        hour_findings_all.sort(key=lambda f: sev_rank.get(f["level"], 9))
        m = hour_findings_all[0]
        is_anomaly, anomaly_type, level = True, m["anomaly_type"], m["level"]
        anomaly_scope = "hour"          # 仅个别小时越阈，整窗均值正常
        evidence, cause, suggestion = m["evidence"], m["cause"], m["suggestion"]
        cause = (f"时段性异常：仅个别小时越阈，整窗均值仍在正常范围。以下为最严重时段的表现——{cause}")
        nstatus, affected_aps = m["network_status"], [m["ap_id"]]
        candidates = m.get("candidates", [])
        ap_candidates = [{"ap_id": f["ap_id"], "candidates": f["candidates"]} for f in hour_findings_all]
        # 时段性异常的证据只覆盖个别小时，置信度不应高于整窗异常
        confidence = round(min(0.75, 0.5 + 0.08 * len(evidence)), 2)
    else:
        rsrp_avg = avg(rows, "rsrp"); sinr_avg = avg(rows, "sinr")
        thr_avg = avg(rows, "throughput"); prb_avg = avg(rows, "prb_usage")
        is_anomaly, anomaly_type, level = False, "正常", "低"
        anomaly_scope = "none"
        evidence = []
        cause = (f"各AP的RSRP均值{rsrp_avg:.1f}dBm、SINR均值{sinr_avg:.1f}dB、PRB利用率{prb_avg:.1f}%、"
                 f"吞吐量均值{thr_avg:.1f}Mbps，各项指标均在正常范围")
        suggestion, nstatus, affected_aps = "无需处理", "normal", []
        candidates = []
        ap_candidates = []
        confidence = 0.95

    # 3) 多 AP 横向对比（哪台AP最差）—— 指定 AP 时仍列出全部 AP，便于对照
    _cmp_rows = rows_all if ap_filter else rows
    _cmp_by_ap = {}
    for _r in _cmp_rows:
        _cmp_by_ap.setdefault(_r["ap_id"], []).append(_r)
    if ap_filter:
        _cmp_spec = {}
        if _SPEC.get("df") is not None:
            for _ap in _cmp_by_ap:
                _cmp_spec[_ap] = _spec_verdict(_ap, req.window_start, req.window_end)
        _cmp_results = [judge_ap(ap, ap_rows, _cmp_spec.get(ap))
                        for ap, ap_rows in sorted(_cmp_by_ap.items())]
    else:
        _cmp_results = ap_results
    ap_comparison = [
        {k: res[k] for k in ("ap_id", "rsrp_avg", "sinr_avg", "thr_avg", "prb_avg",
                             "weak_ratio", "cong_ratio", "anomaly_type", "level")}
        for res in _cmp_results
    ]

    # 4) 告警历史：小时×AP 命中异常（按时间倒序）
    alarm_history = []
    for hour in sorted(by_hour, reverse=True):
        hour_by_ap = {}
        for r in by_hour[hour]:
            hour_by_ap.setdefault(r["ap_id"], []).append(r)
        for ap, ap_rows in hour_by_ap.items():
            res = judge_ap(ap, ap_rows)
            if res["anomaly_type"] != "正常":
                alarm_history.append({
                    "time": hour, "ap_id": ap,
                    "anomaly_type": res["anomaly_type"], "level": res["level"],
                    "cause": res["cause"],
                })
    alarm_history = alarm_history[:50]

    # 5) 处理方案与对比仿真（闭环里的"方案制定"）：按主异常 AP 生成候选方案。
    #    仿真失败不影响研判本身，所以整体包在 try 里并只打印告警。
    plans = []
    try:
        if is_anomaly and anomaly_type in ("覆盖故障", "网络拥塞", "边缘弱用户"):
            plan_ap = m.get("ap_id")
            plan_rows = by_ap.get(plan_ap, [])
            if plan_ap and plan_rows:
                plans = plan_model.build_plans(
                    plan_ap, anomaly_type, plan_rows,
                    {"scenario": req.scenario, "start": req.window_start, "end": req.window_end},
                    {"tilt": 8, "power": 33},
                )
                for p in plans:
                    _remember_plan(p)
    except Exception as exc:
        print(f"[Main] 方案生成失败（不影响研判）：{exc}")
        plans = []

    # ===== 状态语义计算（队友反馈第4点）=====
    # 窗口是"过去 N 分钟"，出现过的异常不等于**此刻**仍在故障，必须分开表达：
    #   fault_active          窗口末尾 5 分钟内仍有异常 → 当前仍在故障
    #   recovered_in_window   窗口内有异常区间，但末尾已恢复
    #   performance_warning   指标偏低但未达故障判据（不算异常，给提示）
    #   normal                都没有
    _spans = _anomaly_spans(rows)
    _last_anomaly_at = _spans[-1]["end"] if _spans else None
    _RECOVER_GRACE_SEC = 600   # 10 分钟：数据是分钟级且故障有 ramp-out，5 分钟太紧
    if is_anomaly and _last_anomaly_at:
        try:
            _gap = (datetime.strptime(req.window_end, "%Y-%m-%d %H:%M:%S")
                    - datetime.strptime(_last_anomaly_at, "%Y-%m-%d %H:%M:%S")).total_seconds()
        except (ValueError, TypeError):
            _gap = 0
        _current_state = "fault_active" if _gap <= _RECOVER_GRACE_SEC else "recovered_in_window"
    elif perf_hints:
        _current_state = "performance_warning"
    else:
        _current_state = "normal"

    return {
        "code": 0,
        "message": "success",
        "data": {
            "is_anomaly": is_anomaly,
            # anomaly_scope: window=整窗异常 / hour=仅个别小时异常（整窗均值正常）/ none=正常
            # 前端据此区分展示，避免把"某一小时越阈"讲成"整窗故障"
            "anomaly_scope": anomaly_scope,
            "anomaly_type": anomaly_type,
            "level": level,
            # rule_score 是主字段名（审查报告 B-6）：它衡量的是"规则命中强度"，
            # 不是统计概率。confidence 保留一版用于向后兼容，前端应优先读 rule_score。
            "rule_score": confidence,
            "confidence": confidence,
            # 语义标注：前端据此显示"规则评分·非概率"
            "score_kind": "rule",
            "kpi_evidence": evidence,
            "cause": cause,
            "suggestion": suggestion,
            "affected_aps": affected_aps,
            # 回显"这次是否按 AP 过滤"（None = 未过滤，整窗研判）
            "ap_filter": ap_filter or None,
            "network_status": nstatus,
            "candidates": [{**c, "rule_score": c.get("confidence")} for c in candidates],
            "ap_candidates": ap_candidates,
            "hourly_trend": hourly_trend,        # 新增：小时级趋势
            "ap_comparison": ap_comparison,      # 新增：多AP横向对比
            "alarm_history": alarm_history,      # 新增：告警历史
            # 支撑范围与裁剪说明：网络表有 30 天、频谱表只有前 7 天，
            # 统一按交集研判；越界时明确告知，而不是静默给出结论。
            "data_range": ({"start": _range[0], "end": _range[1]} if _range else None),
            "range_notice": range_notice,
            # 分钟级异常区间：申报书 L17 要求"定位异常的起止时间"，
            # alarm_history 只有小时粒度，这里给出精确到分钟的连续区间
            "anomaly_spans": _spans,
            # ===== 状态语义（队友反馈：必须区分"当前故障/窗口内已恢复/性能提示"）=====
            # 默认分析是"过去 N 分钟"，窗口里出现过的异常 ≠ 此刻仍在故障。
            "current_state": _current_state,
            "last_anomaly_at": _last_anomaly_at,
            "performance_hints": [
                {"ap_id": h["ap_id"],
                 "metric": ((h.get("evidence") or [{}])[-1]).get("metric", "sinr_low_share"),
                 "value": ((h.get("evidence") or [{}])[-1]).get("avg_value"),
                 "threshold": ((h.get("evidence") or [{}])[-1]).get("threshold", 0.5),
                 "cause": h["cause"], "suggestion": h["suggestion"],
                 "evidence": h.get("evidence") or []}
                for h in perf_hints
            ],
            "plans": plans,                      # 新增：处理方案 + 对比仿真（闭环"方案制定"）
            # 方案的自然语言说明（阶段2）：LLM 可用时改写，否则用模板；只引用方案内已有数值
            "plan_narrative": explain_plans(plans, anomaly_type),
            "plan_narrative_source": "llm" if ENABLE_LLM else "template",
        },
    }


# ============ 接口14：方案对比仿真（改参数后重算预计效果） ============
# 方案在 /api/analyze/anomaly（及对话结果）里已经带了一份按建议值算好的 simulation；
# 这个接口用于"用户改了参数后重新评估"，逻辑完全复用 plan_model.simulate。
_PLAN_CACHE = {}
_PLAN_CACHE_LIMIT = 200


def _remember_plan(plan):
    """记住已生成的方案，供 /api/plan/simulate 反查参数定义。

    方案 id 按 (AP, 故障类型, 序号) 生成，重复研判会覆盖同一条，所以缓存不会无限膨胀；
    仍然加一个上限，避免长期运行累积（教训来自 _FORECAST_CACHE）。
    """
    if len(_PLAN_CACHE) >= _PLAN_CACHE_LIMIT:
        for key in list(_PLAN_CACHE)[:50]:
            _PLAN_CACHE.pop(key, None)
    _PLAN_CACHE[plan["id"]] = plan


class PlanSimulateRequest(BaseModel):
    plan_id: str
    scenario: str
    window_start: str
    window_end: str
    ap_id: str | None = None
    parameter_values: dict[str, str | float] = {}


@app.post("/api/plan/simulate", summary="接口14：方案对比仿真（改参数后重算预计效果）")
def plan_simulate(req: PlanSimulateRequest):
    """按用户修改后的参数值重算"保持原配置 vs 采用方案"的指标对比。

    约定：
      * 只接受**本进程生成过**的 plan_id（缓存命中）；否则提示重新研判，不做猜测性仿真；
      * 参数在服务端按方案定义再夹一次范围，非法值不会算进结论；
      * 返回结构与方案内自带的 simulation 完全一致，前端可直接替换展示。
    """
    plan = _PLAN_CACHE.get(req.plan_id)
    if plan is None:
        return error_response(
            ERROR_CODE["PARAM_INVALID"],
            "方案已过期（可能服务已重启），请重新发起一次研判后再仿真")

    if req.scenario not in VALID_SCENARIOS:
        return error_response(ERROR_CODE["PARAM_INVALID"],
                              "scenario仅支持" + "/".join(VALID_SCENARIOS))
    if req.window_start > req.window_end:
        return error_response(ERROR_CODE["PARAM_OUT_OF_RANGE"], "起始时间不能晚于结束时间")

    ap_id = req.ap_id or plan["objectIds"][0]
    rows = [r for r in filter_rows(req.scenario, req.window_start, req.window_end)
            if r["ap_id"] == ap_id]
    if not rows:
        return error_response(ERROR_CODE["PARAM_INVALID"],
                              f"{ap_id} 在该窗口没有采样，无法做同条件对比")

    scope = {"scenario": req.scenario, "start": req.window_start, "end": req.window_end}
    return success_response(plan_model.simulate(plan, rows, req.parameter_values or {}, scope, ap_id))

# ============ 接口3：自然语言对话（统一规则源：llm_agent.rule_intent → LLM 增强 → 工具注册表分发） ============
class ChatRequest(BaseModel):
    message: str
    session_id: str = "sess_001"
    scenario: str = "校园"
    # 第4周：前端可把当前展示窗口传给工具，保证"对话口径=页面口径"；不传则用默认三天
    window_start: str | None = None
    window_end: str | None = None
    # 回放模式：前端播放器携带当前模拟时刻，作为自然语言时间的日期基准 + 防未来上限
    current_time: str | None = None

# VALID_SCENARIOS 已提前到顶部错误码区定义（启动自检需要它）

# 对话工具默认窗口（前端未传时使用）；接口2/热力图/拓扑接口的默认口径不变
# 工具默认窗口（审查报告 B-4）：原来是写死的 2026-01-01~01-03，而数据集是 7/30 天，
# 一旦走到兜底分支就会给出与页面口径不符的窗口。改为按**实际加载的数据范围**计算。
# 正常路径由 _default_window() 动态取"最近 24h"，这两个常量只在完全没有数据时才用。
_TOOL_TIMESTAMPS = [r["timestamp"] for r in DF] if isinstance(DF, list) and DF else []
TOOL_WINDOW_START = min(_TOOL_TIMESTAMPS) if _TOOL_TIMESTAMPS else "2026-01-01 00:00:00"
TOOL_WINDOW_END = max(_TOOL_TIMESTAMPS) if _TOOL_TIMESTAMPS else "2026-01-03 23:59:59"

def _default_window(scenario: str):
    """AI 工具默认窗口（方案B）：跟随场景数据最新时刻往前推 24 小时。
    回放/普通场景漏传时间时，默认值永远贴近当前，避免拿到写死的 01-01~01-03 与页面口径不一致。

    注意（数据双区间问题）：网络表覆盖 30 天（至 01-30），频谱表只覆盖 7 天（至 01-07）。
    若默认窗口取网络表末端（01-29~01-30），它落在频谱覆盖之外，
    用户一进预测预警就会看到"参考数据不足"。所以这里把末端夹到
    **网络表 ∩ 频谱表** 的交集内 —— 预测链路始终可用，KPI 查询也不受影响。
    """
    rows = filter_rows(scenario, "2026-01-01 00:00:00", "2026-12-31 23:59:59")
    if not rows:
        return TOOL_WINDOW_START, TOOL_WINDOW_END
    end = max(r["timestamp"] for r in rows)
    if _SPEC.get("max_ts") is not None:
        spec_end = _SPEC["max_ts"].strftime("%Y-%m-%d %H:%M:%S")
        end = min(end, spec_end)
    start = (datetime.fromisoformat(end) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    return start, end


def _resolve_time(expr: str, base_date: str | None, relative_base: str | None) -> str | None:
    """把时间表达式解析为 'YYYY-MM-DD HH:MM:SS'。
    expr 形态：'12:34'（时分，需 base_date 拼日期）、'01-02 09:30'（月-日 时分）、
    '-1h'/'-30m'（相对，以 relative_base 为基准回推）、'now'（= relative_base）。"""
    if not expr:
        return None
    if expr == "now":
        return relative_base
    if expr.startswith("-"):
        if not relative_base:
            return None
        try:
            base = datetime.fromisoformat(relative_base)
            delta = expr[1:]
            if delta.endswith("h"):
                return (base - timedelta(hours=float(delta[:-1]))).strftime("%Y-%m-%d %H:%M:%S")
            if delta.endswith("m"):
                return (base - timedelta(minutes=float(delta[:-1]))).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    m = re.match(r"^(\d{1,2})-(\d{1,2}) (\d{1,2}):(\d{2})$", expr)   # '01-02 09:30'
    if m:
        year = (base_date or "2026")[:4]
        return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d} {int(m.group(3)):02d}:{int(m.group(4)):02d}:00"
    m = re.match(r"^(\d{1,2}):(\d{2})$", expr)                         # '12:34'
    if m:
        if not base_date:
            return None
        return f"{base_date} {int(m.group(1)):02d}:{int(m.group(2)):02d}:00"
    return None


def parse_time_range(tr, current_time: str | None = None):
    """把 time_range（LLM 或规则提取的表达）解析为查询窗口 (start, end)。
    回放红线：解析出的 end 超过 current_time（当前模拟时刻）→ 裁剪；start 超过 → 拒绝（None）。
    解析失败返回 None，上层回退默认窗口，保证演示不被打断。"""
    try:
        if isinstance(tr, str):
            extracted = extract_time_range(tr)
            if not extracted:
                return None
            tr = extracted
        start_expr, end_expr = tr.get("start"), tr.get("end")
        if not start_expr or not end_expr:
            return None
        base_date = current_time[:10] if current_time else None
        start = _resolve_time(start_expr, base_date, current_time)
        end = _resolve_time(end_expr, base_date, current_time)
        if not start or not end:
            return None
        if tr.get("single"):   # 单点"12:34" → 该分钟窗口
            end = (datetime.fromisoformat(start)
                   + timedelta(seconds=59)).strftime("%Y-%m-%d %H:%M:%S")
        if current_time:
            if end > current_time:
                end = current_time          # 防未来：裁剪到当前模拟时刻
            if start > current_time:
                return None                 # 整个窗口在未来 → 拒绝
        return start, end
    except Exception:
        return None

def resolve_scenario(params_scenario, default_scenario):
    """场景白名单校验：LLM 可能把"A区"之类当成场景，无效则回退请求默认场景"""
    return params_scenario if params_scenario in VALID_SCENARIOS else default_scenario

# ---- 工具1：KPI 概况查询（真实 CSV 均值；窗口跟随前端，未传用默认三天） ----
# 回放模式：回复注明分析截至时刻 win_end，保证"对话口径=页面口径"（win_end 未传时回落默认三天）
def tool_query_kpi(scenario: str, win_start: str | None = None, win_end: str | None = None,
                   ap_id: str | None = None) -> dict:
    win_start, win_end = win_start or TOOL_WINDOW_START, win_end or TOOL_WINDOW_END
    ap_filter = (ap_id or "").strip() or None
    rows = filter_rows(scenario, win_start, win_end, ap_id=ap_filter)
    subject = ap_filter or "网络整体"
    if rows:
        # 结论文案必须与数据一致：原先无论数据多差都写"整体运行平稳"，
        # 实测窗口 RSRP -111.2 dBm / SINR -15.0 dB 时仍显示"平稳"，评委一眼可见矛盾。
        _n = len(rows)
        _weak = sum(1 for r in rows if r.get("network_status") == "weak_coverage") / _n
        _cong = sum(1 for r in rows if r.get("network_status") == "congestion") / _n
        _rsrp, _sinr = avg(rows, "rsrp"), avg(rows, "sinr")
        if _weak > 0.05 or _cong > 0.05:
            _verdict = (f"本时段存在明显劣化：弱覆盖采样占比 {_weak*100:.0f}%、"
                        f"拥塞采样占比 {_cong*100:.0f}%，不宜按'运行平稳'理解")
        elif _rsrp < -100 or _sinr < 5:
            _verdict = "本时段指标整体偏低（虽未达异常判定阈值，但已接近边界）"
        else:
            _verdict = "当前时段运行平稳"
        reply = (f"截至{win_end}，{subject}：{_verdict}。RSRP均值{_rsrp:.1f}dBm，"
                 f"SINR均值{_sinr:.1f}dB，平均吞吐量{avg(rows,'throughput'):.1f}Mbps，"
                 f"PRB利用率{avg(rows,'prb_usage'):.1f}%，共{_n}条采样。")
    elif ap_filter:
        reply = f"截至{win_end}，未找到 {ap_filter} 在 {win_start} ~ {win_end} 的采样数据（该时段无该 AP 的采样）。"
    else:
        reply = f"截至{win_end}，未找到场景「{scenario}」在所选时段的数据。"
    return {"reply": reply, "intent": "query_kpi", "chart": None,
            "query_ref": {"scenario": scenario, "start_time": win_start, "end_time": win_end}}

# ---- 工具2：故障研判（意图 → 确定性规则引擎=接口2，LLM 不参与计算，规避幻觉） ----
def _build_fault_reply(d: dict, scenario: str, win_start: str, win_end: str) -> str:
    """故障研判文案：LLM 润色四段式（发现问题/判断依据/候选原因与不确定性/下一步验证）；
    LLM 不可用或异常时降级为同结构模板。数字与结论全部来自规则引擎 d，LLM 只负责表达、不编数据。"""
    ap = "、".join(d.get("affected_aps") or ["未知AP"])
    cand = d.get("candidates") or []
    cand_txt = "；".join(
        f"候选{c.get('rank')}：{c.get('cause')}（规则评分 {c.get('rule_score', c.get('confidence')) or '-'}，非概率）" for c in cand
    ) or "暂无候选原因分解"
    # ---- 降级模板：本身就是四段式，LLM 挂了回复结构也不崩 ----
    if not d.get("is_anomaly"):
        return (f"【发现问题】{scenario} {win_start}~{win_end} 时段，未检测到明显异常。\n"
                f"【判断依据】{d.get('cause', '各项指标均在正常范围')}。\n"
                f"【下一步】持续监测即可；如指标出现波动，可重新发起研判。")
    fallback = (
        f"【发现问题】{scenario} {win_start}~{win_end} 时段，{ap} 被判定存在{d.get('anomaly_type')}"
        f"（严重程度：{d.get('level')}）。\n"
        f"【判断依据】{d.get('cause')}。\n"
        f"【候选原因与限制】{cand_txt}。上述为按指标关联推出的候选解释，尚未取得现场配置与环境数据，"
        f"不能确认为根因；干扰与负荷因素仍需排查。\n"
        f"【下一步验证】{d.get('suggestion')}。建议先核对现场配置与环境变化，再针对候选原因准备验证；"
        f"验证后比较覆盖分布、相关指标与连接关系，判断是否改善及是否引入新问题。"
    )
    # ---- LLM 润色：只喂结构化证据，禁止它编数据 ----
    try:
        evidence = {
            "scenario": scenario, "window": [win_start, win_end],
            "anomaly_type": d.get("anomaly_type"), "level": d.get("level"),
            "confidence": d.get("confidence"), "affected_aps": d.get("affected_aps"),
            "conclusion": d.get("cause"), "suggestion": d.get("suggestion"),
            "kpi_evidence": d.get("kpi_evidence"), "candidates": cand,
        }
        sys_prompt = (
            "你是无线网络运维分析助手。下面是后端规则引擎对某时段网络异常的结构化研判结果（JSON）。"
            "请把它改写成给运维人员看的中文分析回复，严格按四段输出，每段一个小标题：\n"
            "【发现问题】哪个AP、什么异常、严重程度。\n"
            "【判断依据】引用JSON里的指标数值，解释这些数据为什么支持该判断。\n"
            "【候选原因与限制】按优先级列出候选原因，明确这是候选解释而非已确认根因，说明还有哪些因素待排查。\n"
            "【下一步验证】先核查什么、再怎么验证、验证时对比哪些指标。\n"
            "硬性要求：只能使用JSON中的数值与事实，禁止编造任何数据/AP编号/阈值；不要输出JSON或寒暄，只输出四段正文。"
        )
        out = llm_agent.call_llm_chat(sys_prompt, _json.dumps(evidence, ensure_ascii=False))
        if out and len(out) > 30:
            return out
    except Exception as e:
        print(f"[Main] LLM研判润色失败，降级模板：{e}")
    return fallback


def _llm_rewrite(sys_prompt: str, evidence: dict, fallback: str, tag: str) -> str:
    """把结构化结果交给 LLM 改写成中文说明；LLM 不可用/超时/输出过短则返回模板。

    与研判润色同一套降级策略：**没有 key 也必须有话可说**，所以 fallback 模板
    由调用方用真实数值拼好，LLM 只负责"说得更顺"，绝不参与计算。
    """
    if not ENABLE_LLM:
        return fallback
    try:
        out = llm_agent.call_llm_chat(sys_prompt, _json.dumps(evidence, ensure_ascii=False))
        if out and len(out) > 20:
            return out
    except Exception as e:
        print(f"[Main] LLM {tag}解释失败，降级模板：{e}")
    return fallback


def _forecast_template(data: dict) -> str:
    """预测结果的模板说明（无 LLM 时使用）。只用 data 里已有的数值。"""
    alerts = data.get("alerts") or []
    impacts = data.get("impacts") or []
    parts = []
    if alerts:
        first = alerts[0]
        lead = 0
        try:
            lead = int((datetime.strptime(first["start"], "%Y-%m-%d %H:%M:%S")
                        - datetime.strptime(data.get("base_time", first["start"]),
                                            "%Y-%m-%d %H:%M:%S")).total_seconds() // 60)
        except (KeyError, ValueError, TypeError):
            lead = 0
        if lead > 0:
            parts.append(f"{first['target']} 预计 {lead} 分钟后越阈（{first['title']}）。")
        else:
            parts.append(f"{first['target']} 已处于越阈状态，预计持续（{first['title']}）。")
        if len(alerts) > 1:
            parts.append(f"另有 {len(alerts) - 1} 个对象存在风险。")
    else:
        parts.append("预测窗口内所有对象均未越过告警阈值。")
    if impacts:
        ups = [i for i in impacts if (i.get("points") or [])]
        if ups:
            first_impact = ups[0]
            pts = first_impact["points"]
            parts.append(f"{first_impact.get('label', '指标')}当前约 {pts[0].get('value')}，"
                         f"预测末值约 {pts[-1].get('value')}（区间 {pts[-1].get('lower')}~{pts[-1].get('upper')}）。")
    parts.append("以上为基于历史窗口趋势外推的结果，不含未来故障注入信息；"
                 "预测越远区间越宽，应以区间而非单点解读。")
    return "".join(parts)


def explain_forecast(data: dict) -> str:
    """预测结果的自然语言说明（阶段2）：LLM 优先，否则用模板。"""
    fallback = _forecast_template(data)
    sys_prompt = (
        "你是无线网络预测分析助手。下面是某时段区域电磁态势与频谱的预测结果 JSON。"
        "请用中文写 2~3 句给运维人员看的说明，必须覆盖：①未来会出现什么变化；"
        "②预计多久越阈（有 alerts 才有此项）；③不确定性与使用限制。"
        "硬性要求：只能引用 JSON 中的数值与对象名，禁止编造数据或阈值，"
        "不要输出 JSON、不要寒暄，只输出正文。"
    )
    evidence = {
        "base_time": data.get("base_time"),
        "alerts": [{k: a.get(k) for k in ("target", "title", "severity", "start", "end")}
                   for a in (data.get("alerts") or [])[:3]],
        "impacts": [{"label": i.get("label"), "current": (i.get("points") or [{}])[0].get("value"),
                     "last": (i.get("points") or [{}])[-1].get("value")}
                    for i in (data.get("impacts") or [])],
        "limitations": data.get("limitations") or [],
    }
    return _llm_rewrite(sys_prompt, evidence, fallback, "预测")


def _plans_template(plans: list[dict], anomaly_type: str = "") -> str:
    """方案的模板说明（无 LLM 时使用）。只用方案里已有的名称、summary 与权衡项。"""
    if not plans:
        return ("本次研判未生成处理方案：当前窗口未判定为可处置的异常类型，"
                "或缺少有效采样。")
    items = []
    for idx, plan in enumerate(plans, start=1):
        sim = plan.get("simulation") or {}
        # summary 形如「下调天线下倾角： RSRP -111.24 → …」，已含方案名且自带句号；
        # 这里去掉尾部句号，避免拼出「…（+11%）。；2）」
        summary = (sim.get("summary") or "").strip().rstrip("。")
        if not summary:
            summary = plan.get("name", "方案")
        items.append(f"{idx}）{summary}")
    cost = []
    for plan in plans:
        cost.extend(plan.get("tradeoffs") or [])
    text = f"针对{anomaly_type or '当前异常'}共给出 {len(plans)} 个候选方案：" + "；".join(items) + "。"
    if cost:
        text += f"需要权衡的是：{'；'.join(dict.fromkeys(cost))}。"
    text += ("以上对比基于经验系数估算，未在现网验证；实施前应结合现场工勘，"
             "实施后需复测边缘点位。")
    return text


def explain_plans(plans: list[dict], anomaly_type: str = "") -> str:
    """方案结果的自然语言说明（阶段2）：LLM 优先，否则用模板。"""
    fallback = _plans_template(plans, anomaly_type)
    sys_prompt = (
        "你是无线网络优化助手。下面是针对某个网络异常生成的处理方案及其仿真对比结果（JSON）。"
        "请用中文写 2~4 句说明：①每个方案做什么、预期改善什么；②各方案的代价或风险；"
        "③实施前后需要注意什么。硬性要求：只能引用 JSON 中的方案名、数值与权衡项，"
        "禁止编造指标或承诺效果，不要输出 JSON、不要寒暄，只输出正文。"
    )
    evidence = {
        "anomaly_type": anomaly_type,
        "plans": [{"name": p.get("name"), "summary": (p.get("simulation") or {}).get("summary"),
                   "tradeoffs": p.get("tradeoffs"), "tasks": p.get("tasks")}
                  for p in plans[:4]],
    }
    return _llm_rewrite(sys_prompt, evidence, fallback, "方案")


def _worst_ap(diagnosis: dict) -> str | None:
    """从研判结果的 ap_comparison 里挑"最差"的 AP（异常占比最高、等级最重）。"""
    rows = diagnosis.get("ap_comparison") or []
    if not rows:
        aps = diagnosis.get("affected_aps") or []
        return aps[0] if aps else None
    rank = {"严重": 0, "中等": 1, "轻微": 2, "低": 3}
    best, best_key = None, None
    for row in rows:
        weak = float(row.get("weak_ratio") or 0)
        cong = float(row.get("cong_ratio") or 0)
        key = (rank.get(row.get("level"), 9), -(weak + cong))
        if best_key is None or key < best_key:
            best, best_key = row, key
    return (best or {}).get("ap_id")


def _plan_chain(req, scenario, params) -> dict | None:
    """工具链编排（阶段2）：一句话触发多步。

    目前编排一种最常见的复合问法："哪个 AP 最差 + 它的预测/预警"。
    为什么用确定性规则而不是让 LLM 自由规划：
      · 无 LLM 时也要能演示，不能把核心回路押在外部服务上；
      · 编排路径可解释、可测试（返回 chain 轨迹）；
      · LLM 更适合做"理解问题"，而不是决定工具调用序列（易产生幻觉调用）。
    返回 None 表示不需要编排，交回单工具分发。
    """
    text = req.message
    wants_worst = any(k in text for k in ("最差", "最严重", "哪个ap", "哪个 AP", "哪里最"))
    wants_future = any(k in text for k in ("预测", "预警", "趋势", "接下来"))
    if not (wants_worst and wants_future):
        return None

    window_start = req.window_start or TOOL_WINDOW_START
    window_end = req.window_end or TOOL_WINDOW_END
    steps: list[dict] = []

    diag = analyze_anomaly(AnomalyRequest(scenario=scenario,
                                         window_start=window_start,
                                         window_end=window_end))["data"]
    worst = _worst_ap(diag)
    steps.append({"tool": "analyze_anomaly", "status": "ok",
                  "summary": f"研判完成，最差对象 {worst or '未识别'}（{diag.get('anomaly_type')} / {diag.get('level')}）"})

    forecast_payload = None
    if worst:
        base_dt = datetime.strptime(window_end, "%Y-%m-%d %H:%M:%S")
        hist_start = (base_dt - timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
        forecast_payload = forecast_preview(ForecastPreviewRequest(
            scenario=scenario, baseTime=window_end,
            history=_TimeRange(start=hist_start, end=window_end),
            horizonMinutes=60))["data"]
        steps.append({"tool": "forecast_preview", "status": "ok",
                      "summary": f"未来 60 分钟预警 {len(forecast_payload.get('alerts') or [])} 条"})

    return {
        "kind": "chain",
        "intent": "worst_ap_forecast",
        "chain": steps,
        "reply": (f"已按两步执行：先研判找出最差对象，再对它做未来 60 分钟预测。"
                  f"最差对象为 {worst or '未识别'}。"),
        "worst_ap": worst,
        "diagnosis": diag,
        "forecast": forecast_payload,
        "chart": None,
    }


def tool_fault_diagnosis(scenario: str, win_start: str | None = None, win_end: str | None = None,
                         ap_id: str | None = None) -> dict:
    """故障研判。ap_id 指定时只研判该 AP（默认 None = 现有行为，整窗按 AP 逐一研判）。

    修复的缺陷：意图层早就抽出了 ap_id（intent_debug.parsed.ap_id 能看到 AP-01），
    但分发时被丢掉，于是"问 AP-01"得到的是"窗口内最差的 AP"。这里把它贯通到
    analyze_anomaly（底层 filter_rows 本来就支持 ap_id 过滤）。
    """
    win_start, win_end = win_start or TOOL_WINDOW_START, win_end or TOOL_WINDOW_END
    ap_filter = (ap_id or "").strip() or None
    an = analyze_anomaly(AnomalyRequest(scenario=scenario, window_start=win_start,
                                        window_end=win_end, ap_id=ap_filter))
    d = an["data"]
    reply = _build_fault_reply(d, scenario, win_start, win_end)
    if ap_filter:
        # 明确回答"你问的那台"，并把同窗口其他 AP 的异常作为附加信息给出，
        # 既不答错对象，也不藏信息（故障期采样可能只落在故障 AP 上）。
        others = [c["ap_id"] for c in (d.get("ap_comparison") or [])
                  if c.get("ap_id") != ap_filter
                  and c.get("anomaly_type") not in ("正常", "性能提示")]
        if d.get("reason") and "无采样" in str(d.get("reason")):
            reply = (f"已按 {ap_filter} 过滤：该时段没有 {ap_filter} 的采样数据，无法给出结论。"
                     + (f"同窗口有异常的是 {'、'.join(others)}，如需查看请指定该 AP。" if others else ""))
        else:
            reply = f"已按 {ap_filter} 过滤。\n" + reply
            if others:
                reply += f"\n补充：同窗口 {'、'.join(others)} 也存在异常，如需查看请指定该 AP。"
    chart = {
        "type": "line",
        "option": {
            "xAxis": {"data": [h["hour"] for h in d["hourly_trend"]]},
            "series": [{"name": "RSRP(dBm)", "data": [h["rsrp_avg"] for h in d["hourly_trend"]]}],
        },
    }
    return {"reply": reply, "intent": "fault_diagnosis", "chart": chart,
            "detail": d,
            # 方案放在对话结果的顶层：前端 services/backend.ts 读的是 data.plans
            "plans": d.get("plans", []),
            "query_ref": {"scenario": scenario, "ap_id": ap_filter,
                          "start_time": win_start, "end_time": win_end}}

# ---- 工具3：热力图（真实：按 UE 坐标网格聚合 RSRP，ECharts heatmap 直接渲染） ----
def tool_heatmap(scenario: str, win_start: str | None = None, win_end: str | None = None) -> dict:
    win_start, win_end = win_start or TOOL_WINDOW_START, win_end or TOOL_WINDOW_END
    rows = filter_rows(scenario, win_start, win_end)
    if not rows:
        return {"reply": f"未找到场景「{scenario}」的数据，暂无法生成热力图。",
                "intent": "get_heatmap", "chart": None, "query_ref": {"scenario": scenario}}
    # 监测区域（与 data_generator.py MAP_RANGE 一致）与网格大小
    x_min, x_max, y_min, y_max = 20.0, 480.0, 20.0, 430.0
    grid = 20.0  # 20m×20m → 23列×21行，热力更细腻
    nx = int((x_max - x_min) // grid) + 1
    ny = int((y_max - y_min) // grid) + 1
    # 网格聚合：ue_x/ue_y → RSRP 均值
    acc = {}  # (ix, iy) -> [sum_rsrp, count]
    for r in rows:
        try:
            ux, uy, rsrp = float(r["ue_x"]), float(r["ue_y"]), float(r["rsrp"])
        except Exception:
            continue
        ix = min(int((ux - x_min) // grid), nx - 1)
        iy = min(int((uy - y_min) // grid), ny - 1)
        s, c = acc.get((ix, iy), (0.0, 0))
        acc[(ix, iy)] = (s + rsrp, c + 1)
    x_labels = [round(x_min + i * grid, 1) for i in range(nx)]
    y_labels = [round(y_min + i * grid, 1) for i in range(ny)]
    data = [[ix, iy, round(s / c, 2)] for (ix, iy), (s, c) in sorted(acc.items())]
    option = {
        "tooltip": {"position": "top"},
        "grid": {"height": "60%", "top": "10%"},
        "xAxis": {"type": "category", "data": x_labels, "name": "X(m)",
                  "splitArea": {"show": True}},
        "yAxis": {"type": "category", "data": y_labels, "name": "Y(m)",
                  "splitArea": {"show": True}},
        "visualMap": {
            "min": -120, "max": -60, "calculable": True, "orient": "horizontal",
            "left": "center", "bottom": "2%", "text": ["强", "弱"],
            "inRange": {"color": ["#313695", "#4575b4", "#74add1", "#abd9e9",
                                  "#e0f3f8", "#fee090", "#fdae61", "#f46d43", "#d73027"]},
        },
        "series": [{
            "name": "RSRP(dBm)", "type": "heatmap", "data": data,
            "label": {"show": False},
            "emphasis": {"itemStyle": {"shadowBlur": 10, "shadowColor": "rgba(0,0,0,0.5)"}},
        }],
    }
    # 回放模式：回复注明分析截至时刻，与页面模拟时钟保持一致
    return {"reply": f"截至{win_end}，已生成「{scenario}」信号热力图：{len(data)}个采样网格，覆盖 X {x_min}~{x_max}m、Y {y_min}~{y_max}m。",
            "intent": "get_heatmap", "chart": {"type": "heatmap", "option": option},
            "detail": {"grid_count": len(data), "grid_size": grid,
                       "x_range": [x_min, x_max], "y_range": [y_min, y_max],
                       "scenario": scenario},
            "query_ref": {"scenario": scenario, "start_time": win_start, "end_time": win_end}}

# ---- 工具4：网络拓扑（基于 CSV 真实连接关系：AP 服务了哪些 UE） ----
def tool_topology(scenario: str, win_start: str | None = None, win_end: str | None = None) -> dict:
    win_start, win_end = win_start or TOOL_WINDOW_START, win_end or TOOL_WINDOW_END
    rows = filter_rows(scenario, win_start, win_end)
    if not rows:
        return {"reply": f"未找到场景「{scenario}」的数据，暂无法生成拓扑。",
                "intent": "get_topology", "chart": None, "query_ref": {"scenario": scenario}}
    ap_set = sorted({r["ap_id"] for r in rows})
    ue_set = sorted({r["user_id"] for r in rows})
    links, seen = [], set()
    for r in rows:
        key = (r["ap_id"], r["user_id"])
        if key not in seen:
            seen.add(key)
            links.append({"source": r["ap_id"], "target": r["user_id"]})
    nodes = [{"id": ap, "category": "AP"} for ap in ap_set] + \
            [{"id": ue, "category": "UE"} for ue in ue_set]
    # 回放模式：回复注明分析截至时刻，与页面模拟时钟保持一致
    return {"reply": f"截至{win_end}，已生成「{scenario}」的网络拓扑：{len(ap_set)}个AP、{len(ue_set)}个UE、{len(links)}条连接。",
            "intent": "get_topology",
            "chart": {"type": "graph", "option": {"nodes": nodes, "links": links}},
            "detail": {"node_count": len(nodes), "link_count": len(links), "scenario": scenario},
            "query_ref": {"scenario": scenario, "start_time": win_start, "end_time": win_end}}

# ---- 工具5：未来预测（复用 forecast_preview，把预警拼成自然语言） ----
def tool_forecast(scenario: str, win_start: str | None = None, win_end: str | None = None, message: str = "") -> dict:
    """AI对话问"未来会怎样"时调用：以对话窗口末尾为基准时刻，跑一次预测，把预警拼成人话。"""
    import re
    win_end = win_end or TOOL_WINDOW_END
    # 从用户消息提取预测时长（如"未来120分钟""未来2小时""接下来半小时"），默认30分钟
    horizon = 30
    m = re.search(r'未来\s*(\d+)\s*(分钟|分)', message)
    if m:
        horizon = int(m.group(1))
    else:
        m = re.search(r'未来\s*(\d+)\s*小时', message)
        if m:
            horizon = int(m.group(1)) * 60
        elif '半小时' in message:
            horizon = 30
        elif '两小时' in message or '2小时' in message or '120分钟' in message:
            horizon = 120
    horizon = max(5, min(horizon, 120))  # 夹到5~120分钟
    try:
        base_dt = datetime.strptime(win_end, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return {"reply": "暂无法确定当前时刻，预测不可用。",
                "intent": "forecast", "chart": None, "query_ref": {"scenario": scenario}}
    # 频谱数据只到 01-07，若对话窗口末尾超出范围，回退到频谱最新时刻作基准
    if _SPEC["max_ts"] is not None:
        max_dt = _SPEC["max_ts"] if isinstance(_SPEC["max_ts"], datetime) \
            else datetime.strptime(str(_SPEC["max_ts"]), "%Y-%m-%d %H:%M:%S")
        if base_dt > max_dt:
            base_dt = max_dt
            win_end = max_dt.strftime("%Y-%m-%d %H:%M:%S")
    hist_start = (base_dt - timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        req = ForecastPreviewRequest(
            scenario=scenario, baseTime=win_end,
            history=_TimeRange(start=hist_start, end=win_end),
            horizonMinutes=horizon,
        )
        resp = forecast_preview(req)
        data = resp.get("data", {})
    except Exception as e:
        return {"reply": f"预测模块暂时不可用：{e}",
                "intent": "forecast", "chart": None, "query_ref": {"scenario": scenario}}

    status = data.get("status")
    if status == "insufficient_data":
        return {"reply": "当前时刻超出频谱数据覆盖范围（2026-01-01~01-07），暂无法预测。",
                "intent": "forecast", "chart": None,
                "query_ref": {"scenario": scenario, "end_time": win_end}}

    alerts = data.get("alerts", [])
    if not alerts:
        reply = (f"以 {win_end} 为基准，未来{horizon}分钟内未发现明显网络风险，"
                 f"各AP负载与覆盖处于正常区间。如需查看详细频谱，可打开右上角「预测预警」面板。")
    else:
        sev_text = {"high": "高风险", "medium": "中风险", "low": "低风险"}
        lines = [f"以 {win_end} 为基准，未来{horizon}分钟内发现 {len(alerts)} 项风险："]
        for i, a in enumerate(alerts, 1):
            lines.append(f"  {i}. [{sev_text.get(a['severity'], a['severity'])}] {a['title']}：{a['summary']}")
        lines.append("建议打开「预测预警」面板查看频谱热力图，并点击「交给AI详细研判」获取处置方案。")
        reply = "\n".join(lines)

    return {"reply": reply, "intent": "forecast", "chart": None,
            "detail": {"alert_count": len(alerts), "status": status},
            "query_ref": {"scenario": scenario, "end_time": win_end}}

# ---- Function Calling 工具注册表：意图名 → 工具函数（新增工具在这里登记即可） ----
def tool_chat(scenario: str, win_start: str | None = None, win_end: str | None = None) -> dict:
    """闲聊 / 能力询问。

    原实现把任何未命中关键词、LLM 也认不出的问题静默回退成 query_kpi，
    于是用户说"你好"会得到一段 RSRP/SINR 均值汇报（审查报告 B-9）。
    现在明确走这条分支：不编造任何数据，只说明能做什么。
    """
    return {
        "reply": (
            "你好，我是 AI-NetTune 网络运维助手。\n\n"
            "我可以帮你做这些事：\n"
            "1. 故障研判：例如「分析一下 AP-02 在 2026-01-01 10:00 到 12:00 的情况」\n"
            "2. 指标查询：例如「看看当前各 AP 的 RSRP 和吞吐量」\n"
            "3. 空间视图：例如「画一下这个时间窗的热力图」「显示网络拓扑」\n"
            "4. 趋势预测：例如「预测下一个小时的功率变化」\n"
            "5. 报告导出：研判完成后可导出 Markdown / HTML 报告\n\n"
            "你想先看哪一部分？"
        ),
        "intent": "chat",
        "chart": None,
        "diagnosis": None,
        "plans": [],
    }


# 判断"这句话是不是在问数据"。用于把闲聊和数据查询分开：
# 关键词规则未命中时，不能一律当闲聊（"当前各AP的RSRP"没有指令词但明显是数据问题），
# 也不能一律当 KPI 查询（"你好"会得到一段 RSRP 均值汇报）。
_DATA_HINTS = (
    "rsrp", "sinr", "rsrq", "吞吐", "throughput", "prb", "功率", "占用", "干扰",
    "热力", "拓扑", "链路", "节点", "研判", "诊断", "故障", "弱覆盖", "拥塞", "异常",
    "预测", "趋势", "预警", "报告", "导出", "指标", "统计", "采样", "频段", "频谱",
    "方案", "处置", "优化建议", "怎么处理", "闭环",
    "ap", "ue", "用户", "基站", "场景", "校园", "商场", "体育馆", "分析", "对比", "评估",
)


def _looks_like_data_query(message: str) -> bool:
    """粗判是否为数据类问题（宁可判成数据问题，也不要漏掉真实查询）。"""
    text = (message or "").strip().lower()
    return any(hint in text for hint in _DATA_HINTS)


TOOLS = {
    "query_kpi": tool_query_kpi,
    "fault_diagnosis": tool_fault_diagnosis,
    "get_heatmap": tool_heatmap,
    "get_topology": tool_topology,
    "forecast": tool_forecast,
    "chat": tool_chat,
}


# 说明：ENABLE_LLM 已上移到文件顶部配置区（那里定义了环境变量 NETTUNE_ENABLE_LLM 开关）。


# ============ 多轮会话上下文（缺口6） ============
# 原来 ChatRequest.session_id 只声明、从未使用，于是每轮都是"失忆"的单轮调用：
# 用户说"那给它出个方案"，系统不知道"它"是谁。
# 这里给每个 session 留存最近若干轮的关键槽位（场景/时间窗/AP），
# 遇到指代词时从上一轮继承，并把上下文一并交给 LLM 做意图识别。
_SESSIONS: dict[str, list[dict]] = {}
_SESSION_TURNS = 8          # 每个会话保留最近 8 轮
_SESSION_LIMIT = 50         # 最多保留 50 个会话（防止长跑内存增长）
_REFER_PATTERNS = (
    "它", "他", "她", "那个", "这个", "该", "这条", "上一条", "上面", "刚才",
    "再", "继续", "然后", "还有", "呢", "换成", "改成", "同样",
)


def _remember_turn(session_id: str, turn: dict) -> None:
    """记录一轮对话，并按上限淘汰（会话数 + 轮数双限，避免内存无界增长）。"""
    turns = _SESSIONS.setdefault(session_id, [])
    turns.append(turn)
    del turns[:-_SESSION_TURNS]
    while len(_SESSIONS) > _SESSION_LIMIT:
        _SESSIONS.pop(next(iter(_SESSIONS)), None)


def _context_line(turns: list[dict]) -> str:
    """把最近几轮压成一行上下文，供 LLM 做指代消解。"""
    if not turns:
        return ""
    parts = []
    for t in turns[-3:]:
        seg = f"用户问「{t.get('question', '')}」→ 识别为 {t.get('intent', '')}"
        if t.get("ap_id"):
            seg += f"，对象 {t['ap_id']}"
        if t.get("window_start"):
            seg += f"，窗口 {t.get('window_start')}~{t.get('window_end')}"
        parts.append(seg)
    return "【对话上文】" + "；".join(parts) + "。"


def _normalize_ap(value) -> str | None:
    """把各种写法的 AP 编号统一成 AP-XX。

    为什么需要：大模型返回的 params 里常是 "ap-1"（小写、无补零），
    直接覆盖会把正则抽到的 "AP-01" 变成 "ap-1"，导致前端筛选/展示不一致
    （实测配 key 后 test_chat_intent_debug 因此失败）。
    """
    if not value:
        return None
    match = re.search(r"AP\s*[-－]?\s*0*(\d+)", str(value), re.IGNORECASE)
    if match:
        return f"AP-{int(match.group(1)):02d}"
    return str(value)


@app.post("/api/agent/chat")
def agent_chat(req: ChatRequest):
    # 1. 第一层：统一关键词规则（llm_agent.rule_intent，独立主路径，完全不依赖LLM）
    rule = rule_intent(req.message)
    intent = rule.get("intent", "query_kpi")
    params = rule.get("params", {})
    # 规则引擎不一定抽 AP 编号，但用户话里常直接说"AP-02"。
    # 这里补一个显式提取，让 intent_debug.parsed 能如实反映"我理解你指的是哪台"。
    if not params.get("ap_id"):
        _ap_hit = re.search(r"AP\s*[-－]?\s*0*(\d+)", req.message, re.IGNORECASE)
        if _ap_hit:
            params["ap_id"] = f"AP-{int(_ap_hit.group(1)):02d}"
    else:
        params["ap_id"] = _normalize_ap(params["ap_id"])
    hit = rule.get("hit", False)

    # 2. 第二层：关键词未命中 + 启用LLM时，才调用大模型增强识别（get_intent 内部自带降级）
    intent_source = "rule" if hit else "fallback"
    # 大模型自评的意图理解置信度（0~1）。规则命中路径没有这个值，保持 None —— 
    # 不拿规则分数冒充"概率"，与 B-6 的 rule_score 口径一致。
    intent_confidence: float | None = None
    turns = _SESSIONS.get(req.session_id, [])
    context_note = _context_line(turns) if turns else ""
    if not hit and ENABLE_LLM:
        try:
            # 把上文一并交给 LLM，让它能理解"那给它出个方案"里的"它"
            intent_obj = get_intent(f"{context_note}{req.message}" if context_note else req.message)
            intent = intent_obj.get("intent", intent)
            # 大模型返回的 params 不能直接覆盖已有槽位：
            # 它可能给 null/空串（把正则抽到的值清掉），也可能给 "ap-1" 这种非规范写法。
            _llm_params = {k: v for k, v in (intent_obj.get("params") or {}).items()
                           if v not in (None, "", [])}
            params = {**params, **_llm_params}
            if params.get("ap_id"):
                params["ap_id"] = _normalize_ap(params["ap_id"])
            # 标记意图来源，便于前端/答辩说明"这句是大模型识别的还是规则识别的"
            if intent_obj.get("intent"):
                intent_source = "llm"
                raw_conf = intent_obj.get("confidence")
                try:
                    intent_confidence = float(raw_conf) if raw_conf is not None else None
                except (TypeError, ValueError):
                    intent_confidence = None
        except Exception:
            # get_intent 已内部降级到 rule_intent，此处仅做最终兜底，不抛出错误
            pass

    # 2.4 "出个方案/怎么处置"这类请求没有独立工具：方案本身是研判的产物
    #     （analyze_anomaly 会带上 plans），所以先落到 fault_diagnosis，
    #     再靠下面的指代继承拿到 AP 与时间窗。否则会被判成闲聊。
    if intent in ("chat", "query_kpi") and any(
            word in req.message for word in ("方案", "处置", "优化建议", "怎么处理", "怎么优化")):
        intent = "fault_diagnosis"

    # 2.5 指代继承：话里出现"它/那个/再/继续"等指代词且本轮未给槽位时，沿用上一轮
    inherited: dict = {}
    if turns and any(word in req.message for word in _REFER_PATTERNS):
        last = turns[-1]
        if not params.get("scenario") and last.get("scenario"):
            params["scenario"] = last["scenario"]
            inherited["scenario"] = last["scenario"]
        if not params.get("ap_id") and last.get("ap_id"):
            params["ap_id"] = last["ap_id"]
            inherited["ap_id"] = last["ap_id"]
        if not req.window_start and last.get("window_start"):
            req.window_start, req.window_end = last["window_start"], last.get("window_end")
            inherited["window"] = f"{last['window_start']}~{last.get('window_end')}"
        if not params.get("time_range") and last.get("time_range"):
            params["time_range"] = last["time_range"]

    # 3. 第三层：最终兜底。
    #    关键词未命中且没有 LLM 可用时，只有"明显不是数据问题"的才走闲聊；
    #    像"当前各AP的RSRP"这种没有指令词但明显在问数据的，仍按 KPI 查询处理。
    #    原实现把一切都回退 query_kpi，于是"你好"会得到一段 RSRP 均值汇报（审查报告 B-9）；
    #    但也不能反过来把数据问题当闲聊，所以用 _looks_like_data_query 分流。
    if not hit and not ENABLE_LLM and not _looks_like_data_query(req.message):
        intent = "chat"
    if intent not in TOOLS:
        intent = "chat"                 # 未知意图一律走闲聊，而不是 query_kpi

    # 只给了一个时间区间、没有任何动作词时，默认按"该时段研判"处理。
    # 否则会掉进闲聊：实测用户输入 "01-01 10:00–11:59" 得到的是帮助文案，
    # 而这块面板就叫"问题研判与证据"，给一段时间最可能就是"分析这段时间"。
    if intent == "chat" and params.get("time_range"):
        intent = "fault_diagnosis"

    # 4. 执行工具：窗口跟随前端（未传则用动态最近 24h，贴近当前，回放漏传不穿帮）
    scenario = resolve_scenario(params.get("scenario"), req.scenario)
    if not req.window_start or not req.window_end:
        # 4.1 自然语言时间：用户话里带了时间（如"分析12:34到12:50"）→ 解析为查询窗口
        tr = params.get("time_range")
        if not tr:
            tr = extract_time_range(req.message)   # 规则兜底：LLM 没提但话里有时间
        parsed = parse_time_range(tr, req.current_time) if tr else None
        if parsed:
            req.window_start, req.window_end = parsed
        # 4.2 仍缺窗口 → 动态最近 24h
        if not req.window_start or not req.window_end:
            req.window_start, req.window_end = _default_window(scenario)
    # 3.5 工具链编排（阶段2）：复合问法先试编排，命中则跳过单工具分发
    chain_result = _plan_chain(req, scenario, params)
    if chain_result is not None:
        data = chain_result
        intent = "chain"
    else:
        handler = TOOLS[intent]
        if intent == "forecast":
            data = handler(scenario, req.window_start, req.window_end, req.message)
        elif intent in ("fault_diagnosis", "query_kpi"):
            # 只有这两个工具支持按 AP 过滤，其余工具签名不变，避免牵连。
            data = handler(scenario, req.window_start, req.window_end, params.get("ap_id"))
        else:
            data = handler(scenario, req.window_start, req.window_end)
    # 记录本轮，供下一轮做指代消解（缺口6）
    _remember_turn(req.session_id, {
        "question": req.message, "intent": intent, "scenario": scenario,
        "window_start": req.window_start, "window_end": req.window_end,
        "ap_id": params.get("ap_id"), "time_range": params.get("time_range"),
    })
    # 把"这一轮用了上文什么"显式回给前端，便于展示"AI 记得上文"
    if isinstance(data, dict):
        data.setdefault("multi_turn", {
            "session_id": req.session_id,
            "turns": len(_SESSIONS.get(req.session_id, [])),
            "inherited": inherited,
        })
        # 意图可解释（阶段2）：让前端能显示"我理解你想看 X 在 Y 时段的情况"，
        # 而不是把意图藏在后端。source 用来区分这句是规则命中还是大模型识别的。
        data.setdefault("intent_debug", {
            "intent": intent,
            "source": intent_source,        # rule / llm / fallback
            "rule_hit": hit,
            "llm_enabled": bool(ENABLE_LLM),
            # 意图理解置信度：只有走大模型时才有值，且是"模型自评、未校准"，
            # 规则命中/兜底路径为 null（不用规则分数冒充概率）
            "confidence": intent_confidence,
            "confidence_kind": "llm_self_reported" if intent_confidence is not None else None,
            "message": req.message,
            "parsed": {
                "scenario": scenario,
                "ap_id": params.get("ap_id"),
                "time_range": params.get("time_range"),
                "window": {"start": req.window_start, "end": req.window_end},
            },
        })
    return {"code": 0, "message": "success", "data": data}


@app.get("/api/data/events", summary="接口7：故障事件清单（来自 data/fault_events.csv）")
def data_events(scenario: str | None = Query(None, description="按场景过滤，不传返回全部")):
    """返回数据生成器同步输出的故障事件清单。

    为什么需要它（审查报告 B-4 / D-2）：前端此前的"案例起点"是手写的三个时间戳，
    数据一换就失效，而且交接文档提到的 fault_events.csv 从未被生成过。
    现在生成器每次运行都输出该表，前端读这个接口即可拿到事件起止时间，
    "事件"只有一份事实来源。

    文件缺失时返回空列表（而不是 500）：事件表缺失不应让工作台打不开。
    """
    path = DATA_DIR / "fault_events.csv"
    if not path.exists():
        return success_response({"events": [], "count": 0,
                                 "note": f"未找到 {path.name}，请用 data_generator.py 重新生成数据"})
    import csv as _csv
    rows = list(_csv.DictReader(path.open(encoding="utf-8-sig")))
    if scenario:
        rows = [r for r in rows if r.get("scenario") == scenario]
    events = rows
    for item in events:
        for key in ("ramp_minutes", "strength"):
            try:
                item[key] = float(item[key])
            except (TypeError, ValueError):
                item[key] = None
    return success_response({"events": events, "count": len(events)})


# ============ 接口15：区域电磁态势帧序列（过去 N 帧 + 未来 N 帧） ============
@app.get("/api/forecast/spatial_frames", summary="接口15：区域电磁态势帧序列（过去N帧 + 未来N帧）")
def spatial_frame_series(
    scenario: str = Query(..., description="场景，如：校园"),
    baseTime: str = Query(..., description="基准时刻 yyyy-MM-dd HH:mm:ss"),
    historyMinutes: int = Query(48, ge=6, le=1920, description="历史总时长（分钟）"),
    frameMinutes: int = Query(6, ge=1, le=240, description="每帧覆盖的分钟数"),
    pastFrames: int = Query(6, ge=1, le=12, description="返回的历史帧数"),
    futureFrames: int = Query(3, ge=1, le=12, description="预测的未来帧数"),
    metric: str = Query("rsrp", description="场指标：rsrp / sinr / throughput"),
):
    """按"帧"返回区域电磁态势：每帧是一张 grid×grid 的二维场。

    为什么要这个接口（申报书「预测覆盖区域电磁频谱态势」+「序列态势图」）：
    接口11 的频谱预测是频率维度的一条带，画出来只有横纹；态势图的参考形态是
    "过去N帧 → 未来N帧"的二维区域图。这里用 IDW 把 UE 采样点插成面，
    未来帧对**每个格点**分别外推，所以故障周边那片区域会随时间扩张/加深。

    未来帧的斜率来自最近若干历史帧，饱和外推（τ=3 帧）避免线性外推到荒谬值；
    max_drift 按指标量级给，并在超限时按峰值等比缩放，防止全场撞上限被夹平。
    """
    if metric not in ("rsrp", "sinr", "throughput"):
        return error_response(ERROR_CODE["PARAM_INVALID"], "metric 仅支持 rsrp / sinr / throughput")
    try:
        base_dt = datetime.strptime(baseTime, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return error_response(ERROR_CODE["PARAM_INVALID"], "baseTime 格式应为 yyyy-MM-dd HH:mm:ss")

    fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
    span = pastFrames * frameMinutes
    if span > historyMinutes:
        pastFrames = max(1, historyMinutes // frameMinutes)

    past, grids = [], []
    meta = {"grid": _spf.GRID, "x_range": [0, 0], "y_range": [0, 0]}
    # 场景统一坐标范围：所有帧用同一 extent 建场，保证"同一个格子 = 同一个位置"。
    # 原先每帧各自按当帧采样点算范围（实测 X 从 14.3~494.3 变到 1.6~484.1），
    # 逐格点趋势外推就作用在了不同物理位置上，序列图的"扩张"不成立。
    scene_extent = _scene_extent(scenario)
    for i in range(pastFrames):
        end = base_dt - timedelta(minutes=(pastFrames - 1 - i) * frameMinutes)
        start = end - timedelta(minutes=frameMinutes)
        built = _spf.build_grid(filter_rows(scenario, fmt(start), fmt(end)),
                                metric, extent=scene_extent)
        meta = {"grid": built["grid"], "x_range": built["x_range"], "y_range": built["y_range"]}
        past.append({"start": fmt(start), "end": fmt(end),
                     "points": built["points"], "values": built["values"]})
        grids.append(built["values"])

    # 指标量级对应的最大漂移（与接口11 的 profile 口径一致）
    drift = {"rsrp": 15.0, "sinr": 8.0, "throughput": 20.0}[metric]
    bounds = {"rsrp": (-140.0, -40.0), "sinr": (-25.0, 30.0), "throughput": (0.0, 150.0)}[metric]
    future = []
    for i, values in enumerate(
            _spf.predict_frames(grids, future=futureFrames, max_drift=drift, bounds=bounds), start=1):
        f_start = base_dt + timedelta(minutes=(i - 1) * frameMinutes)
        future.append({"start": fmt(f_start),
                       "end": fmt(f_start + timedelta(minutes=frameMinutes)),
                       "values": values})

    last_grid = grids[-1] if grids else []
    return success_response({
        "scenario": scenario, "metric": metric, "base_time": baseTime,
        "frame_minutes": frameMinutes,
        "shape": {"grid": meta["grid"],
                  "rows": len(last_grid), "cols": len(last_grid[0]) if last_grid else 0},
        "x_range": meta["x_range"], "y_range": meta["y_range"],
        "past": past, "future": future,
        "note": "每帧为 IDW 插值得到的区域场；未来帧逐格点外推，饱和时间常数 3 帧",
    })


# ============ 接口16：信号导航建议 ============
@app.get("/api/navigate/signal", summary="接口16：信号导航建议（位置感知+覆盖分析+移动建议）")
def navigate_signal(
    scenario: str = Query(..., description="场景，如：校园"),
    start_time: str = Query(..., description="分析窗口开始 yyyy-MM-dd HH:mm:ss"),
    end_time: str = Query(..., description="分析窗口结束 yyyy-MM-dd HH:mm:ss"),
    ue_id: str | None = Query(None, description="目标 UE，不传取样本里的第一个"),
    metric: str = Query("rsrp", description="依据指标：rsrp / sinr / throughput"),
):
    """给出"往哪走信号更好"的建议（分工文档 P1：位置感知、覆盖分析、移动建议）。

    与其他模块共用同一套 IDW 区域场，保证"导航建议"和"区域态势图"口径一致——
    否则会出现"图上这里是绿的、导航却让我走开"的矛盾。

    返回里带 assumptions：这是基于区域信号场的**建议**，不是实时信道测量，
    移动后的实际信号还受遮挡、负载、终端能力影响。前端应原样展示这些前提。
    """
    if metric not in ("rsrp", "sinr", "throughput"):
        return error_response(ERROR_CODE["PARAM_INVALID"], "metric 仅支持 rsrp / sinr / throughput")
    rows = filter_rows(scenario, start_time, end_time)
    if not rows:
        return error_response(ERROR_CODE["PARAM_OUT_OF_RANGE"], "该窗口没有数据，无法做覆盖分析")
    result = _sn.suggest(rows, ue_id, metric)
    if not result.get("available"):
        return error_response(ERROR_CODE["PARAM_OUT_OF_RANGE"], result.get("reason", "无法生成导航建议"))
    return success_response({**result, "scenario": scenario,
                             "window": {"start": start_time, "end": end_time}})


# ============ 接口4：导出分析报告 ============
class ReportRequest(BaseModel):
    scenario: str
    start_time: str
    end_time: str
    format: str = "md"
    include_chart: bool = True

@app.post("/api/export/report", summary="接口4：导出分析报告（真实研判内容）")
def export_report(req: ReportRequest):
    """生成一份包含研判结论、证据链、候选原因、分 AP 对比、趋势与处理方案的报告。

    旧实现只写 4 行均值，且 format=html 时仅把扩展名改成 .html、内容仍是 Markdown。
    现在报告正文由 report_model 生成：md 与 html 各自渲染，include_chart 生效。
    """
    if not req.scenario or not req.scenario.strip():
        return error_response(ERROR_CODE["PARAM_MISSING"], "场景参数不能为空")
    if not req.start_time or not req.end_time:
        return error_response(ERROR_CODE["PARAM_MISSING"], "时间参数不能为空")
    if req.start_time > req.end_time:
        return error_response(ERROR_CODE["PARAM_OUT_OF_RANGE"], "起始时间不能晚于结束时间")
    if req.format not in ("md", "markdown", "html"):
        return error_response(ERROR_CODE["PARAM_INVALID"], "format 仅支持 md / markdown / html")

    rows = filter_rows(req.scenario, req.start_time, req.end_time)
    if not rows:
        # 没有数据就不要产出"0 均值"的报告，明确告知
        return error_response(
            ERROR_CODE["PARAM_INVALID"],
            f"场景「{req.scenario}」在 {req.start_time} ~ {req.end_time} 没有采样，无法生成报告")

    # 复用接口2的研判，保证"报告口径 = 页面口径"
    diagnosis = analyze_anomaly(AnomalyRequest(
        scenario=req.scenario, window_start=req.start_time, window_end=req.end_time))["data"]

    # 小时趋势不足 3 个点时，向**前**补取（最多 6 小时）作为"趋势参考区间"。
    # 目的：2 小时窗口只有 2 个小时点，看不出趋势；补到 8 个点后能看出"持续下滑"。
    # 必须标注清楚：这一段是窗口外的数据，其余章节仍严格对应所选窗口。
    hourly_trend = diagnosis.get("hourly_trend") or []
    trend_range = {"start": req.start_time, "end": req.end_time, "extended": False}
    if 0 < len(hourly_trend) < 3:
        try:
            _ws = datetime.strptime(req.start_time, "%Y-%m-%d %H:%M:%S")
            ext_start = (_ws - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
            _sup = _supported_range()
            if _sup is not None and ext_start < _sup[0]:
                ext_start = _sup[0]
            ext_rows = filter_rows(req.scenario, ext_start, req.end_time)
            if ext_rows:
                ext = analyze_anomaly(AnomalyRequest(
                    scenario=req.scenario, window_start=ext_start, window_end=req.end_time))["data"]
                ext_trend = ext.get("hourly_trend") or []
                if len(ext_trend) > len(hourly_trend):
                    hourly_trend = ext_trend
                    trend_range = {"start": ext_start, "end": req.end_time, "extended": True}
        except Exception:
            pass

    now = datetime.now()
    # 加 4 位随机后缀：原先是 RPT_YYYYMMDD_HHMMSS（秒级），
    # 同一秒内两次导出会生成同名文件并互相覆盖（并发或连点两次即可复现）。
    report_id = "RPT_" + now.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    ctx = {
        "report_id": report_id,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "scenario": req.scenario,
        "start": req.start_time,
        "end": req.end_time,
        "sample_count": len(rows),
        "source_note": "仿真数据集（CSV），非现网实测",
        "means": {k: avg(rows, k) for k in
                  ("rsrp", "sinr", "throughput", "prb_usage", "packet_loss", "latency")},
        "diagnosis": diagnosis,
        "hourly_trend": hourly_trend,
        "trend_range": trend_range,
        "ap_comparison": diagnosis.get("ap_comparison") or [],
        "alarm_history": diagnosis.get("alarm_history") or [],
        "plans": diagnosis.get("plans") or [],
    }

    ext, content = report_model.build_report(ctx, req.format, req.include_chart)
    filename = f"{report_id}.{ext}"
    file_path = REPORTS_DIR / filename

    # 目录不可写（只读容器、Program Files、权限受限等）时不要抛 500 堆栈，
    # 直接把"写到哪个路径、为什么失败"返回给调用方，便于现场定位。
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        return error_response(
            ERROR_CODE["INTERNAL_ERROR"],
            f"报告写入失败：{exc}（目标路径 {file_path}，请确认进程对该目录有写权限）",
        )

    return success_response({
        "report_id": report_id,
        "file_url": f"/api/download/reports/{filename}",
        "format": ext,
        "include_chart": req.include_chart,
        "sample_count": len(rows),
        "bytes": len(content.encode("utf-8")),
    })

# ========== 热力图数据接口 ==========
@app.get("/api/data/heatmap", summary="信号热力图数据")
def get_heatmap(
    scenario: str = Query(..., description="场景名，如：校园"),
    start_time: str | None = Query(None, description="开始时间"),
    end_time: str | None = Query(None, description="结束时间"),
    metric: str = Query("rsrp", description="热力指标：rsrp / sinr")
):
    # 热力图结果缓存（120 秒）：Kriging 的耗时随采样点立方增长
    # （实测 60 分钟窗口 ~0.2s，7 天窗口 ~4~6s）。缓存只复用**完全相同参数**的结果，
    # 数值零变化；回放切换时刻时窗口不同仍会重新计算。
    _ck = (scenario, start_time or "", end_time or "", metric, _data_version())
    _hit = _HEATMAP_CACHE.get(_ck)
    if _hit is not None and time.monotonic() - _hit[0] < _HEATMAP_TTL_SEC:
        return _copy.deepcopy(_hit[1])
    result = _get_heatmap_uncached(scenario, start_time, end_time, metric)
    if isinstance(result, dict) and result.get("code") == 0:
        if len(_HEATMAP_CACHE) >= _HEATMAP_CACHE_MAX:
            _HEATMAP_CACHE.pop(next(iter(_HEATMAP_CACHE)), None)
        _HEATMAP_CACHE[_ck] = (time.monotonic(), _copy.deepcopy(result))
    return result


def _get_heatmap_uncached(scenario, start_time, end_time, metric):
    # 1. 复用已加载数据集，按条件过滤
    rows = filter_rows(
        scenario,
        start_time or "2026-01-01 00:00:00",
        end_time or "2026-12-31 23:59:59"
    )

    if not rows:
        return {
            "code": 0,
            "message": "success",
            "data": {"metric": metric, "method": "grid_mean", "grid_count": 0,
                     "values": [], "variance": None, "sample_points": 0}
        }

    # 2. 空间估计：Kriging 插值（估计未测位置指标 + 方差置信度）
    #    采样点不足或拟合失败时降级为旧逻辑"网格均值"（只输出有采样点的格子）
    spatial = build_spatial(rows, metric)
    if spatial["method"] == "kriging":
        return {
            "code": 0,
            "message": "success",
            "data": {
                "metric": metric,
                "method": "kriging",
                "grid_count": spatial["grid_count"],
                "values": spatial["values"],
                "variance": spatial["variance"],
                "sample_points": spatial["sample_points"],
            }
        }

    # 3. 降级：按 UE 坐标 20m 网格聚合（与对话工具 tool_heatmap 口径一致），计算平均信号值
    x_min, x_max, y_min, y_max = 20.0, 480.0, 20.0, 430.0
    grid = 20.0
    nx = int((x_max - x_min) // grid) + 1
    ny = int((y_max - y_min) // grid) + 1
    grid_map = {}
    for r in rows:
        try:
            ux, uy, val = float(r["ue_x"]), float(r["ue_y"]), float(r[metric])
        except Exception:
            continue
        ix = min(int((ux - x_min) // grid), nx - 1)
        iy = min(int((uy - y_min) // grid), ny - 1)
        grid_map.setdefault((ix, iy), []).append(val)

    # 4. 转成前端ECharts直接能用的格式（x/y 为网格中心坐标）
    result = []
    for (ix, iy), values in grid_map.items():
        avg_val = sum(values) / len(values)
        cx = round(x_min + ix * grid + grid / 2, 1)
        cy = round(y_min + iy * grid + grid / 2, 1)
        result.append([cx, cy, round(avg_val, 2)])

    return {
        "code": 0,
        "message": "success",
        "data": {
            "metric": metric,
            "method": "grid_mean",
            "grid_count": len(result),
            "values": result,
            "variance": None,
            "sample_points": spatial["sample_points"],
        }
    }

# ========== 基础网络拓扑数据接口 ==========
@app.get("/api/network/topology/basic", summary="基础网络拓扑数据")
def get_basic_topology(
    scenario: str = Query(..., description="场景名"),
    start_time: str | None = Query(None, description="窗口起始时间"),
    end_time: str | None = Query(None, description="窗口结束时间"),
    timestamp: str | None = Query(None, description="指定时刻，默认取窗口内第一条数据的时刻")
):
    # 1. 复用已加载数据集；与 /api/data/query 同口径按窗口过滤（未传则默认全量）
    if start_time and end_time:
        rows = filter_rows(scenario, start_time, end_time)
    else:
        rows = filter_rows(scenario, "2026-01-01 00:00:00", "2026-12-31 23:59:59")

    if not rows:
        return {"code": 0, "message": "success", "data": {"nodes": [], "links": []}}

    # 2. 拓扑节点/链路基于整个监测窗口聚合（不再按单时刻过滤）：
    #    窗口内出现过的全部 AP 与 UE 都展示，链路为该 AP-UE 连接在整个窗口的均值指标
    #    注意：timestamp 未传时必须是 None（不能直接判真假——FastAPI 的 Query 默认对象是真值，
    #    直接 `if not timestamp` 会把默认对象原样回吐到响应里）。
    if timestamp is None:
        timestamp = rows[0]["timestamp"]

    # 节点：AP 取场景内全部固定基站（不随窗口消失），UE 取窗口内出现过的终端
    nodes = []
    all_rows = filter_rows(scenario, "2026-01-01 00:00:00", "2026-12-31 23:59:59")
    ap_seen = {}
    for r in all_rows:
        if r["ap_id"] not in ap_seen:
            ap_seen[r["ap_id"]] = (float(r["ap_x"]), float(r["ap_y"]))
    for ap_id in sorted(ap_seen):
        x, y = ap_seen[ap_id]
        nodes.append({"id": ap_id, "type": "ap", "x": x, "y": y})
    ue_seen = set()
    for r in rows:
        if r["user_id"] not in ue_seen:
            ue_seen.add(r["user_id"])
            nodes.append({
                "id": r["user_id"],
                "type": "ue",
                "x": float(r["ue_x"]),
                "y": float(r["ue_y"])
            })

    # 链路：每个 UE 只保留一条主链路 —— 窗口内连接采样占比最高的服务 AP
    #（并列时优先最新时刻仍在服务的 AP）；指标为该连接在窗口内的均值
    links = []
    link_acc = {}
    last_ts = max(r["timestamp"] for r in rows)
    last_ue_ap = {}
    for r in rows:
        if r["timestamp"] == last_ts:
            last_ue_ap[r["user_id"]] = r["ap_id"]
    for r in rows:
        key = (r["ap_id"], r["user_id"])
        acc = link_acc.setdefault(key, {"rsrp": [], "sinr": [], "throughput": [], "count": 0})
        acc["rsrp"].append(float(r["rsrp"]))
        acc["sinr"].append(float(r["sinr"]))
        acc["throughput"].append(float(r["throughput"]))
        acc["count"] += 1
    # 每 UE 选主服务 AP：采样次数最多，并列时选最新时刻服务 AP
    primary_key = {}
    for (source, target), acc in link_acc.items():
        cur = primary_key.get(target)
        is_latest = last_ue_ap.get(target) == source
        if cur is None:
            primary_key[target] = (source, acc["count"], is_latest)
        elif acc["count"] > cur[1] or (acc["count"] == cur[1] and is_latest and not cur[2]):
            primary_key[target] = (source, acc["count"], is_latest)
    for (source, target), acc in link_acc.items():
        if primary_key[target][0] != source:
            continue
        links.append({
            "id": f"{source}->{target}",
            "source": source,
            "target": target,
            "rsrp": round(sum(acc["rsrp"]) / len(acc["rsrp"]), 2),
            "sinr": round(sum(acc["sinr"]) / len(acc["sinr"]), 2),
            "throughput": round(sum(acc["throughput"]) / len(acc["throughput"]), 2),
            "primary": True
        })

    return {
        "code": 0,
        "message": "success",
        "data": {
            "timestamp": timestamp,
            "nodes": nodes,
            "links": links
        }
    }

# 【已删除】GET /api/data/realtime（审查报告 B-1）
#   原实现返回 get_incremental_data() 的结果，字段是 DATA_COLUMNS 那套旧结构，
#   与主数据接口 /api/data/query 的 18 列契约完全不同；前端 0 引用（grep 全仓无命中），
#   docs/api.md 里也没有它的契约。保留它只会让"接口清单"多一个没人用、语义又混乱的项。
#   若将来确实需要"增量拉取"，应按 /api/data/query 的字段契约重新实现，而不是复用旧结构。


# ========== 接口5：区域信号/频谱态势预测（Prophet 时序模型，P1 任务1） ==========
class ForecastRequest(BaseModel):
    scenario: str = "校园"
    window_start: str = "2026-01-01 00:00:00"
    window_end: str = "2026-01-31 23:59:59"


@app.post("/api/analyze/forecast", summary="区域信号/频谱态势预测（Prophet 时序模型）")
def analyze_forecast(req: ForecastRequest):
    """
    对每个 UE × 指标（rsrp/sinr/throughput）：
    1. 按 1 小时桶聚合实测序列（forecast_model.aggregate_hourly）；
    2. 前 80% 训练 Prophet，后 20% 测试 → 测试段实测均值(True) vs 预测均值(Predicted) 与 MAPE；
    3. 测试段之后再延伸 24 小时未来预测，输出 yhat/lower/upper 置信区间。
    容错：序列过短或 Prophet 失败自动降级 moving_average，接口永不崩。
    答辩口径：统一表述为"区域信号/频谱态势预测"，前端预测部分标注"未来预测"。
    """
    rows = filter_rows(req.scenario, req.window_start, req.window_end)
    metrics = DEFAULT_METRICS
    if not rows:
        return success_response({
            "scenario": req.scenario, "window_start": req.window_start,
            "window_end": req.window_end, "method": "no_data",
            "metrics": metrics, "ues": [], "series": {}
        })

    built = build_series(rows, metrics)
    # 顶层 method 如实聚合：全部 prophet / 全部 moving_average / 混合 mixed / 均无法预测 none
    methods = {s["method"] for ue in built["series"].values() for s in ue.values()}
    if not methods or methods == {"none"}:
        top_method = "moving_average"
    elif methods == {"prophet"}:
        top_method = "prophet"
    elif methods == {"moving_average"}:
        top_method = "moving_average"
    else:
        top_method = "mixed"
    # 降级原因如实透出：前端可据此说明"为什么不是机器学习模型"，而不是只看到一个 method
    reasons = sorted({s.get("degrade_reason") for ue in built["series"].values()
                      for s in ue.values() if s.get("degrade_reason")})
    limitations = []
    if top_method in ("moving_average", "mixed"):
        limitations.append(
            "Prophet 不可用或拟合超时，已降级为移动平均（常数预测），"
            "MAE/RMSE 为测试段相对该常数的离散度")
    limitations.extend(reasons)
    return success_response({
        "scenario": req.scenario,
        "window_start": req.window_start,
        "window_end": req.window_end,
        "method": top_method,
        "bin_size_min": built["bin_size_min"],
        "metrics": built["metrics"],
        "ues": built["ues"],
        "series": built["series"],
        "limitations": limitations,
    })


# ============================================================================
# 接口8：时序预测结果查询（GET /api/predict/result，契约 v1）
#   契约：docs/api.md 接口8。组织 main 已将该接口定位为“查询壳”：不新建预测模型，
#         后续转调 /api/analyze/forecast（接口10）的数据做字段映射。
#   本次实现：契约 v1 占位阶段，固定返回 predict_list=[]、total=0、model=placeholder，
#         字段结构保持 v1 不变，前端可先行对接。
#   错误码：必填参数缺失由全局异常处理器统一返回 40001；白名单取值非法与时间格式非法 40002。
# ============================================================================
@app.get("/api/predict/result", summary="接口8：时序预测结果查询（契约 v1 · 占位实现）")
def predict_result(
    scenario: str = Query(..., description="场景，如：校园"),
    start_time: str = Query(..., description="起始时间 yyyy-MM-dd HH:mm:ss"),
    end_time: str = Query(..., description="结束时间 yyyy-MM-dd HH:mm:ss"),
    target_type: str = Query(..., description="ap / ue"),
    target_id: str | None = Query(None, description="对象ID，不传查全部"),
    metric: str = Query(..., description="rsrp / sinr / throughput"),
    interval: str = Query("15min", description="5min/15min/30min/60min"),
):
    """契约 v1 占位实现：只做参数校验，不新建模型、不返回真实预测数据、不读取数据文件。

    参数刻意声明为 str 而非 Literal/Enum：否则 FastAPI 的类型校验会先抛
    RequestValidationError，被全局处理器统一映射成 40001，无法满足契约要求的
    “取值非法返回 40002”。
    """
    # 1. 取值白名单校验（必填缺失由全局异常处理器统一返回 40001）
    if scenario not in VALID_SCENARIOS:
        return error_response(ERROR_CODE["PARAM_INVALID"],
                              "scenario仅支持" + "/".join(VALID_SCENARIOS))
    if target_type not in ("ap", "ue"):
        return error_response(ERROR_CODE["PARAM_INVALID"], "target_type仅支持ap/ue")
    if metric not in ("rsrp", "sinr", "throughput"):
        return error_response(ERROR_CODE["PARAM_INVALID"], "metric仅支持rsrp/sinr/throughput")
    if interval not in ("5min", "15min", "30min", "60min"):
        return error_response(ERROR_CODE["PARAM_INVALID"], "interval仅支持5min/15min/30min/60min")

    # 2. 时间格式校验
    for value in (start_time, end_time):
        try:
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return error_response(ERROR_CODE["PARAM_INVALID"], "时间格式应为yyyy-MM-dd HH:mm:ss")

    # 2.1 时间先后校验（第三轮评审 6.2）：起始时间不得晚于结束时间，非法返回 40003
    #     格式已统一为 "YYYY-MM-DD HH:MM:SS"，字符串字典序即时间序，可直接比较
    if start_time > end_time:
        return error_response(ERROR_CODE["PARAM_OUT_OF_RANGE"],
                              "起始时间不能晚于结束时间")

    # 3. 占位返回：字段与契约 v1 完全一致
    return success_response({
        "target_type": target_type,
        "target_id": target_id,
        "metric": metric,
        "model": "placeholder",
        "predict_list": [],
        "total": 0,
    })


# ============================================================================
# 预测预警 v2 双接口（前端 forecast/api.ts 契约）
#   POST /api/forecast/preview  预测预览：返回 spectra/alerts/impacts
#   POST /api/forecast/analyze   AI 文字研判：基于一轮预测给出原因/证据/方案
# 说明：频谱频段数据（band_id + power_dbm + occupancy_pct）尚未由
#       data_generator.py 扩展完成，preview 暂返回 insufficient_data 占位；
#       analyze 已可独立工作（拉真实历史 KPI 趋势喂 LLM）。
# ============================================================================
# 内存缓存：forecastId -> 该轮 preview 结果，供 analyze 按 alertId 反查预警详情。
# 必须有上限：单份结果实测 0.50 MB（120 分钟档）/ 3.97 MB（1440 分钟档），
# 而前端按 5 分钟槽自动刷新，7 天回放 ≈ 2000 次刷新 → 不设上限会涨到 GB 级。
# 只保留最近使用的若干轮即可：analyze 总是在 preview 之后紧接着调用，命中率不受影响。
_FORECAST_CACHE: dict = {}
_FORECAST_CACHE_LIMIT = 32          # 32 份 ≈ 16 MB（120 档）~ 128 MB（1440 档）


def _remember_forecast(fid: str, result: dict):
    """写入 forecastId -> 结果，超上限时按插入顺序淘汰最旧的一批。"""
    if fid not in _FORECAST_CACHE and len(_FORECAST_CACHE) >= _FORECAST_CACHE_LIMIT:
        for key in list(_FORECAST_CACHE)[: max(1, _FORECAST_CACHE_LIMIT // 4)]:
            _FORECAST_CACHE.pop(key, None)
    _FORECAST_CACHE[fid] = result


class _TimeRange(BaseModel):
    start: str
    end: str


class ForecastPreviewRequest(BaseModel):
    scenario: str
    baseTime: str
    history: _TimeRange
    horizonMinutes: float


class ForecastAnalyzeRequest(BaseModel):
    forecastId: str
    scenario: str
    baseTime: str
    alertId: str | None = None
    history: _TimeRange
    forecast: _TimeRange
    # 前端一直会发这两个字段（forecast/api.ts），但模型原先没声明 →
    # Pydantic 默认忽略多余字段，于是"用户的问题"和"关注时段"被静默丢弃，
    # 研判只能输出通用文案。这里补上声明并真正使用。
    question: str | None = None
    targetWindow: _TimeRange | None = None


def _parse_ts(value: str):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


# ============ 预测外推：阻尼趋势（替代原来的"均值±1 方波"） ============
# 原实现：预测帧 = 历史末 10 分钟各频段均值 ±1 交替；指标预测 = 末值恒定 → 方波与水平线。
# 直接改成线性外推同样不行：故障是阶跃注入，30 分钟窗口拟合出的斜率可达 -2 dBm/分钟，
# 外推 120 分钟会得到 -350 dBm（实测），物理上不存在。
# 因此用「阻尼外推」：前几分钟体现趋势，之后按 1-e^(-k/tau) 指数饱和，
# 再把总偏移夹在 ±max_drift 与指标物理范围内，保证任何 horizon 都落在可信区间。
FORECAST_TREND_WINDOW = 30        # 用最近多少分钟拟合斜率

# 预测曲线的"参考时长"：τ 与归一化峰值都按它算，**不随请求的 horizonMinutes 变化**。
# 目的：同一个未来时刻（例如基准 14:12 的 14:22）在任何预测时长下都得到同一数值；
# 请求时长只决定截取到第几帧，而不是改变曲线本身。
#
# 取 30 的理由（实测对比）：参考值越大 → τ 越大 → 外推越保守、预警越晚。
#   参考 120（τ=72 分钟）：8 个事件提前 1 / 晚 7（EV-005 晚 10 分、EV-007 提前量 21→4 分）
#   参考  30（τ=18 分钟）：提前 2 / 晚 6 —— 与"按请求时长算"的旧行为一致，命中与提前量不变
# 所以取 30：既不牺牲验收指标，又保证"值与请求时长无关"。
# 代价：请求 120 分钟时后段更接近饱和（曲线偏平），属观感问题，不影响结论。
FORECAST_CANONICAL_HORIZON = 30
FORECAST_DAMPING_TAU_MIN = 15.0   # 趋势饱和时间常数（分钟）
FORECAST_PROFILES = {
    # 每个指标：物理范围 + 允许的最大偏移（None 表示按末值 25% 动态算）
    # power 的 max_drift 维持 10 dB。
    # 曾试过放宽到 15 dB（动机：故障爬坡期 4~10 分钟可跌 10~20 dB，10 dB 上限会截断外推，
    # 使"预计 N 分钟后越阈"报得偏晚，EV-005 实测滞后 6 分钟）。实测否决：
    #   AP-01 在 2026-01-01 09:00 仅 11/50 个频段短暂下探（远不到 40 门槛），
    #   drift=15 会把这段噪声放大成"预计 21 分钟后跌破 −95"的误报。
    # 结论：宁可快爬升故障滞后约 1~6 分钟，也不引入误报。
    "power":      {"bounds": (-140.0, -40.0), "max_drift": 10.0},
    "occupancy":  {"bounds": (0.0, 100.0),    "max_drift": 20.0},
    "sinr":       {"bounds": (-20.0, 40.0),   "max_drift": 6.0},
    "throughput": {"bounds": (0.0, 300.0),    "max_drift": None},
}

# 预警阈值：与 docs/api.md、频谱数据转交说明保持同一口径
ALERT_WEAK_POWER_DBM = -95.0        # 频段功率低于此值视为弱覆盖
# 频段占用率高于此值视为拥塞。
# 为什么从 85 调到 80：预警还要求"≥40/50 频段同时越阈"，而实测 4 个拥塞事件
# 的越阈频段数最多只有 39/37/37/39（差 1~3 个），导致拥塞预警**从未触发过**。
# 全 7 天回测（scripts/backtest_forecast.py）：
#   阈值 85% → 触发 0 段；阈值 80% → 触发 114 段，全部落在真值拥塞窗口内，0 误报。
# 依据：拥塞窗口占用率均值 75.6%~77.4%、最高 94.8%；正常窗口均值 37%~40%、最高 70.8%。
ALERT_CONGESTION_OCC_PCT = 80.0

# 越阈门槛的"比例/持续"部分，提到模块级以便「现状研判」与「预测预警」共用同一套判据。
# docs/fault_definition.md 第 2.2 节：≥80% 频段（即 40/50）同时越阈且连续 ≥3 分钟。
# 只按 5/50 判定时正常时段也会因边缘频段误报；只按单分钟触发时也出现过误报。
ALERT_MIN_CROSSING_RATIO = 0.8
ALERT_MIN_CROSSING_MINUTES = 3


def _min_crossing_bands(band_count: int) -> int:
    """越阈频段数门槛（与预警、研判共用）。"""
    return max(5, int(band_count * ALERT_MIN_CROSSING_RATIO))


def _linear_trend(values):
    """对等间隔序列做最小二乘（可含 None，自动跳过）。

    返回 (slope_per_step, intercept, residual_std, last_value)。
    有效点少于 5 个时 slope=0、residual_std=0，即退化为"末值持平"。
    """
    pts = [(i, float(v)) for i, v in enumerate(values) if v is not None]
    if not pts:
        return 0.0, 0.0, 0.0, None
    last_value = values[-1]
    last_value = float(last_value) if last_value is not None else pts[-1][1]
    if len(pts) < 5:
        return 0.0, last_value, 0.0, last_value

    n = len(pts)
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        return 0.0, last_value, 0.0, last_value

    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    resid = [p[1] - (intercept + slope * p[0]) for p in pts]
    resid_std = (sum(r * r for r in resid) / max(1, n - 2)) ** 0.5
    return slope, intercept, resid_std, last_value


def _damped_predict(last_value, slope_per_minute, minutes_ahead, max_drift, bounds):
    """阻尼外推：offset = slope × tau × (1 − e^(−k/tau))，夹到 ±max_drift 与物理范围。"""
    if last_value is None:
        return None
    drift = slope_per_minute * FORECAST_DAMPING_TAU_MIN * (
        1.0 - math.exp(-max(0.0, minutes_ahead) / FORECAST_DAMPING_TAU_MIN))
    drift = max(-max_drift, min(max_drift, drift))
    low, high = bounds
    return round(max(low, min(high, last_value + drift)), 2)


def _gated_slope(slope, resid_std, point_count):
    """趋势显著性门限：信噪比不足时把斜率归零。

    分钟级 KPI 抖动本身就大，30 个点拟合出的细小斜率很可能只是噪声；
    如果照它外推，会把"没有趋势"的时段也画成一条斜线。
    规则：整段趋势的累计变化量小于 0.8×残差标准差时，认为没有趋势。
    """
    if point_count < 5:
        return 0.0
    if abs(slope) * point_count < 0.8 * max(resid_std, 1e-6):
        return 0.0
    return slope


def _profile_drift(metric, last_value):
    """取该指标允许的最大偏移；None 表示按末值 25% 动态计算。"""
    profile = FORECAST_PROFILES[metric]
    if profile["max_drift"] is not None:
        return profile["max_drift"]
    return max(2.0, abs(last_value or 0.0) * 0.25)


@app.post("/api/forecast/preview", summary="预测预警 v2：预测预览（真实频谱数据）")
def forecast_preview(req: ForecastPreviewRequest):
    """真读 spectrum_7d.csv：按 baseTime 返回历史频谱帧 + 未来外推 + 风险预警。
    频谱数据范围 2026-01-01~01-07，超出范围自动降级 insufficient_data。"""
    try:
        base_dt = _parse_ts(req.baseTime)
    except (ValueError, TypeError):
        return error_response(ERROR_CODE["PARAM_INVALID"], "baseTime 格式应为 yyyy-MM-dd HH:mm:ss")
    if req.horizonMinutes <= 0 or req.horizonMinutes > 1440:
        return error_response(ERROR_CODE["PARAM_OUT_OF_RANGE"], "horizonMinutes 需在 1~1440 分钟之间")

    # 历史窗口必须整体落在基准之前（防未来数据混入）。
    # 原先没有这条校验：实测 baseTime=13:30 却传 history=13:35~14:35 时接口照常返回 ready，
    # 等于用"未来的观测"去预测"过去"，任何准确率结论都会失真。
    try:
        hist_start_dt, hist_end_dt = _parse_ts(req.history.start), _parse_ts(req.history.end)
    except (ValueError, TypeError):
        return error_response(ERROR_CODE["PARAM_INVALID"], "history 起止格式应为 yyyy-MM-dd HH:mm:ss")
    if hist_end_dt > base_dt:
        return error_response(
            ERROR_CODE["PARAM_INVALID"],
            f"history.end（{req.history.end}）不得晚于 baseTime（{req.baseTime}），否则会读到未来数据")
    if hist_start_dt >= hist_end_dt:
        return error_response(
            ERROR_CODE["PARAM_INVALID"],
            f"history.start（{req.history.start}）必须早于 history.end（{req.history.end}）")

    fid = "FC_" + base_dt.strftime("%Y%m%d%H%M%S")
    fore_end = base_dt + timedelta(minutes=req.horizonMinutes)

    # 频谱未加载 与 基准越界 是两种完全不同的原因，必须分开说明。
    # 原先合并成一句"当前时刻超出频谱数据范围（频谱覆盖 2026-01-01 ~ 01-07）"，
    # 实测在"频谱表缺失 + baseTime 完全合法（01-02 14:10）"时也这么报，
    # 把"文件没加载"误导成"时间选错了"（队友就因此排查了半天）。
    _spec_missing = _SPEC["df"] is None
    _spec_out_of_range = (not _spec_missing) and base_dt > _SPEC["max_ts"]
    if _spec_missing or _spec_out_of_range:
        if _spec_missing:
            _insufficient_msg = ("频谱数据未加载（缺少 data/spectrum_7d.csv 或加载失败），"
                                 "暂不提供频谱预测；故障研判等功能不受影响。")
        else:
            _cover = (f'{_SPEC["min_ts"]:%Y-%m-%d %H:%M:%S} ~ '
                      f'{_SPEC["max_ts"]:%Y-%m-%d %H:%M:%S}')
            _insufficient_msg = (f"基准时刻 {req.baseTime} 超出频谱数据覆盖范围（{_cover}），"
                                 f"暂不提供频谱预测。")
        result = {
            "id": fid, "scenario": req.scenario, "baseTime": req.baseTime,
            "history": {"start": req.history.start, "end": req.history.end},
            "forecast": {"start": req.baseTime, "end": fore_end.strftime("%Y-%m-%d %H:%M:%S")},
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "simulation", "status": "insufficient_data",
            "message": _insufficient_msg,
            "alerts": [], "spectra": [], "impacts": [],
        }
        _remember_forecast(fid, result)
        return success_response(result)

    hist_start = _parse_ts(req.history.start)
    hist_end = _parse_ts(req.history.end)
    bands = _SPEC["bands"]
    df = _SPEC["df"]

    def fmt(dt):
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    spectra = []
    alerts = []
    band_list = [{"id": b["band_id"], "label": f'{b["lower_mhz"]}-{b["upper_mhz"]}MHz',
                  "lowerMHz": int(b["lower_mhz"]), "upperMHz": int(b["upper_mhz"])} for b in bands]

    for ap in ["AP-01", "AP-02", "AP-03"]:
        # B-3：用加载时预分好的子表 + 二分索引，替代 df[df.observation_id == ap] 全表扫描
        ap_df = (_SPEC.get("by_ap") or {}).get(ap)
        if ap_df is None or ap_df.empty:
            continue
        hist_sub = spectrum_window(ap, hist_start, hist_end)
        # spectrum_window 在"窗口内没有任何频谱行"时返回 None。
        # 原先没有这个保护，选一个太早的基准（历史窗口落在数据集起点之前）会直接
        # AttributeError → 500。这里跳过该 AP；若所有 AP 都无数据，下面统一返回
        # insufficient_data，而不是抛异常。
        if hist_sub is None or hist_sub.empty:
            continue
        # 按分钟透视：ts -> {band_id: power}, {band_id: occ}
        hist_by_ts = {}
        for ts, grp in hist_sub.groupby("timestamp"):
            hist_by_ts[ts] = (dict(zip(grp.band_id, grp.power_dbm)),
                              dict(zip(grp.band_id, grp.occupancy_pct)))

        # 逐频段趋势：按分钟顺序整理该 AP 的历史序列（一次遍历，避免 50×2 次布尔筛选）
        ts_list = sorted(hist_by_ts.keys())
        series = {"power": {b["band_id"]: [] for b in bands},
                  "occupancy": {b["band_id"]: [] for b in bands}}
        for ts in ts_list:
            pmap, omap = hist_by_ts[ts]
            for b in bands:
                bid = b["band_id"]
                series["power"][bid].append(pmap.get(bid))
                series["occupancy"][bid].append(omap.get(bid))

        band_trend = {}
        for metric in ("power", "occupancy"):
            band_trend[metric] = {}
            for b in bands:
                bid = b["band_id"]
                window = series[metric][bid][-FORECAST_TREND_WINDOW:]
                slope, _intercept, resid_std, last = _linear_trend(window)
                slope = _gated_slope(slope, resid_std, len(window))
                band_trend[metric][bid] = (slope, last)   # 历史为每分钟一帧，斜率即"每分钟"

        def build_hist(metric):
            frames = []
            for ts in ts_list:
                pmap, omap = hist_by_ts[ts]
                mp = pmap if metric == "power" else omap
                frames.append({
                    "start": fmt(ts), "end": fmt(ts + timedelta(minutes=1)),
                    "values": [round(float(mp[b["band_id"]]), 2) if b["band_id"] in mp else None
                               for b in bands],
                })
            return frames

        def build_pred(metric):
            frames = []
            t = base_dt
            for step in range(int(req.horizonMinutes)):
                values = []
                # 按"场"推进而不是逐段独立外推：平滑 + 细节回填 + 结构放大，
                # 既压噪声、又保住频段间差异（否则未来帧只是整体平移）。
                _band_slopes = [band_trend[metric][b["band_id"]][0] or 0.0 for b in bands]
                _last_values = [band_trend[metric][b["band_id"]][1] or 0.0 for b in bands]
                slopes_field = _sf.field_from_slopes(_band_slopes,
                                                     [v if v else None for v in _last_values])
                if max((abs(x) for x in slopes_field), default=0.0) < _sf.ENERGY_EPS:
                    slopes_field = [0.0] * len(bands)
                # τ 与归一化都必须**与请求的预测时长无关**，否则同一个未来时刻会因
                # 用户切换"预测时长"而得到不同数值（实测 14:12 基准的 14:22：
                # horizon=30 给 -102.25、horizon=120 给 -100.83）。
                # 改法：用固定参考时长 FORECAST_CANONICAL_HORIZON 计算 τ 与归一化峰值，
                # 这样"未来 t 分钟的值"只由历史决定；请求时长只决定**截取到第几帧**。
                _tau = max(_sf.TAU_MINUTES, FORECAST_CANONICAL_HORIZON * 0.6)
                _total_factor = _tau * (1.0 - math.exp(-FORECAST_CANONICAL_HORIZON / _tau))
                # 归一化：若场外推到末端会整体撞上 max_drift 上限，所有频段会被夹到同一
                # 个值（实测末帧各频段变化跨度 = 0），形态又消失。这里按峰值等比缩放，
                # 让最大值落在上限的 80%，频段间差异得以保留。
                _known = [v for v in (band_trend[metric][b["band_id"]][1] for b in bands) if v is not None]
                _drift_cap = _profile_drift(metric, sum(_known) / len(_known)) if _known else 1e9
                _peak = max((abs(x) for x in slopes_field), default=0.0) * _total_factor
                _scale = min(1.0, (_drift_cap * 0.8) / _peak) if _peak > 1e-9 else 1.0
                for idx, b in enumerate(bands):
                    _slope, last = band_trend[metric][b["band_id"]]
                    if last is None:
                        values.append(None)
                        continue
                    factor = _tau * (1.0 - math.exp(-step / _tau))
                    value = last + slopes_field[idx] * factor * _scale
                    # 仍保留硬上限兜底（归一化后正常不会触发）
                    drift = _profile_drift(metric, last)
                    value = max(last - drift, min(last + drift, value))
                    lo_b, hi_b = FORECAST_PROFILES[metric]["bounds"]
                    values.append(round(max(lo_b, min(hi_b, value)), 2))
                frames.append({"start": fmt(t), "end": fmt(t + timedelta(minutes=1)),
                               "values": values})
                t += timedelta(minutes=1)
            return frames

        # 历史帧/预测帧各算一次，供 spectra 与 alerts 共用（避免重复计算）
        hist_frames = {"power": build_hist("power"), "occupancy": build_hist("occupancy")}
        pred_frames = {"power": build_pred("power"), "occupancy": build_pred("occupancy")}

        for metric, sid_suffix, unit, scale in [
            ("power", "power", "dBm", {"min": -110, "max": -40}),
            ("occupancy", "occupancy", "%", {"min": 0, "max": 100}),
        ]:
            spectra.append({
                "id": f"{ap}-{sid_suffix}", "observationLabel": ap, "observationId": ap,
                "metric": metric, "unit": unit, "scale": scale, "bands": band_list,
                "history": hist_frames[metric], "predicted": pred_frames[metric],
            })

        # ---- 预警判据：看"预测帧"里第几分钟越过阈值，而不是统计已发生的历史标签 ----
        # 阈值与 docs/api.md、频谱转交说明一致：功率 < −95 dBm 视为弱覆盖；占用率 > 80% 视为拥塞。
        # 输出分两种语义，与《问题状态与责任分工》里"持续风险"和"提前预警"分别验收一致：
        #   already=True  → 基准时刻已越阈，提示"已发生、预计持续"（持续风险）
        #   already=False → 基准时刻未越阈，提示"预计 N 分钟后…"（提前预警，带提前量）
        # 越阈频段数下限：要求"大多数频段"越阈，而不是几个边缘频段。
        # 起因：包络中心 −60 dBm、边缘 −70 dBm，边缘频段更容易先跌破阈值；
        # 实测只按 5/50 个频段判定时，正常时段也会因为边缘频段而误报。
        # 真实弱覆盖/拥塞是 50/50 全段越阈，渐变期则是"逐渐有更多频段越阈"，
        # 用 80% 作为门槛既能滤掉边缘噪声，又能让预测阶段给出提前量。
        min_crossing_bands = _min_crossing_bands(len(bands))
        # 持续性约束：必须连续 N 分钟越阈才算命中。
        # 起因：KPI 有阴影衰落，单分钟噪声就可能让若干频段瞬间跌破阈值，
        # 若只按"某一分钟越阈"触发，正常时段也会冒出误报（实测出现过 1 次）。
        min_crossing_minutes = ALERT_MIN_CROSSING_MINUTES

        def _bands_over(values, threshold, direction):
            """返回越阈频段列表；按越阈幅度从最严重排起。"""
            hits = [(v, b["band_id"]) for b, v in zip(bands, values)
                    if v is not None and (v < threshold if direction == "below" else v > threshold)]
            hits.sort(key=(lambda t: t[0]) if direction == "below" else (lambda t: -t[0]))
            return [bid for _v, bid in hits]

        for metric, threshold, direction, sid_suffix, label, verb in (
            ("power", ALERT_WEAK_POWER_DBM, "below", "power", "功率", "跌破"),
            ("occupancy", ALERT_CONGESTION_OCC_PCT, "above", "occupancy", "占用率", "超过"),
        ):
            frames = pred_frames[metric]
            if not frames:
                continue
            crossing = [_bands_over(f["values"], threshold, direction) for f in frames]
            hit_minutes = [i for i, ids in enumerate(crossing) if len(ids) >= min_crossing_bands]

            # 找出"连续 ≥ min_crossing_minutes 分钟"的段落，取第一段
            run_start = None
            run = []
            for i in hit_minutes:
                if run and i == run[-1] + 1:
                    run.append(i)
                else:
                    if len(run) >= min_crossing_minutes:
                        run_start = run[0]
                        break
                    run = [i]
            if run_start is None:
                if len(run) >= min_crossing_minutes:
                    run_start = run[0]
                else:
                    continue          # 没有够长的连续段 → 视为噪声，不告警
            hit_minutes = [i for i in hit_minutes if i >= run_start]
            if not hit_minutes:
                continue

            first_minute = hit_minutes[0]
            last_minute = hit_minutes[-1]
            hardest = crossing[first_minute]
            ratio = len(hardest) / max(1, len(bands))
            already = bool(hist_frames[metric]) and len(
                _bands_over(hist_frames[metric][-1]["values"], threshold, direction)
            ) >= min_crossing_bands

            if already:
                severity = "high" if ratio >= 0.5 else "medium"
                unit_text = "dBm" if metric == "power" else "%"
                if last_minute + 1 >= len(frames):
                    duration_text = f"预测整个 {int(req.horizonMinutes)} 分钟内该状态维持"
                else:
                    duration_text = (f"预计持续到第 {last_minute + 1} 分钟"
                                     f"（{frames[last_minute]['end'][11:16]}）后逐步恢复")
                title = f"{ap} 已处于{'弱覆盖' if metric == 'power' else '拥塞'}，预计持续"
                summary = (
                    f"基准时刻（{req.baseTime}）已有 {len(hardest)}/{len(bands)} 个频段"
                    f"{label}{verb} {threshold}{unit_text}；按当前趋势，{duration_text}。"
                )
            else:
                severity = "high" if first_minute <= 10 else ("medium" if first_minute <= 30 else "low")
                unit_text = "dBm" if metric == "power" else "%"
                summary = (
                    f"基准时刻（{req.baseTime}）尚未越阈；按当前趋势外推，"
                    f"预计第 {first_minute} 分钟后开始有 {len(hardest)}/{len(bands)} 个频段"
                    f"{label}{verb} {threshold}{unit_text}。"
                    # 措辞校准：这是"趋势延续"判断，不是保证。实测 01-02 AP-03 弱覆盖
                    # 在故障注入前的历史窗口内毫无前兆，任何外推都无法提前预知，
                    # 所以不写"提前预警"这类绝对表述。
                    f"（趋势推算值，非确定预测；若后续趋势变化，越阈时刻会随之变动）"
                )
                title = f"{ap} 预计 {first_minute} 分钟后{label}{verb} {threshold}{unit_text}"

            span_start = base_dt + timedelta(minutes=first_minute)
            span_end = min(fore_end, base_dt + timedelta(minutes=last_minute + 1))
            if span_end <= span_start:      # 单分钟命中：至少给 1 分钟窗口
                span_end = min(fore_end, span_start + timedelta(minutes=1))

            alerts.append({
                "id": f"ALT-{ap}-{'weak' if metric == 'power' else 'cong'}",
                "target": ap,
                "title": title,
                "summary": summary,
                "severity": severity,
                "start": fmt(span_start), "end": fmt(span_end),
                "spectrumId": f"{ap}-{sid_suffix}", "impactId": None,
                "bandIds": hardest[:5],
            })

    # impacts：SINR/吞吐从主网络数据按分钟聚合，用同一套阻尼趋势外推
    impacts = []
    try:
        rows = filter_rows(req.scenario, req.history.start, req.history.end)
        for key, label, unit, sid in [("sinr", "SINR", "dB", "imp-sinr"),
                                       ("throughput", "吞吐量", "Mbps", "imp-thr")]:
            by_ts = {}
            for r in rows:
                v = r.get(key)
                if v not in (None, ""):
                    by_ts.setdefault(r["timestamp"], []).append(float(v))
            raw_pts = [{"time": ts, "value": round(sum(vs) / len(vs), 2)}
                       for ts, vs in sorted(by_ts.items())]
            if not raw_pts:
                continue  # 网络表无此指标数据，跳过，不填0值预测
            # 点太多时均匀降采样（原实现同口径），但必须记住步长，否则斜率不是"每分钟"
            stride = max(1, len(raw_pts) // 60)
            hist_pts = raw_pts[::stride]
            step_minutes = float(stride)
            if not hist_pts:
                continue

            last_val = hist_pts[-1]["value"]
            window_points = max(4, int(round(FORECAST_TREND_WINDOW / step_minutes)))
            window_values = [p["value"] for p in hist_pts[-window_points:]]
            slope_per_point, _intercept, resid_std, _last = _linear_trend(window_values)
            slope_per_point = _gated_slope(slope_per_point, resid_std, len(window_values))
            slope_per_minute = slope_per_point / step_minutes

            profile = FORECAST_PROFILES[key]
            max_drift = _profile_drift(key, last_val)
            # 经验区间：以拟合残差为基准（约 1.96σ≈95%），但设相对上限。
            # 只用残差会把"故障窗口内状态来回切换"造成的巨大方差也算进去，
            # 得到 0~75 Mbps 这种没有信息量的区间；因此再按末值 25% 封顶。
            half_width = max(1.96 * resid_std, abs(last_val) * 0.02, 0.3)
            half_width = min(half_width, max(2.0, abs(last_val) * 0.25))
            low, high = profile["bounds"]

            pred_pts = []
            t = base_dt
            total_steps = max(1, int(req.horizonMinutes // 5))
            for step in range(total_steps):
                minutes_ahead = step * 5          # 预测点每 5 分钟一个，趋势按分钟拟合
                value = _damped_predict(last_val, slope_per_minute, minutes_ahead,
                                        max_drift, profile["bounds"])
                # 区间随预测步长扩大：原来 half_width 在循环外只算一次，
                # 每个预测点的带宽完全相同，图上是等宽平带——但越往后越不确定，
                # 等宽区间在统计上说不通（也是"预测看起来不对"的直接原因之一）。
                # 用 √(1 + step/total) 增长：起步约 1 倍，末端约 1.41 倍。
                growth = (1.0 + step / total_steps) ** 0.5
                pred_pts.append({
                    "time": fmt(t), "value": value,
                    "lower": round(max(low, value - half_width * growth), 2),
                    "upper": round(min(high, value + half_width * growth), 2),
                })
                t += timedelta(minutes=5)
            impacts.append({"id": sid, "targetLabel": "全网", "label": label, "unit": unit,
                            "history": hist_pts, "predicted": pred_pts})
    except Exception:
        pass

    # 所有 AP 在历史窗口内都没有频谱行（例如基准太早，历史窗口落在数据集起点之前）
    # → 明确返回"数据不足"，而不是给一张空图或抛异常。
    if not spectra:
        empty = {
            "id": fid, "scenario": req.scenario, "baseTime": req.baseTime,
            "history": {"start": req.history.start, "end": req.history.end},
            "forecast": {"start": req.baseTime, "end": fmt(fore_end)},
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "simulation", "status": "insufficient_data",
            "message": (f"参考历史窗口（{req.history.start} ~ {req.history.end}）内没有频谱数据；"
                        f"频谱表覆盖至 {_SPEC['max_ts']:%Y-%m-%d %H:%M}，请把基准时刻调到该区间内。"),
            "alerts": [], "spectra": [], "impacts": [],
        }
        _remember_forecast(fid, empty)
        return success_response(empty)

    views_ap = alerts[0]["target"] if alerts else "AP-01"
    result = {
        "id": fid, "scenario": req.scenario, "baseTime": req.baseTime,
        "history": {"start": req.history.start, "end": req.history.end},
        "forecast": {"start": req.baseTime, "end": fmt(fore_end)},
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "simulation", "status": "ready",
        "message": "预测窗口内发现需要关注的风险" if alerts else "预测窗口内整体正常",
        "alerts": alerts, "spectra": spectra, "impacts": impacts,
        # 预测结果的自然语言说明（阶段2）：LLM 可用时由大模型改写，否则用模板。
        # 模板只引用本响应里已有的数值，保证"没 key 也有话可说"且不编造。
        "narrative": explain_forecast({"base_time": req.baseTime, "alerts": alerts,
                                       "impacts": impacts}),
        "narrative_source": "llm" if ENABLE_LLM else "template",
        "spectrumViews": {
            "powerId": f"{views_ap}-power", "occupancyId": f"{views_ap}-occupancy",
            "recommendedView": "occupancy" if alerts else "curve",
            "reason": "存在拥塞/覆盖风险，优先展示对应频谱视图",
        },
    }
    _remember_forecast(fid, result)
    return success_response(result)


@app.post("/api/forecast/analyze", summary="预测预警 v2：AI 文字研判")
def forecast_analyze(req: ForecastAnalyzeRequest):
    """按 forecastId + alertId 对一轮预测做文字研判。
    优先复用 preview 缓存中的预警详情；缓存缺失时用历史 KPI 趋势兜底组 prompt。
    LLM 不可用时降级 source=rule 模板文案，接口不崩。"""
    cached = _FORECAST_CACHE.get(req.forecastId, {})
    alert_detail = None
    if req.alertId:
        for a in cached.get("alerts", []):
            if a.get("id") == req.alertId:
                alert_detail = a
                break

    # 用户的问题与关注时段（v3）：只在**有值**时参与，未传时输出与旧行为逐字节一致。
    question_text = (req.question or "").strip()
    target_window = req.targetWindow
    # 未指定 alertId 但给了关注时段时，挑与该时段相交的预警来研判（否则整窗）
    if target_window is not None and alert_detail is None:
        for a in cached.get("alerts", []):
            if (a.get("start") or "") <= target_window.end and (a.get("end") or "") >= target_window.start:
                alert_detail = a
                break

    # 拉历史窗口真实数据，算 KPI 现状与近窗口趋势（供 prompt 使用）
    try:
        hist_rows = filter_rows(req.scenario, req.history.start, req.history.end)
    except Exception:
        hist_rows = []

    def _avg(rows, key):
        vals = [float(r.get(key)) for r in rows if r.get(key) not in (None, "")]
        return sum(vals) / len(vals) if vals else None

    avg_rsrp = _avg(hist_rows, "rsrp")
    avg_sinr = _avg(hist_rows, "sinr")
    avg_thr = _avg(hist_rows, "throughput")
    n_rows = len(hist_rows)
    # 无数据窗口：均值为 None，统一转成"暂缺"文本，避免 f-string 格式化 None 崩溃
    rsrp_text = f"{avg_rsrp:.1f}" if avg_rsrp is not None else "暂缺"
    sinr_text = f"{avg_sinr:.1f}" if avg_sinr is not None else "暂缺"
    thr_text = f"{avg_thr:.1f}" if avg_thr is not None else "暂缺"
    # 趋势：窗口后半段 vs 前半段平均 SINR
    trend_text = "历史趋势暂缺"
    if n_rows >= 10:
        half = n_rows // 2
        first_sinr = _avg(hist_rows[:half], "sinr")
        second_sinr = _avg(hist_rows[half:], "sinr")
        if first_sinr is not None and second_sinr is not None:
            delta = second_sinr - first_sinr
            direction = "恶化（下降）" if delta < -1 else ("改善（上升）" if delta > 1 else "基本平稳")
            trend_text = f"窗口后半段 SINR 较前半段{direction}，变化约 {delta:+.1f} dB"

    alert_desc = (
        f"预警标题：{alert_detail.get('title')}；摘要：{alert_detail.get('summary')}；"
        f"等级：{alert_detail.get('severity')}；时段：{alert_detail.get('start')} ~ {alert_detail.get('end')}"
        if alert_detail else f"预警ID：{req.alertId or '未指定'}"
    )

    system_prompt = (
        "你是校园无线网络监测平台的资深运维研判助手。针对一条预测预警，用中文写一段 250~400 字的专业研判，"
        "面向网络运维人员。必须按顺序覆盖：①风险概述（发生了什么、大约何时、影响哪些用户/区域）；"
        "②可能原因（结合给出的历史数据趋势推测，不要凭空编造）；③支持证据与反面疑点；"
        "④影响范围；⑤预防/处置方案与预期效果；⑥不确定性说明（这条预测的可信边界）。"
        "语气克制专业，不喊口号，不要编造未给出的数值。"
    )
    # 频谱维度的事实数字（唐苗检测报告 R08：原先只给 RSRP/SINR/吞吐均值，
    # 没有把"逐频段越阈情况"交给 AI，导致 AI 只能泛泛而谈）。
    # 只在能取到数据时追加，取不到时 prompt 与旧版一致。
    _spectrum_ctx = ""
    try:
        _ap = (alert_detail or {}).get("target") or ""
        if _ap and _SPEC.get("df") is not None:
            _part = spectrum_window(_ap, req.history.start, req.history.end, inclusive_end=True)
            if _part is not None and not _part.empty:
                _last_ts = _part["timestamp"].max()
                _last = _part[_part["timestamp"] == _last_ts]
                _n = len(_last)
                _low = int((_last["power_dbm"] < ALERT_WEAK_POWER_DBM).sum())
                _occ = int((_last["occupancy_pct"] > ALERT_CONGESTION_OCC_PCT).sum())
                _imin = _last["power_dbm"].idxmin()
                _spectrum_ctx = (
                    f"频谱维度（{_ap}，历史窗口最后一帧 {_last_ts}）：共 {_n} 个频段，"
                    f"功率低于 {ALERT_WEAK_POWER_DBM} dBm 的有 {_low}/{_n} 个，"
                    f"占用率高于 {ALERT_CONGESTION_OCC_PCT}% 的有 {_occ}/{_n} 个，"
                    f"最低功率 {float(_last.loc[_imin, 'power_dbm']):.1f} dBm "
                    f"@ 频段 {_last.loc[_imin, 'band_id']}。")
    except Exception:
        _spectrum_ctx = ""

    # 仅在用户确实给了问题/关注时段时才追加，未传时 user_message 与旧版逐字节一致，
    # 因此不会影响既有调用方的输出（这是"改提示词有风险"的规避方式）。
    extra_ctx = ""
    if question_text or target_window is not None:
        lines = []
        if question_text:
            lines.append(f"用户的问题：{question_text}")
        if target_window is not None:
            lines.append(f"用户关注时段：{target_window.start} ~ {target_window.end}")
        lines.append("请优先直接回应用户的问题与关注时段；与该问题无关的通用建议可以压缩。")
        extra_ctx = "\n" + "\n".join(lines) + "\n"
    if _spectrum_ctx:
        extra_ctx = extra_ctx + _spectrum_ctx + "\n"

    user_message = (
        f"场景：{req.scenario}\n预测基准时刻：{req.baseTime}\n"
        f"预测窗口：{req.forecast.start} ~ {req.forecast.end}\n"
        f"历史观测窗口：{req.history.start} ~ {req.history.end}（共 {n_rows} 条采样）\n"
        f"历史均值：RSRP={rsrp_text} dBm，SINR={sinr_text} dB，吞吐量={thr_text} Mbps\n"
        f"{trend_text}\n{alert_desc}\n{extra_ctx}请按系统要求输出研判文字。"
    )

    reply = None
    source = "rule"
    try:
        if llm_agent.LLM_API_KEY:
            reply = call_llm_chat(system_prompt, user_message, temperature=0.3)
            if reply:
                source = "llm"
    except Exception:
        reply = None

    if not reply:
        # 无告警 ≠ 已检测到风险：cached.alerts 为空时要如实说"未发现越阈风险"，
        # 否则会出现"没有风险却在说检测到风险"的自相矛盾（预览页 0 项需关注时必现）。
        cached_alerts = cached.get("alerts") or []
        if not cached_alerts:
            head = (f"【规则研判】预测窗口 {req.forecast.start} ~ {req.forecast.end} 内"
                    f"未检测到越阈风险（预警阈值为经验设定，未触发不代表绝对安全）。")
            advice = ("建议：维持当前配置，按常规巡检节奏关注窗口后半段的 SINR 变化；"
                      "如需提前量，可把预测时长拉长再看一次趋势。")
        else:
            head = (f"【规则研判】预测窗口 {req.forecast.start} ~ {req.forecast.end} 内检测到需要关注的风险"
                    f"（{alert_detail.get('title', '见预警摘要') if alert_detail else '预警事件'}）。")
            advice = ("建议：提前核查相关 AP 负载与邻区干扰，关注受影响 UE 的接入与切换状态；"
                      "若窗口后半段 SINR 持续走低，考虑均衡邻区负载或扩容。")
        reply = (
            head
            + f"历史窗口 RSRP={rsrp_text} dBm、SINR={sinr_text} dB，{trend_text}。"
            + advice
            + "不确定性：当前 LLM 研判不可用，以上为基于历史均值的规则提示，未结合频谱细节。"
        )
        # 规则兜底也要回应用户的问题与关注时段，否则"问了等于没问"。
        if target_window is not None:
            reply += f"（已按关注时段 {target_window.start} ~ {target_window.end} 收敛分析范围。）"
        if question_text:
            reply += f"关于你的问题「{question_text}」：上面已给出该时段的风险判断与处置建议；" \
                     "若涉及具体业务类型，可结合第③项证据判断影响程度。"

    return success_response({
        "forecastId": req.forecastId,
        "baseTime": req.baseTime,
        "alertId": req.alertId,
        "source": source,
        "reply": reply,
        # 回显用户输入，便于前端确认"它确实读到了这句话/这个时段"
        "question": question_text,
        "targetWindow": ({"start": target_window.start, "end": target_window.end}
                         if target_window is not None else None),
    })


# ============================================================================
# 接口9：拓扑增强数据（GET /api/network/topology/enhanced，契约 v1 · 真实实现）
#   契约：docs/api.md 接口9。指定时刻的 AP/UE 节点 + 链路五指标 + 整体统计。
#   过滤逻辑与接口6（/api/network/topology/basic）同口径，复用 filter_rows。
# ============================================================================
def _topology_active_users(ap_rows):
    """AP 活跃用户数：取数据源 active_users 列（契约示例值 11 与该列口径一致）；
    该列缺失或非法时退化为按 user_id 去重计数。"""
    values = []
    for r in ap_rows:
        raw = r.get("active_users")
        if raw in (None, ""):
            continue
        try:
            values.append(int(float(raw)))
        except (TypeError, ValueError):
            continue
    if values:
        return max(values)
    return len({r["user_id"] for r in ap_rows})


@app.get("/api/network/topology/enhanced", summary="接口9：拓扑增强数据（契约 v1）")
def get_enhanced_topology(
    scenario: str = Query(..., description="场景名"),
    time: str | None = Query(None, description="指定时刻，默认取数据集最新时刻"),
    include_metrics: bool = Query(True, description="是否附加链路指标"),
):
    # 1. 参数校验：场景白名单（与 VALID_SCENARIOS 一致）+ 时间格式
    if scenario not in VALID_SCENARIOS:
        return error_response(ERROR_CODE["PARAM_INVALID"],
                              "scenario仅支持" + "/".join(VALID_SCENARIOS))
    # 回放口径：time 必填，禁止静默取数据集最新端点（max=2026-12-31 会未卜先知露馅）
    if not time:
        return error_response(ERROR_CODE["PARAM_MISSING"],
                              "参数缺失：time 必填（禁止静默取数据集端点，回放模式会露馅）")
    if time:
        try:
            datetime.strptime(time, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return error_response(ERROR_CODE["PARAM_INVALID"], "时间格式应为yyyy-MM-dd HH:mm:ss")

    # 2. 复用公共过滤函数取该场景全量数据（与接口6同口径）
    rows = filter_rows(scenario, "2026-01-01 00:00:00", "2026-12-31 23:59:59")
    if not rows:
        # 场景整体无数据：与“指定时刻无数据”分支保持同一 stats 结构（五项全 0），
        # 避免前端面对 {} 与五项两种结构（第三轮评审 6.1）
        return success_response({
            "timestamp": None, "nodes": [], "links": [],
            "stats": {"ap_count": 0, "ue_count": 0, "link_count": 0,
                      "avg_rsrp": 0.0, "avg_sinr": 0.0},
        })

    # 3. 定位时刻：time 已由参数校验强制必填，直接按指定时刻过滤
    rows = [r for r in rows if r["timestamp"] == time]

    # 4. 边界：该时刻无数据 → 返回空图，接口不崩
    if not rows:
        return success_response({
            "timestamp": time, "nodes": [], "links": [],
            "stats": {"ap_count": 0, "ue_count": 0, "link_count": 0,
                      "avg_rsrp": 0.0, "avg_sinr": 0.0},
        })

    # 5. 节点：AP 聚合属性 + UE 当前时刻指标
    ap_rows_map = {}
    for r in rows:
        ap_rows_map.setdefault(r["ap_id"], []).append(r)

    nodes = []
    for ap_id in sorted(ap_rows_map):
        ap_rows = ap_rows_map[ap_id]
        nodes.append({
            "id": ap_id,
            "type": "ap",
            "x": float(ap_rows[0]["ap_x"]),
            "y": float(ap_rows[0]["ap_y"]),
            "properties": {
                "rsrp_avg": round(avg(ap_rows, "rsrp"), 2),
                "prb_usage": round(avg(ap_rows, "prb_usage"), 2),
                "active_users": _topology_active_users(ap_rows),
            },
        })

    # 6. 链路：一条 AP-UE 链路；include_metrics=false 时不生成 metrics 键
    # 6.3 加固：按 (source,target) 对去重（与接口6 同口径），同一 AP-UE 对只保留首条，
    #      避免生成器或历史数据出现重复行时链路数与 link_count 统计膨胀
    links = []
    link_seen = set()
    ue_seen = set()
    for r in rows:
        if r["user_id"] not in ue_seen:
            ue_seen.add(r["user_id"])
            nodes.append({
                "id": r["user_id"],
                "type": "ue",
                "x": float(r["ue_x"]),
                "y": float(r["ue_y"]),
                "properties": {
                    "rsrp": float(r["rsrp"]),
                    "sinr": float(r["sinr"]),
                    "throughput": float(r["throughput"]),
                },
            })
        link_key = (r["ap_id"], r["user_id"])
        if link_key in link_seen:
            continue
        link_seen.add(link_key)
        link = {"source": r["ap_id"], "target": r["user_id"]}
        if include_metrics:
            link["metrics"] = {
                "rsrp": float(r["rsrp"]),
                "sinr": float(r["sinr"]),
                "throughput": float(r["throughput"]),
                "latency": float(r["latency"]),
                "packet_loss": float(r["packet_loss"]),
            }
        links.append(link)

    # 7. 整体统计
    return success_response({
        "timestamp": time,
        "nodes": nodes,
        "links": links,
        "stats": {
            "ap_count": len(ap_rows_map),
            "ue_count": len(ue_seen),
            "link_count": len(links),
            "avg_rsrp": round(avg(rows, "rsrp"), 2),
            "avg_sinr": round(avg(rows, "sinr"), 2),
        },
    })


