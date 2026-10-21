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
import re
import math
import os
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
from realtime_simulator import start_realtime_service, get_incremental_data, DATA_COLUMNS
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://10.63.118.174:5173"],
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
    _SPEC["df"] = df
    _SPEC["max_ts"] = df["timestamp"].max()
    bands = (df[["band_id", "lower_mhz", "upper_mhz"]].drop_duplicates()
             .sort_values("lower_mhz").to_dict("records"))
    _SPEC["bands"] = bands
    print(f"[OK] {SPECTRUM_PATH.name} 加载完成：{len(df)} 行，时间至 {_SPEC['max_ts']}, {len(bands)} 频段")


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
    """按场景/时间/AP/用户过滤 CSV 数据，按时间升序返回"""
    rows = []
    for r in DF:
        if r["scenario"] != scenario:
            continue
        if ap_id and r["ap_id"] != ap_id:
            continue
        if user_id and r["user_id"] != user_id:
            continue
        if not (start_time <= r["timestamp"] <= end_time):
            continue
        rows.append(r)
    rows.sort(key=lambda x: x["timestamp"])
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

    return {
        "code": 0,
        "message": "success",
        "data": {
            "total": total,  # 修复：返回符合条件的总记录数
            "columns": columns,
            "records": records,  # 当前页数据
        },
    }


# ============ 接口2：多KPI联合故障研判 ============
class AnomalyRequest(BaseModel):
    scenario: str
    window_start: str
    window_end: str
    metrics: list[str] = ["rsrp", "sinr", "throughput"]

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

def judge_ap(ap, ap_rows):
    """对单个AP做多KPI联合研判（通信规则 + 状态标签占比），返回研判结果dict"""
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
    if weak_ratio > 0.3 or (rsrp_avg < -105 and sinr_avg < 5):
        level = "严重" if weak_ratio > 0.5 else "中等"
        return {**base, "anomaly_type": "覆盖故障", "level": level, "network_status": "weak_coverage",
                "cause": f"{ap}弱覆盖采样占比{weak_ratio*100:.0f}%、RSRP均值{rsrp_avg:.1f}dBm，判定为覆盖空洞",
                "suggestion": "建议检查AP天线倾角/功率或增加补盲AP",
                "candidates": build_candidates(ap, base, "覆盖故障")}
    if cong_ratio > 0.25 or prb_avg > 90:
        level = "严重" if cong_ratio > 0.5 else "中等"
        return {**base, "anomaly_type": "网络拥塞", "level": level, "network_status": "congestion",
                "cause": f"{ap}拥塞采样占比{cong_ratio*100:.0f}%、PRB利用率均值{prb_avg:.1f}%，判定为容量拥塞",
                "suggestion": "建议负载均衡或扩容",
                "candidates": build_candidates(ap, base, "网络拥塞")}
    if thr_avg < 50:
        return {**base, "anomaly_type": "边缘弱用户", "level": "中等", "network_status": "weak_coverage",
                "cause": f"{ap}吞吐量均值{thr_avg:.1f}Mbps偏低、SINR均值{sinr_avg:.1f}dB，判定为边缘弱用户",
                "suggestion": "建议用户靠近信号源或调整波束",
                "candidates": build_candidates(ap, base, "边缘弱用户")}
    return {**base, "anomaly_type": "正常", "level": "低", "network_status": "normal",
            "cause": f"{ap}各项指标均在正常范围", "suggestion": "无需处理",
            "candidates": build_candidates(ap, base, "正常")}

