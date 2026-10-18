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
from pathlib import Path
import llm_agent
from llm_agent import get_intent, rule_intent, extract_time_range, call_llm_chat
from forecast_model import build_series, DEFAULT_METRICS
from spatial_model import build_spatial
from realtime_simulator import start_realtime_service, get_incremental_data, DATA_COLUMNS
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from datetime import datetime, timedelta
from fastapi.staticfiles import StaticFiles

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
import os
# 自动创建reports目录，避免不存在报错
os.makedirs("reports", exist_ok=True)
# 挂载静态目录，路径和返回的file_url前缀保持一致
app.mount("/download/reports", StaticFiles(directory="reports"), name="download_reports")
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

# ============ 数据源：唐苗仿真数据集 ============
# 默认 30d（129600条，18列，P1 预测训练数据量充足）；3d 版备份为 network_data_3d_bak.csv
# 注意：表头首列带 BOM（\ufefftimestamp），必须用 utf-8-sig 读取
DATA_PATH = Path(__file__).resolve().parent / "data" / "network_data_30d(1).csv"

def load_data():
    if not DATA_PATH.exists():
        print("[WARN] 未找到 data/network_data.csv，接口将返回空数据")
        return []
    with open(DATA_PATH, encoding="utf-8-sig", newline="") as f:
        return list(_csv.DictReader(f))

DF = load_data()

# 完整 18 列：11 核心 KPI + AP/UE 坐标（4列）+ 故障深度分析辅助字段（3列）
ALL_COLUMNS = ["timestamp", "ap_id", "ap_x", "ap_y", "user_id", "ue_x", "ue_y",
               "rsrp", "sinr", "throughput", "latency", "packet_loss", "prb_usage",
               "scenario", "network_status",
               "retransmission_count", "traffic_demand", "active_users"]

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
        },
    }

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
            "detail": d, "query_ref": {"scenario": scenario,
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

# ---- Function Calling 工具注册表：意图名 → 工具函数（新增工具在这里登记即可） ----
TOOLS = {
    "query_kpi": tool_query_kpi,
    "fault_diagnosis": tool_fault_diagnosis,
    "get_heatmap": tool_heatmap,
    "get_topology": tool_topology,
}


# 开关：可配置是否启用LLM增强，LLM故障时设为False即可纯规则运行

ENABLE_LLM = True


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
    data = handler(scenario, req.window_start, req.window_end)
    return {"code": 0, "message": "success", "data": data}


# ============ 接口4：导出分析报告 ============
class ReportRequest(BaseModel):
    scenario: str
    start_time: str
    end_time: str
    format: str = "md"
    include_chart: bool = True

@app.post("/api/export/report")
def export_report(req: ReportRequest):
    # 第一周：只生成报告编号与下载路径；第3周接入真实报告生成
        # ===== 新增：参数合法性校验 =====
    if not req.scenario or not req.scenario.strip():
        return error_response(ERROR_CODE["PARAM_MISSING"], "场景参数不能为空")
    if not req.start_time or not req.end_time:
        return error_response(ERROR_CODE["PARAM_MISSING"], "时间参数不能为空")
    # ==============================
    report_id = "RPT_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = "md" if req.format in ("md", "markdown") else "html"
    filename = f"{report_id}.{ext}"
    file_path = f"reports/{filename}"

    # ===== 新增：生成真实报告内容并写入文件 =====
    # 1. 拉取对应范围的数据
    rows = filter_rows(req.scenario, req.start_time, req.end_time)

    # 2. 组装md报告内容
    content = f"""# 网络分析报告
    - 报告ID：{report_id}
    - 场景：{req.scenario}
    - 时间范围：{req.start_time} 至 {req.end_time}

    ## 数据概览
    - 总采样条数：{len(rows)}
    - 平均RSRP：{avg(rows, 'rsrp'):.1f} dBm
    - 平均SINR：{avg(rows, 'sinr'):.1f} dB
    - 平均吞吐量：{avg(rows, 'throughput'):.1f} Mbps
    """

    # 3. 写入文件
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    # ============================================

    return success_response({
        "report_id": report_id,
        "file_url": f"/download/reports/{filename}"
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
    if not timestamp:
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
    return success_response({
        "scenario": req.scenario,
        "window_start": req.window_start,
        "window_end": req.window_end,
        "method": top_method,
        "bin_size_min": built["bin_size_min"],
        "metrics": built["metrics"],
        "ues": built["ues"],
        "series": built["series"]
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
# 内存缓存：forecastId -> 该轮 preview 结果，供 analyze 按 alertId 反查预警详情
_FORECAST_CACHE: dict = {}


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


@app.post("/api/forecast/preview", summary="预测预警 v2：预测预览（频谱数据接入中，占位实现）")
def forecast_preview(req: ForecastPreviewRequest):
    """频谱频段数据扩展完成前的占位：返回 insufficient_data，不带图表。
    前端约定：不可用状态可被正常渲染为'数据接入中'提示，不阻塞浮窗打开。"""
    try:
        base_dt = _parse_ts(req.baseTime)
    except (ValueError, TypeError):
        return error_response(ERROR_CODE["PARAM_INVALID"], "baseTime 格式应为 yyyy-MM-dd HH:mm:ss")
    if req.horizonMinutes <= 0 or req.horizonMinutes > 1440:
        return error_response(ERROR_CODE["PARAM_OUT_OF_RANGE"], "horizonMinutes 需在 1~1440 分钟之间")

    fid = "FC_" + base_dt.strftime("%Y%m%d%H%M%S")
    result = {
        "id": fid,
        "scenario": req.scenario,
        "baseTime": req.baseTime,
        "history": {"start": req.history.start, "end": req.history.end},
        "forecast": {"start": req.baseTime,
                     "end": (base_dt + timedelta(minutes=req.horizonMinutes)).strftime("%Y-%m-%d %H:%M:%S")},
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "simulation",
        "status": "insufficient_data",
        "message": "电磁频谱频段数据接入中，暂不提供频谱预测；AI 研判仍可使用。",
        "alerts": [],
        "spectra": [],
        "impacts": [],
    }
    _FORECAST_CACHE[fid] = result
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


