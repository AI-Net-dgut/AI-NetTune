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

# 单元测试必须**确定性**：无论宿主机有没有配 LLM_API_KEY，这里都强制关闭大模型。
# 原因（实测）：配了 key 后 ENABLE_LLM=True，条条请求都会真的调一次大模型，
#   ① 耗时从 11s 涨到 80s；
#   ② 断言会随模型输出漂移 —— 大模型返回 ap_id="ap-1"（小写不补零）曾让
#      test_chat_intent_debug 失败，返回的自由文本也未必包含模板里的固定措辞。
# LLM 通路另有专门的联调脚本（F:\AI-NetTune\_review\check_llm.py），不混在契约测试里。
main.ENABLE_LLM = False

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
def test_anomaly_no_false_positive_on_normal_window():
    """研判不得把正常时段判成异常。

    实测问题：2026-01-02 10:17:41~11:17:41 这窗口底层标签全部 normal（RSRP -93.4、
    SINR 6.1、吞吐 49.1 Mbps），却因 `thr_avg < 50` 被判"边缘弱用户/中等"，
    导致"研判说异常、预测说无风险"两个模块结论相反。
    """
    d = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO,
        window_start="2026-01-02 10:17:41", window_end="2026-01-02 11:17:41")))
    assert d["is_anomaly"] is False, f"正常窗口不应判异常，实得 {d['anomaly_type']}"
    assert d["anomaly_type"] == "正常"
    assert d["anomaly_scope"] == "none"
    assert d["affected_aps"] == []

    # 同一时段更短的切片也不该误报（小时切片样本过少时不下结论）
    e = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO,
        window_start="2026-01-02 11:00:00", window_end="2026-01-02 11:17:41")))
    assert e["is_anomaly"] is False, f"16 行切片不应判异常，实得 {e['anomaly_type']}"


def test_anomaly_conclusion_has_evidence():
    """下了"异常"结论就必须有证据链，不能无证据断言。"""
    for start, end in ((WEAK_START, WEAK_END),
                       ("2026-01-02 10:17:41", "2026-01-02 11:17:41")):
        d = _data(main.analyze_anomaly(main.AnomalyRequest(
            scenario=SCENARIO, window_start=start, window_end=end)))
        if d["is_anomaly"]:
            assert d["kpi_evidence"], f"{start} 判了异常却没有证据"


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
def test_anomaly_minute_spans():
    """缺口5：申报书要求"定位异常的起止时间"，必须精确到分钟而不是小时。"""
    d = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start=WEAK_START, window_end=WEAK_END)))
    spans = d["anomaly_spans"]
    assert spans, "弱覆盖窗口应至少有一个异常区间"
    first = spans[0]
    for key in ("ap_id", "type", "start", "end", "minutes"):
        assert key in first, f"区间缺字段 {key}"
    # 分钟级：起止时间必须带分钟，不能只有小时
    assert len(first["start"]) == 19 and first["start"][14:16] != "00", \
        f"起止时间应精确到分钟，实得 {first['start']}"
    assert first["start"] <= first["end"]
    assert first["minutes"] >= 1
    # 平稳窗口不应报区间
    quiet = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO,
        window_start="2026-01-03 02:00:00", window_end="2026-01-03 03:59:59")))
    assert quiet["anomaly_spans"] == [], "平稳窗口不应有异常区间"


