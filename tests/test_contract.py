"""AI-NetTune 接口契约冒烟测试（零依赖：只用标准库 + 后端自身依赖）。

为什么不用 pytest：本机没装 pytest，也没装 httpx（FastAPI TestClient 需要它）。
为了"拉到仓库就能验证"，这里用最朴素的方式：`test_*` 函数 + 一个自写的运行器，
直接调用 main.py 的接口函数（FastAPI 的 Query 默认值在请求上下文外会失效，
所以调用时所有参数都显式传，见各用例）。

运行：
    python tests/run_tests.py              # 全部
    python tests/run_tests.py anomaly      # 只跑名字含 anomaly 的

退出码：0 = 全通过；1 = 有失败。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import main  # noqa: E402

ISO = "%Y-%m-%d %H:%M:%S"
SCENARIO = "校园"

# 演示用的固定窗口（与前端 replay/config.ts、DEFAULT_SCOPE 对齐）
WEAK_START, WEAK_END = "2026-01-01 10:00:00", "2026-01-01 11:59:59"


def _data(res):
    """统一剥掉 {code, message, data} 外壳。"""
    return res.get("data", res) if isinstance(res, dict) else res


def _preview(base: str, minutes: int = 60, history: int = 60):
    base_dt = datetime.strptime(base, ISO)
    return _data(main.forecast_preview(main.ForecastPreviewRequest(
        scenario=SCENARIO, baseTime=base,
        history=main._TimeRange(
            start=(base_dt - timedelta(minutes=history)).strftime(ISO),
            end=base),
        horizonMinutes=minutes)))


# ---------------------------------------------------------------- 部署自检
def test_health():
    d = _data(main.health())
    assert d["ok"] is True, "数据自检未通过"
    assert d["network"]["rows"] > 0
    assert d["spectrum"]["rows"] > 0
    caps = d["capabilities"]
    assert caps["kriging"] is True, "Kriging 应为可用"


# ---------------------------------------------------------------- 数据查询
def test_data_query():
    # 注意：main.py 的参数用 FastAPI Query(...) 做默认值，在请求上下文之外调用时
    # 必须把**所有**参数都显式传进去，否则拿到的是 Query 对象本身（会报没有 .split）。
    d = _data(main.data_query(
        scenario=SCENARIO, start_time=WEAK_START, end_time=WEAK_END,
        ap_id=None, user_id=None, metrics=None, offset=0, limit=500))
    assert d["total"] > 0
    assert len(d["columns"]) == 18, f"应为 18 列，实得 {len(d['columns'])}"
    for r in d["records"][:5]:
        assert r["ap_id"].startswith("AP-")
        assert r["network_status"] in ("normal", "weak_coverage", "congestion")


# ---------------------------------------------------------------- 研判
def test_anomaly_coverage_window():
    d = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start=WEAK_START, window_end=WEAK_END)))
    assert d["is_anomaly"] is True
    assert d["anomaly_type"] == "覆盖故障", f"应为覆盖故障，实得 {d['anomaly_type']}"
    assert d["anomaly_scope"] == "window", "整窗异常应标 window"
    assert "AP-02" in d["affected_aps"]
    assert d["candidates"], "应有候选原因"


def test_anomaly_scope_hour():
    """整窗均值正常、只有个别小时越阈 → 必须标 hour，不能谎称整窗异常。"""
    d = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO,
        window_start="2026-01-03 02:00:00", window_end="2026-01-03 03:59:59")))
    assert d["anomaly_scope"] in ("window", "hour", "none")
    if d["is_anomaly"] and d["anomaly_scope"] == "hour":
        assert "时段性异常" in d["cause"], "hour 范围必须在 cause 里说清楚"


def test_anomaly_plans():
    d = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start=WEAK_START, window_end=WEAK_END)))
    plans = d.get("plans") or []
    assert len(plans) >= 2, "覆盖故障应给出至少 2 个候选方案"
    sim = plans[0]["simulation"]
    assert "summary" in sim


def test_plan_simulate_roundtrip():
    d = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start=WEAK_START, window_end=WEAK_END)))
    plan = (d.get("plans") or [])[0]
    r = _data(main.plan_simulate(main.PlanSimulateRequest(
        plan_id=plan["id"], scenario=SCENARIO,
        window_start=WEAK_START, window_end=WEAK_END)))
    # plan_simulate 直接返回仿真体（结构同方案内自带的 simulation）
    sim = r.get("simulation", r) if isinstance(r, dict) else r
    assert sim.get("summary"), "仿真应返回 summary"


# ---------------------------------------------------------------- 空间视图
def test_heatmap():
    d = _data(main.get_heatmap(scenario=SCENARIO, start_time=WEAK_START,
                               end_time=WEAK_END, metric="rsrp"))
    assert d["grid_count"] > 0
    assert d["method"] in ("kriging", "idw")


def test_topology():
    d = _data(main.get_basic_topology(scenario=SCENARIO, start_time=WEAK_START,
                                      end_time=WEAK_END, timestamp=None))
    aps = [n for n in d["nodes"] if n["type"] == "ap"]
    assert len(aps) == 3


# ---------------------------------------------------------------- 预警
def test_forecast_preview_early_warning():
    """渐变期必须能给出提前量 —— 这是申报书的核心里程碑。

    不在单个时刻断言：渐变期只有十几分钟，哪一分钟给出提前量取决于趋势拟合，
    钉死某一分钟会让测试变脆。这里扫 10:00~10:20（事件 10:00 起、渐变 20 分钟），
    要求"至少有一次提前预警"，并顺带要求"至少有一次已发生"。
    """
    leads = []
    saw_happened = False
    for offset in range(0, 21, 2):
        bd = datetime.strptime("2026-01-01 10:00:00", ISO) + timedelta(minutes=offset)
        base = bd.strftime(ISO)
        d = _preview(base)
        for a in d["alerts"]:
            lead = int((datetime.strptime(a["start"], ISO) - bd).total_seconds() // 60)
            if lead > 0:
                leads.append((base, lead))
            else:
                saw_happened = True
    assert leads, "整个渐变期内都没有给出提前预警"
    assert saw_happened, "事件发生后应给出「已发生·持续」"


def test_forecast_preview_insufficient():
    """超出频谱覆盖区（频谱只有 7 天）应明确返回 insufficient_data，而不是瞎给结果。"""
    d = _preview("2026-01-20 10:00:00")
    assert d["status"] in ("insufficient_data", "ready")


# ---------------------------------------------------------------- 接口10
def test_forecast_interface10():
    d = _data(main.analyze_forecast(main.ForecastRequest(
        scenario=SCENARIO, window_start=WEAK_START, window_end=WEAK_END)))
    assert d["method"] in ("prophet", "moving_average", "mixed")
    assert d["ues"], "应返回 UE 列表"
    s = d["series"][d["ues"][0]]["rsrp"]
    for key in ("true_mean", "pred_mean", "mae", "rmse", "bins", "true", "predicted"):
        assert key in s, f"序列缺字段 {key}"
    if s.get("method") != "none":
        assert s["mae"] is not None, "应给出有量纲的 MAE"


# ---------------------------------------------------------------- 对话
def test_chat_chitchat():
    """"你好"不能被当成 KPI 查询（审查报告 B-9）。"""
    for msg in ("你好", "谢谢", "你是谁"):
        d = _data(main.agent_chat(main.ChatRequest(message=msg, scenario=SCENARIO)))
        assert d["intent"] == "chat", f"「{msg}」应走 chat，实得 {d['intent']}"


def test_chat_routes_tools():
    cases = {
        "当前各AP的RSRP": "query_kpi",
        "看看热力图": "get_heatmap",
        "分析一下 AP-02 的弱覆盖": "fault_diagnosis",
        "预测下一个小时的功率": "forecast",
    }
    for msg, want in cases.items():
        d = _data(main.agent_chat(main.ChatRequest(message=msg, scenario=SCENARIO)))
        assert d["intent"] == want, f"「{msg}」应为 {want}，实得 {d['intent']}"


# ---------------------------------------------------------------- 报告
def test_fault_events_endpoint():
    """接口15：事件清单来自 fault_events.csv，是"案例起点"的唯一事实来源。"""
    d = _data(main.data_events(scenario=None))
    assert d["count"] >= 4, f"事件数过少：{d['count']}"
    first = d["events"][0]
    for key in ("event_id", "scenario", "ap_id", "fault_type",
                "start_time", "end_time", "ramp_minutes", "strength"):
        assert key in first, f"事件缺字段 {key}"
    assert first["ap_id"].startswith("AP-")
    assert first["fault_type"] in ("weak_coverage", "congestion")
    assert first["start_time"] < first["end_time"]
    # 按场景过滤：不存在的场景应返回 0 条而不是报错
    assert _data(main.data_events(scenario="商场"))["count"] == 0


def test_report_export():
    """导出接口返回 {report_id, file_url, format, include_chart, sample_count, bytes}。"""
    def _report(include_chart: bool):
        d = main.export_report(main.ReportRequest(
            scenario=SCENARIO, start_time=WEAK_START, end_time=WEAK_END,
            format="html", include_chart=include_chart))
        payload = d.get("data", d)
        assert payload.get("file_url"), f"返回结构异常：{list(payload)[:8]}"
        assert payload.get("bytes", 0) > 0, "报告文件为空"
        # file_url 形如 /api/download/reports/RPT_xxx.html
        name = payload["file_url"].rstrip("/").split("/")[-1]
        import os
        from data_layout import BASE_DIR
        reports_dir = os.getenv("NETTUNE_REPORTS_DIR") or str(BASE_DIR / "reports")
        return payload, (Path(reports_dir) / name).read_text(encoding="utf-8")

    payload, with_chart = _report(True)
    assert payload["include_chart"] is True
    assert "<svg" in with_chart, "include_chart=true 应内嵌 SVG"
    payload2, without = _report(False)
    assert payload2["include_chart"] is False
    assert "<svg" not in without, "include_chart=false 不应有 SVG"


# ---------------------------------------------------------------- 数据层
def test_data_label_consistency():
    """弱覆盖行 RSRP ≤ −110、normal 行 > −110（含 0.01 舍入容差）。"""
    import pandas as pd
    from data_layout import DATA_DIR
    path = DATA_DIR / "network_data_7d.csv"
    df = pd.read_csv(path, encoding="utf-8-sig",
                     usecols=["network_status", "rsrp", "prb_usage", "packet_loss"])
    weak = df[df.network_status == "weak_coverage"]["rsrp"]
    normal = df[df.network_status == "normal"]["rsrp"]
    cong = df[df.network_status == "congestion"]
    assert len(weak) and weak.max() <= -110 + 0.02, f"弱覆盖最大 {weak.max():.2f} 超过 −110"
    assert len(normal) and normal.min() > -110.01, f"normal 最小 {normal.min():.2f} 低于 −110"
    if len(cong):
        assert cong.prb_usage.min() > 85.0, "拥塞行 PRB 应 > 85%"
        assert cong.packet_loss.min() > 5.0, "拥塞行丢包应 > 5%"


def test_data_numeric_bounds():
    """D-7：RSRP 不得高于 −60；吞吐不得恒等 150；正常行 PRB p95 不得逼近拥塞阈值。"""
    import pandas as pd
    from data_layout import DATA_DIR
    df = pd.read_csv(DATA_DIR / "network_data_30d.csv", encoding="utf-8-sig",
                     usecols=["network_status", "rsrp", "throughput", "prb_usage"])
    assert df.rsrp.max() <= -60.0, f"RSRP 最大 {df.rsrp.max():.2f} 高于 −60"
    assert int((df.throughput.round(2) == 150.0).sum()) == 0, "仍存在吞吐恰好 150 的水平带"
    normal_prb = df[df.network_status == "normal"]["prb_usage"]
    assert normal_prb.quantile(0.95) < 85.0, f"正常行 PRB p95={normal_prb.quantile(0.95):.1f} 逼近拥塞阈值"