@app.post("/api/analyze/anomaly")
def analyze_anomaly(req: AnomalyRequest):
    # 第2周深化版：CSV真实数据，按AP分组多KPI联合研判（可解释证据链）
    # 新增：hourly_trend 小时趋势 / ap_comparison 多AP对比 / alarm_history 告警历史
    rows = filter_rows(req.scenario, req.window_start, req.window_end)
    if not rows:
        return {"code": 0, "message": "success",
                "data": {"is_anomaly": False, "reason": "该时间窗口无数据",
                         "cause": "该时间窗口无数据",
                         "hourly_trend": [], "ap_comparison": [], "alarm_history": []}}

    sev_rank = {"严重": 0, "中等": 1, "低": 9}

    # 1) 按 AP 分组研判 → 主结果
    by_ap = {}
    for r in rows:
        by_ap.setdefault(r["ap_id"], []).append(r)
    ap_results = [judge_ap(ap, ap_rows) for ap, ap_rows in sorted(by_ap.items())]
    findings = [res for res in ap_results if res["anomaly_type"] != "正常"]

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
        hour_findings = [x for x in hour_res if x["anomaly_type"] != "正常"]
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
        evidence, cause, suggestion = m["evidence"], m["cause"], m["suggestion"]
        nstatus, affected_aps = m["network_status"], [f["ap_id"] for f in findings]
        candidates = m.get("candidates", [])
        ap_candidates = [{"ap_id": f["ap_id"], "candidates": f["candidates"]} for f in findings]
        confidence = round(min(0.99, 0.6 + 0.08 * len(evidence) + (0.1 if len(findings) > 1 else 0)), 2)
    elif hour_findings_all:
        # 整窗均值被正常时段稀释判"正常"，但存在时段性异常（小时级）
        # → 主结果升级为最严重的小时异常，避免"有告警却说正常"的矛盾
        hour_findings_all.sort(key=lambda f: sev_rank.get(f["level"], 9))
        m = hour_findings_all[0]
        is_anomaly, anomaly_type, level = True, m["anomaly_type"], m["level"]
        evidence, cause, suggestion = m["evidence"], m["cause"], m["suggestion"]
        nstatus, affected_aps = m["network_status"], [m["ap_id"]]
        candidates = m.get("candidates", [])
        ap_candidates = [{"ap_id": f["ap_id"], "candidates": f["candidates"]} for f in hour_findings_all]
        confidence = round(min(0.99, 0.6 + 0.08 * len(evidence) + 0.1), 2)
    else:
        rsrp_avg = avg(rows, "rsrp"); sinr_avg = avg(rows, "sinr")
        thr_avg = avg(rows, "throughput"); prb_avg = avg(rows, "prb_usage")
        is_anomaly, anomaly_type, level = False, "正常", "低"
        evidence = []
        cause = (f"各AP的RSRP均值{rsrp_avg:.1f}dBm、SINR均值{sinr_avg:.1f}dB、PRB利用率{prb_avg:.1f}%、"
                 f"吞吐量均值{thr_avg:.1f}Mbps，各项指标均在正常范围")
        suggestion, nstatus, affected_aps = "无需处理", "normal", []
        candidates = []
        ap_candidates = []
        confidence = 0.95

    # 3) 多 AP 横向对比（哪台AP最差）
    ap_comparison = [
        {k: res[k] for k in ("ap_id", "rsrp_avg", "sinr_avg", "thr_avg", "prb_avg",
                             "weak_ratio", "cong_ratio", "anomaly_type", "level")}
        for res in ap_results
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

    return {
        "code": 0,
        "message": "success",
        "data": {
            "is_anomaly": is_anomaly,
            "anomaly_type": anomaly_type,
            "level": level,
            "confidence": confidence,
            "kpi_evidence": evidence,
            "cause": cause,
            "suggestion": suggestion,
            "affected_aps": affected_aps,
            "network_status": nstatus,
            "candidates": candidates,
            "ap_candidates": ap_candidates,
            "hourly_trend": hourly_trend,        # 新增：小时级趋势
            "ap_comparison": ap_comparison,      # 新增：多AP横向对比
            "alarm_history": alarm_history,      # 新增：告警历史
            "plans": plans,                      # 新增：处理方案 + 对比仿真（闭环"方案制定"）
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

VALID_SCENARIOS = ("校园", "商场", "体育馆")

# 对话工具默认窗口（前端未传时使用）；接口2/热力图/拓扑接口的默认口径不变
TOOL_WINDOW_START = "2026-01-01 00:00:00"
TOOL_WINDOW_END = "2026-01-03 23:59:59"

def _default_window(scenario: str):
    """AI 工具默认窗口（方案B）：跟随场景数据最新时刻往前推 24 小时。
    回放/普通场景漏传时间时，默认值永远贴近当前，避免拿到写死的 01-01~01-03 与页面口径不一致。"""
    rows = filter_rows(scenario, "2026-01-01 00:00:00", "2026-12-31 23:59:59")
    if not rows:
        return TOOL_WINDOW_START, TOOL_WINDOW_END
    end = max(r["timestamp"] for r in rows)
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
def tool_query_kpi(scenario: str, win_start: str | None = None, win_end: str | None = None) -> dict:
    win_start, win_end = win_start or TOOL_WINDOW_START, win_end or TOOL_WINDOW_END
    rows = filter_rows(scenario, win_start, win_end)
    if rows:
        reply = (f"截至{win_end}，当前时段网络整体运行平稳：RSRP均值{avg(rows,'rsrp'):.1f}dBm，"
                 f"SINR均值{avg(rows,'sinr'):.1f}dB，平均吞吐量{avg(rows,'throughput'):.1f}Mbps，"
                 f"PRB利用率{avg(rows,'prb_usage'):.1f}%，共{len(rows)}条采样。")
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
        f"候选{c.get('rank')}：{c.get('cause')}（置信度{c.get('confidence', '-')}）" for c in cand
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
        import json as _json
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


def tool_fault_diagnosis(scenario: str, win_start: str | None = None, win_end: str | None = None) -> dict:
    win_start, win_end = win_start or TOOL_WINDOW_START, win_end or TOOL_WINDOW_END
    an = analyze_anomaly(AnomalyRequest(scenario=scenario,
                                        window_start=win_start, window_end=win_end))
    d = an["data"]
    reply = _build_fault_reply(d, scenario, win_start, win_end)
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
            "query_ref": {"scenario": scenario,
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
TOOLS = {
    "query_kpi": tool_query_kpi,
    "fault_diagnosis": tool_fault_diagnosis,
    "get_heatmap": tool_heatmap,
    "get_topology": tool_topology,
    "forecast": tool_forecast,
}


# 说明：ENABLE_LLM 已上移到文件顶部配置区（那里定义了环境变量 NETTUNE_ENABLE_LLM 开关）。


@app.post("/api/agent/chat")
def agent_chat(req: ChatRequest):
    # 1. 第一层：统一关键词规则（llm_agent.rule_intent，独立主路径，完全不依赖LLM）
    rule = rule_intent(req.message)
    intent = rule.get("intent", "query_kpi")
    params = rule.get("params", {})
    hit = rule.get("hit", False)

    # 2. 第二层：关键词未命中 + 启用LLM时，才调用大模型增强识别（get_intent 内部自带降级）
    if not hit and ENABLE_LLM:
        try:
            intent_obj = get_intent(req.message)
            intent = intent_obj.get("intent", intent)
            params = {**params, **intent_obj.get("params", {})}
        except Exception:
            # get_intent 已内部降级到 rule_intent，此处仅做最终兜底，不抛出错误
            pass

    # 3. 第三层：最终兜底，任何非法/未知意图全部回退KPI查询
    if intent not in TOOLS:
        intent = "query_kpi"

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
    handler = TOOLS.get(intent, TOOLS["query_kpi"])
    if intent == "forecast":
        data = handler(scenario, req.window_start, req.window_end, req.message)
    else:
        data = handler(scenario, req.window_start, req.window_end)
    return {"code": 0, "message": "success", "data": data}


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

    now = datetime.now()
    report_id = "RPT_" + now.strftime("%Y%m%d_%H%M%S")
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
        "hourly_trend": diagnosis.get("hourly_trend") or [],
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

#新增实时数据接口
@app.get("/api/data/realtime", summary="获取实时增量监测数据")
def api_get_realtime_data(last_time: str = None):
    """
    获取分钟级增量监测数据
    - last_time: 上次拉取的最大时间戳，格式 yyyy-MM-dd HH:mm:ss，不传则返回最近10条
    """
    # 参数格式校验
    if last_time is not None:
        try:
            datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return {
                "code": 40002,
                "message": "时间参数格式错误，正确格式：yyyy-MM-dd HH:mm:ss",
                "data": None
            }

    records = get_incremental_data(last_time)
    return {
        "code": 0,
        "message": "success",
        "data": {
            "total": len(records),
            "columns": DATA_COLUMNS,
            "records": records
        }
    }


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


def _parse_ts(value: str):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


# ============ 预测外推：阻尼趋势（替代原来的"均值±1 方波"） ============
# 原实现：预测帧 = 历史末 10 分钟各频段均值 ±1 交替；指标预测 = 末值恒定 → 方波与水平线。
# 直接改成线性外推同样不行：故障是阶跃注入，30 分钟窗口拟合出的斜率可达 -2 dBm/分钟，
# 外推 120 分钟会得到 -350 dBm（实测），物理上不存在。
# 因此用「阻尼外推」：前几分钟体现趋势，之后按 1-e^(-k/tau) 指数饱和，
# 再把总偏移夹在 ±max_drift 与指标物理范围内，保证任何 horizon 都落在可信区间。
FORECAST_TREND_WINDOW = 30        # 用最近多少分钟拟合斜率
FORECAST_DAMPING_TAU_MIN = 15.0   # 趋势饱和时间常数（分钟）
FORECAST_PROFILES = {
    # 每个指标：物理范围 + 允许的最大偏移（None 表示按末值 25% 动态算）
    "power":      {"bounds": (-140.0, -40.0), "max_drift": 10.0},
    "occupancy":  {"bounds": (0.0, 100.0),    "max_drift": 20.0},
    "sinr":       {"bounds": (-20.0, 40.0),   "max_drift": 6.0},
    "throughput": {"bounds": (0.0, 300.0),    "max_drift": None},
}

# 预警阈值：与 docs/api.md、频谱数据转交说明保持同一口径
ALERT_WEAK_POWER_DBM = -95.0        # 频段功率低于此值视为弱覆盖
ALERT_CONGESTION_OCC_PCT = 85.0     # 频段占用率高于此值视为拥塞


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

    fid = "FC_" + base_dt.strftime("%Y%m%d%H%M%S")
    fore_end = base_dt + timedelta(minutes=req.horizonMinutes)

    # 频谱未加载或 baseTime 超出频谱数据范围 → 降级占位
    if _SPEC["df"] is None or base_dt > _SPEC["max_ts"]:
        result = {
            "id": fid, "scenario": req.scenario, "baseTime": req.baseTime,
            "history": {"start": req.history.start, "end": req.history.end},
            "forecast": {"start": req.baseTime, "end": fore_end.strftime("%Y-%m-%d %H:%M:%S")},
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "simulation", "status": "insufficient_data",
            "message": "当前时刻超出频谱数据范围（频谱覆盖 2026-01-01 ~ 01-07），暂不提供频谱预测。",
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
        ap_df = df[df.observation_id == ap]
        hist_sub = ap_df[(ap_df.timestamp >= hist_start) & (ap_df.timestamp < hist_end)]
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
                for b in bands:
                    slope, last = band_trend[metric][b["band_id"]]
                    if last is None:
                        values.append(None)
                        continue
                    values.append(_damped_predict(
                        last, slope, step,
                        _profile_drift(metric, last),
                        FORECAST_PROFILES[metric]["bounds"]))
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
        # 阈值与 docs/api.md、频谱转交说明一致：功率 < −95 dBm 视为弱覆盖；占用率 > 85% 视为拥塞。
        # 输出分两种语义，与《问题状态与责任分工》里"持续风险"和"提前预警"分别验收一致：
        #   already=True  → 基准时刻已越阈，提示"已发生、预计持续"（持续风险）
        #   already=False → 基准时刻未越阈，提示"预计 N 分钟后…"（提前预警，带提前量）
        # 越阈频段数下限：要求"大多数频段"越阈，而不是几个边缘频段。
        # 起因：包络中心 −60 dBm、边缘 −70 dBm，边缘频段更容易先跌破阈值；
        # 实测只按 5/50 个频段判定时，正常时段也会因为边缘频段而误报。
        # 真实弱覆盖/拥塞是 50/50 全段越阈，渐变期则是"逐渐有更多频段越阈"，
        # 用 80% 作为门槛既能滤掉边缘噪声，又能让预测阶段给出提前量。
        min_crossing_bands = max(5, int(len(bands) * 0.8))
        # 持续性约束：必须连续 N 分钟越阈才算命中。
        # 起因：KPI 有阴影衰落，单分钟噪声就可能让若干频段瞬间跌破阈值，
        # 若只按"某一分钟越阈"触发，正常时段也会冒出误报（实测出现过 1 次）。
        min_crossing_minutes = 3

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
                    f"基准时刻（{req.baseTime}）尚未越阈；按当前趋势，"
                    f"预计第 {first_minute} 分钟后开始有 {len(hardest)}/{len(bands)} 个频段"
                    f"{label}{verb} {threshold}{unit_text}，提前量约 {first_minute} 分钟。"
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
            for step in range(max(1, int(req.horizonMinutes // 5))):
                minutes_ahead = step * 5          # 预测点每 5 分钟一个，趋势按分钟拟合
                value = _damped_predict(last_val, slope_per_minute, minutes_ahead,
                                        max_drift, profile["bounds"])
                pred_pts.append({
                    "time": fmt(t), "value": value,
                    "lower": round(max(low, value - half_width), 2),
                    "upper": round(min(high, value + half_width), 2),
                })
                t += timedelta(minutes=5)
            impacts.append({"id": sid, "targetLabel": "全网", "label": label, "unit": unit,
                            "history": hist_pts, "predicted": pred_pts})
    except Exception:
        pass

    views_ap = alerts[0]["target"] if alerts else "AP-01"
    result = {
        "id": fid, "scenario": req.scenario, "baseTime": req.baseTime,
        "history": {"start": req.history.start, "end": req.history.end},
        "forecast": {"start": req.baseTime, "end": fmt(fore_end)},
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "simulation", "status": "ready",
        "message": "预测窗口内发现需要关注的风险" if alerts else "预测窗口内整体正常",
        "alerts": alerts, "spectra": spectra, "impacts": impacts,
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
    user_message = (
        f"场景：{req.scenario}\n预测基准时刻：{req.baseTime}\n"
        f"预测窗口：{req.forecast.start} ~ {req.forecast.end}\n"
        f"历史观测窗口：{req.history.start} ~ {req.history.end}（共 {n_rows} 条采样）\n"
        f"历史均值：RSRP={rsrp_text} dBm，SINR={sinr_text} dB，吞吐量={thr_text} Mbps\n"
        f"{trend_text}\n{alert_desc}\n请按系统要求输出研判文字。"
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
        reply = (
            f"【规则研判】预测窗口 {req.forecast.start} ~ {req.forecast.end} 内检测到需要关注的风险"
            f"（{alert_detail.get('title', '见预警摘要') if alert_detail else '预警事件'}）。"
            f"历史窗口 RSRP={rsrp_text} dBm、SINR={sinr_text} dB，{trend_text}。"
            f"建议：提前核查相关 AP 负载与邻区干扰，关注受影响 UE 的接入与切换状态；"
            f"若窗口后半段 SINR 持续走低，考虑均衡邻区负载或扩容。"
            f"不确定性：当前 LLM 研判不可用，以上为基于历史均值的规则提示，未结合频谱细节。"
        )

    return success_response({
        "forecastId": req.forecastId,
        "baseTime": req.baseTime,
        "alertId": req.alertId,
        "source": source,
        "reply": reply,
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