def test_chat_multi_turn_inherit():
    """缺口6：session_id 必须真正用于多轮上下文，指代词要能继承上一轮的槽位。"""
    sid = "test-multiturn"
    first = _data(main.agent_chat(main.ChatRequest(
        message="分析一下 AP-02 在 2026-01-01 10:00 到 12:00 的弱覆盖",
        session_id=sid, scenario=SCENARIO)))
    assert first["intent"] == "fault_diagnosis"
    assert first["multi_turn"]["turns"] == 1
    assert first["multi_turn"]["inherited"] == {}, "第一轮没有上文，不应继承"

    second = _data(main.agent_chat(main.ChatRequest(
        message="那给它出个方案", session_id=sid, scenario=SCENARIO)))
    inherited = second["multi_turn"]["inherited"]
    assert second["multi_turn"]["turns"] == 2, "同一 session 应累计轮次"
    assert "window" in inherited, f"「它」应继承上一轮时间窗，实得 {inherited}"
    assert inherited.get("window", "").startswith("2026-01-01 10:00")
    assert second["intent"] == "fault_diagnosis", "「出个方案」应走研判（方案是研判产物）"

    # 换一个 session 不应继承
    other = _data(main.agent_chat(main.ChatRequest(
        message="那给它出个方案", session_id="test-multiturn-other", scenario=SCENARIO)))
    assert other["multi_turn"]["inherited"] == {}, "新会话不应继承别的会话上文"
    assert other["multi_turn"]["turns"] == 1


def test_spatial_frames_series():
    """接口15：区域态势帧序列——过去N帧+未来N帧，未来帧必须比当前帧"更糟"且形态保留。"""
    d = _data(main.spatial_frame_series(
        scenario=SCENARIO, baseTime="2026-01-01 10:08:00", historyMinutes=90,
        frameMinutes=10, pastFrames=8, futureFrames=6, metric="rsrp"))
    assert d["shape"]["rows"] > 0 and d["shape"]["cols"] > 0, "应返回网格尺寸"
    assert len(d["past"]) == 8, f"过去帧数应为 8，实得 {len(d['past'])}"
    assert len(d["future"]) == 6, f"未来帧数应为 6，实得 {len(d['future'])}"

    def flat(frame):
        return [v for row in frame["values"] for v in row if v is not None]

    for frame in d["past"] + d["future"]:
        assert len(frame["start"]) == 19 and frame["start"] < frame["end"]
        vals = flat(frame)
        assert vals, "每帧都应有有效数值"
        assert all(-140 <= v <= -40 for v in vals), "RSRP 应落在物理范围内"

    # 未来帧的时间必须连续向后（可播放的前提）
    for prev, nxt in zip(d["future"], d["future"][1:]):
        assert prev["end"] == nxt["start"], "未来帧时间应首尾相接"

    # 恶化期：未来末帧的最差格应不优于当前帧（外推方向正确），且空间落差扩大
    def spread(frame):
        vals = flat(frame)
        return max(vals) - min(vals)

    assert spread(d["future"][-1]) >= spread(d["past"][-1]) - 1.0, \
        "弱覆盖渐变期未来帧的空间落差不应缩小（否则外推方向错了）"


def test_signal_navigation():
    """缺口8：信号导航 = 位置感知 + 覆盖分析 + 移动建议（分工文档 P1）。"""
    r = main.navigate_signal(scenario=SCENARIO, start_time=WEAK_START, end_time=WEAK_END,
                             ue_id=None, metric="rsrp")
    assert r["code"] == 0, r.get("message")
    d = r["data"]
    assert d["available"] is True
    assert d["ue_id"].startswith("UE-")
    assert d["samples"] > 0
    cur = d["current"]
    for key in ("x", "y", "field_value", "measured_value"):
        assert key in cur, f"当前位置缺字段 {key}"
    advice = d["advice"]
    assert advice["action"] in ("move", "stay")
    if advice["action"] == "move":
        assert advice["direction"] in ("东", "东北", "北", "西北", "西", "西南", "南", "东南")
        assert advice["distance_m"] > 0
        assert advice["gain_db"] >= 1.0
        assert "建议向" in advice["text"]
        # 目标点必须落在采样覆盖范围内（不外推到未知区域）
        assert d["grid"]["x_range"][0] <= advice["target_x"] <= d["grid"]["x_range"][1]
        assert d["grid"]["y_range"][0] <= advice["target_y"] <= d["grid"]["y_range"][1]
    # 必须如实给出前提，不能包装成确定性结论
    assert len(d["assumptions"]) >= 2


def test_chat_intent_debug():
    """阶段2：意图必须可解释——返回理解到的槽位与来源，而不是藏在后端。"""
    d = _data(main.agent_chat(main.ChatRequest(
        message="分析一下 AP-02 在 2026-01-01 10:00 到 12:00 的弱覆盖",
        session_id="test-intent-debug", scenario=SCENARIO)))
    dbg = d["intent_debug"]
    assert dbg["intent"] == "fault_diagnosis"
    assert dbg["source"] in ("rule", "llm", "fallback")
    assert dbg["parsed"]["ap_id"] == "AP-02", f"应抽取到 AP-02，实得 {dbg['parsed'].get('ap_id')}"
    assert dbg["parsed"]["scenario"] == SCENARIO
    assert dbg["parsed"]["window"]["start"].startswith("2026-01-01 10:00")

    # AP 编号写法要宽容：ap-1 / AP 3 都应识别
    for text, want in (("看看ap-1的情况", "AP-01"), ("AP 3 现在怎么样", "AP-03")):
        e = _data(main.agent_chat(main.ChatRequest(
            message=text, session_id="test-intent-debug-2", scenario=SCENARIO)))
        assert e["intent_debug"]["parsed"]["ap_id"] == want, f"「{text}」应得 {want}"


def test_forecast_narrative():
    """阶段2：预测结果必须有自然语言说明；无 LLM 时走模板，且模板不编造数据。"""
    d = _preview("2026-01-01 10:08:00")
    assert d["narrative"], "预测结果应带自然语言说明"
    assert d["narrative_source"] in ("llm", "template")
    text = d["narrative"]
    assert len(text) > 20
    # 模板必须说明不确定性/限制，不能只报喜
    assert ("区间" in text) or ("不确定性" in text) or ("不含未来故障" in text), text
    if d["narrative_source"] == "template":
        # 有告警时应给出"预计 N 分钟后"这类提前量表述
        if d["alerts"]:
            assert ("分钟后" in text) or ("已处于越阈" in text)


def test_plan_narrative():
    """阶段2：方案结果也必须有自然语言说明，且如实给出代价与限制。"""
    d = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start=WEAK_START, window_end=WEAK_END)))
    assert d["plans"], "覆盖故障应有候选方案"
    text = d["plan_narrative"]
    assert text and len(text) > 20
    assert d["plan_narrative_source"] in ("llm", "template")
    # 必须点出方案数量与代价，不能只报好处
    assert "候选方案" in text or "方案" in text
    assert ("权衡" in text) or ("代价" in text) or ("风险" in text), text
    # 必须声明这是估算而非现网验证
    assert ("未在现网验证" in text) or ("经验系数" in text), text
    # 拼接质量
    assert "。；" not in text


def test_chat_tool_chain():
    """阶段2：复合问法应触发多步编排，并返回可解释的执行轨迹。"""
    d = _data(main.agent_chat(main.ChatRequest(
        message="哪个 AP 最差，接下来会怎样", session_id="test-chain", scenario=SCENARIO,
        window_start=WEAK_START, window_end=WEAK_END)))
    assert d.get("kind") == "chain", f"应走编排，实得 {d.get('kind')}"
    steps = d.get("chain") or []
    assert len(steps) >= 2, "编排至少两步"
    tools = [s["tool"] for s in steps]
    assert tools[0] == "analyze_anomaly", f"第一步应研判，实得 {tools}"
    assert "forecast_preview" in tools, f"第二步应做预测，实得 {tools}"
    assert d.get("worst_ap", "").startswith("AP-"), f"应识别最差 AP，实得 {d.get('worst_ap')}"
    for step in steps:
        assert step["status"] == "ok" and step["summary"]
    # 编排结果要能自洽：最差 AP 出现在研判的受影响对象里
    assert d["worst_ap"] in (d["diagnosis"].get("affected_aps") or [d["worst_ap"]])


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
