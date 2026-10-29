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
import llm_agent  # noqa: E402

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


def test_report_trend_chart_avoids_misleading_line():
    """导出报告的小时趋势图：少于 3 个点不能画折线。

    实测问题（报错来自用户截图）：2 小时窗口只产生 2 个小时点，却被画成
    一条贯穿全宽的斜线，看起来像"明确下降趋势"；纵轴还按 2 个点的极差自适应，
    把 3.7 dB 的正常波动铺满整幅图；x 轴刻度 (0, len//2, len-1) 在 len=2 时
    生成 (0,1,1)，最后一个标签被画两遍。
    """
    import re
    import report_model as rm

    two = rm._svg_trend([("02 08", -90.5), ("02 09", -94.2)], "dBm", "RSRP 小时趋势")
    assert "<polyline" not in two, "2 个点不应画折线"
    assert two.count("<circle") == 2, "应改为画 2 个散点"
    assert "不足以判断趋势" in two, "必须提示数据点不足"

    # x 轴刻度去重
    ticks = [t for t in re.findall(r'text-anchor="middle"[^>]*>([^<]+)<', two)
             if re.match(r"^\d\d ", t)]
    assert len(ticks) == len(set(ticks)) == 2, f"x 轴刻度应去重，实得 {ticks}"

    # 纵轴最小跨度：两点只差 3.7 dB，纵轴跨度应被撑到 ≥6 dB，避免夸大波动
    ylab = [float(x) for x in re.findall(r'text-anchor="end"[^>]*>(-?\d+\.\d+)<', two)]
    assert ylab and abs(max(ylab) - min(ylab)) >= 5.9, f"纵轴跨度过小：{ylab}"

    # 3 个点及以上仍画折线
    three = rm._svg_trend([("02 08", -90.5), ("02 09", -94.2), ("02 10", -92.0)],
                          "dBm", "RSRP 小时趋势")
    assert "<polyline" in three and "不足以判断趋势" not in three


def test_api_routes_map_to_expected_functions():
    """每个 /api 路由必须绑定到真实业务函数。

    实测事故：把辅助函数 _supported_range() 插到了
    `@app.post("/api/analyze/anomaly")` 与 analyze_anomaly 之间，
    装饰器于是绑到了辅助函数上 → 接口2 返回的是 ['2026-01-01 00:00:00',
    '2026-01-07 23:59:00']（支撑范围元组），而 analyze_anomaly 变成无人调用的普通函数。
    单元测试全部直接调用函数，只有跑 HTTP 冒烟才发现——所以这里补一条路由断言。
    """
    api = {r.path: r.endpoint.__name__ for r in main.app.routes
           if hasattr(r, "endpoint") and r.path.startswith("/api")}
    assert len(api) == 17, f"/api 路由数量应为 17，实得 {len(api)}"

    expected = {
        "/api/data/query": "data_query",
        "/api/analyze/anomaly": "analyze_anomaly",
        "/api/agent/chat": "agent_chat",
        "/api/export/report": "export_report",
        "/api/data/heatmap": "get_heatmap",
        "/api/network/topology/basic": "get_basic_topology",
        "/api/data/events": "data_events",
        "/api/predict/result": "predict_result",
        "/api/network/topology/enhanced": "get_enhanced_topology",
        "/api/analyze/forecast": "analyze_forecast",
        "/api/forecast/preview": "forecast_preview",
        "/api/forecast/analyze": "forecast_analyze",
        "/api/forecast/plans": "forecast_plans",
        "/api/health": "health",
        "/api/plan/simulate": "plan_simulate",
        "/api/forecast/spatial_frames": "spatial_frame_series",
        "/api/navigate/signal": "navigate_signal",
    }
    for path, func in expected.items():
        assert api.get(path) == func, f"{path} 应绑定 {func}，实得 {api.get(path)}"

    # 兜底：任何 /api 路由都不能绑到下划线开头的内部辅助函数
    bad = [(p, f) for p, f in api.items() if f.startswith("_")]
    assert not bad, f"路由被绑定到内部辅助函数：{bad}"


def test_anomaly_supported_range_alignment():
    """研判与预测必须使用同一"支撑范围"（网络表 30 天 ∩ 频谱表 7 天 = 7 天）。

    实测缺陷：网络表有 30 天、频谱表只有前 7 天。研判对 01-20 这类窗口会答
    "覆盖故障/当前故障中"，而同一时刻预测返回 insufficient_data ——
    同一时间窗里"研判说有故障、预测说没数据"，自相矛盾。
    """
    sup = main._supported_range()
    assert sup == ("2026-01-01 00:00:00", "2026-01-07 23:59:00"), f"支撑范围异常：{sup}"

    # ① 完全越界：不下故障结论，给出明确说明与范围回显
    out = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start="2026-01-20 10:00:00", window_end="2026-01-20 11:00:00")))
    assert out["is_anomaly"] is False and out["current_state"] == "normal"
    assert "超出支撑范围" in out["range_notice"]
    assert out["data_range"] == {"start": sup[0], "end": sup[1]}

    # ② 部分越界：裁剪到范围内继续研判，并说明裁剪动作
    clipped = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start="2026-01-05 10:00:00", window_end="2026-01-10 10:00:00")))
    assert "裁剪到 2026-01-07 23:59:00" in clipped["range_notice"]
    early = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start="2025-12-31 10:00:00", window_end="2026-01-02 10:00:00")))
    assert "起始时间早于支撑范围" in early["range_notice"]

    # ③ 范围内：不产生多余提示
    inside = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start=WEAK_START, window_end=WEAK_END)))
    assert inside["range_notice"] == ""
    # ④ 与预测侧一致：越界窗口的预测同样不可用
    fc = _data(main.forecast_preview(main.ForecastPreviewRequest(
        scenario=SCENARIO, baseTime="2026-01-20 10:00:00",
        history=main._TimeRange(start="2026-01-20 09:00:00", end="2026-01-20 10:00:00"),
        horizonMinutes=30)))
    assert fc["status"] == "insufficient_data"


def test_forecast_analyze_rejects_stale_or_mismatched_context():
    """研判必须拒绝"记录已失效"和"上下文不一致"，不能把拿不到结果当没风险。

    审计第 1 条：缓存淘汰后原实现返回"未检测到越阈风险 / 维持配置"（漏报式回答）。
    审计第 5 条：场景、预测范围、关注时段越界都要拒绝。
    """
    base, hs, he = "2026-01-02 14:12:00", "2026-01-02 13:12:00", "2026-01-02 14:12:00"
    prev = _data(main.forecast_preview(main.ForecastPreviewRequest(
        scenario=SCENARIO, baseTime=base,
        history=main._TimeRange(start=hs, end=he), horizonMinutes=120)))
    fid = prev["id"]
    fc = (prev["forecast"]["start"], prev["forecast"]["end"])

    def ask(scenario=SCENARIO, forecast=None, tw=None, forecast_id=fid):
        return main.forecast_analyze(main.ForecastAnalyzeRequest(
            forecastId=forecast_id, scenario=scenario, baseTime=base, alertId=None,
            history=main._TimeRange(start=hs, end=he),
            forecast=main._TimeRange(start=(forecast or fc)[0], end=(forecast or fc)[1]),
            targetWindow=(main._TimeRange(start=tw[0], end=tw[1]) if tw else None)))

    # ① 记录不存在（模拟缓存淘汰/重启）→ 必须报错，且不能宣称无风险
    saved = dict(main._FORECAST_CACHE)
    try:
        main._FORECAST_CACHE.clear()
        stale = ask()
    finally:
        main._FORECAST_CACHE.clear()
        main._FORECAST_CACHE.update(saved)
    assert stale.get("code") != 0, "记录失效时不应返回成功"
    assert ("过期" in str(stale.get("message"))) or ("不存在" in str(stale.get("message")))
    assert stale.get("data") is None

    # ② 场景不一致 ③ 预测范围不一致 ④ 关注时段越界
    assert ask(scenario="商场").get("code") == 40002
    assert ask(forecast=("2026-01-02 14:12:00", "2026-01-02 14:17:00")).get("code") == 40002
    assert ask(tw=("2026-01-02 17:00:00", "2026-01-02 18:00:00")).get("code") == 40003
    # ⑤ 正常上下文仍可用
    ok = ask(tw=("2026-01-02 14:35:00", "2026-01-02 14:45:00"))
    assert ok.get("code") == 0 and ok["data"]["reply"]


def test_short_window_does_not_claim_fault():
    """样本过少时不下故障结论（审计第 3 条）。

    实测 AP-01 01-02 05:52 只有 2 行采样、标签全 normal，却返回
    is_anomaly=true + 覆盖故障 + current_state=normal + anomaly_spans=[]。
    """
    for ap, ts in (("AP-01", "2026-01-02 05:52:00"), ("AP-02", "2026-01-01 10:08:00")):
        d = _data(main.analyze_anomaly(main.AnomalyRequest(
            scenario=SCENARIO, window_start=ts, window_end=ts, ap_id=ap)))
        assert d["is_anomaly"] is False, f"{ap} {ts} 样本不足不应判故障"
        assert d["current_state"] != "fault_active"
        assert d["anomaly_type"] in ("正常", "性能提示"), d["anomaly_type"]


def test_candidates_include_spectrum_evidence():
    """候选原因必须拿到合并后的证据（含频谱维），审计第 4 条。"""
    d = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start="2026-01-01 10:00:00", window_end="2026-01-01 10:15:00")))
    assert any(e["metric"] == "spectrum_power_bands" for e in d["kpi_evidence"])
    support = " ".join(d["candidates"][0]["support_evidence"])
    assert "频谱功率越阈" in support, f"候选支持证据缺频谱项：{support}"

    cong = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start="2026-01-05 20:00:00", window_end="2026-01-05 20:20:00")))
    csupport = " ".join(cong["candidates"][0]["support_evidence"])
    assert "频谱占用越阈" in csupport, f"拥塞候选支持证据缺频谱项：{csupport}"


def test_spatial_frames_early_history_returns_insufficient():
    """最早历史附近没有采样时应返回数据不足，而不是 500（审计第 8 条）。"""
    d = _data(main.spatial_frame_series(
        scenario=SCENARIO, baseTime="2026-01-01 00:10:00", historyMinutes=36,
        frameMinutes=6, pastFrames=6, futureFrames=5, metric="rsrp"))
    assert d["status"] == "insufficient_data", d.get("status")
    assert d["past"] == [] and d["future"] == []
    assert "没有采样" in d["message"]

    ok = _data(main.spatial_frame_series(
        scenario=SCENARIO, baseTime="2026-01-02 10:08:00", historyMinutes=90,
        frameMinutes=10, pastFrames=8, futureFrames=6, metric="rsrp"))
    assert len(ok["past"]) == 8 and len(ok["future"]) == 6


def test_chat_forecast_horizon_parses_chinese_durations():
    """对话里的预测时长必须认中文数词与量词"个"（审计第 2 条）。

    原先 "未来一个小时/三个小时" 匹配不上任何正则，静默回落成默认 30 分钟。
    """
    cases = [("未来半个小时网络如何", 30), ("未来一个小时网络如何", 60),
             ("未来10分钟呢", 10), ("接下来两小时怎么样", 120), ("未来2小时会怎样", 120)]
    for msg, want in cases:
        d = main.tool_forecast(SCENARIO, "2026-01-02 08:00:00", "2026-01-02 12:00:00", msg)
        got = (d.get("query_ref") or {}).get("horizon_minutes")
        assert got == want, f"{msg} 期望 {want} 分钟，实得 {got}"
    # "三个小时"超过产品上限时夹到 120，且回复里能看到真实使用的时长
    d = main.tool_forecast(SCENARIO, "2026-01-02 08:00:00", "2026-01-02 12:00:00", "未来三个小时网络如何")
    assert d["query_ref"]["horizon_minutes"] == 120
    assert "未来120分钟" in d["reply"]


def test_plan_simulate_rejects_unknown_enum_value():
    """仿真参数里的枚举值必须在服务端被拒绝（审计 5.3）。"""
    d = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start="2026-01-05 20:00:00", window_end="2026-01-05 20:20:00")))
    plan = next(p for p in d["plans"] if any(x["id"] == "capacity" for x in p["parameters"]))
    bad = main.plan_simulate(main.PlanSimulateRequest(
        plan_id=plan["id"], scenario=SCENARIO,
        window_start="2026-01-05 20:00:00", window_end="2026-01-05 20:20:00",
        parameter_values={"capacity": "garbage"}))
    assert bad.get("code") == 40002, bad
    ok = main.plan_simulate(main.PlanSimulateRequest(
        plan_id=plan["id"], scenario=SCENARIO,
        window_start="2026-01-05 20:00:00", window_end="2026-01-05 20:20:00",
        parameter_values={"capacity": "one_carrier"}))
    assert ok.get("code") == 0


def test_forecast_plans_generates_plans_with_basis():
    """预测风险必须能变成方案，且方案带清来源与依据（审计第 6 条）。

    不误导的三条硬约束：
      * 方案只来自该轮预测的风险，不混入现存问题；
      * 参数基线与效应评估窗口 = 预测基准前的实测窗口（basisWindow）；
      * 无风险时不编造方案（plans 为空 + 说明）。
    """
    base, hs, he = "2026-01-02 14:12:00", "2026-01-02 13:12:00", "2026-01-02 14:12:00"

    def ask(fid, fc, scenario=SCENARIO, tw=None):
        return main.forecast_plans(main.ForecastPlanRequest(
            forecastId=fid, scenario=scenario, baseTime=base,
            history=main._TimeRange(start=hs, end=he),
            forecast=main._TimeRange(start=fc[0], end=fc[1]),
            targetWindow=(main._TimeRange(start=tw[0], end=tw[1]) if tw else None)))

    p120 = _data(main.forecast_preview(main.ForecastPreviewRequest(
        scenario=SCENARIO, baseTime=base, history=main._TimeRange(start=hs, end=he), horizonMinutes=120)))
    fc = (p120["forecast"]["start"], p120["forecast"]["end"])
    r = ask(p120["id"], fc)
    assert r.get("code") == 0, r
    d = r["data"]
    assert d["plans"], "有风险时必须能生成方案"
    plan = d["plans"][0]
    assert plan.get("origin") == "forecast"
    assert plan["basis"]["forecastId"] == p120["id"]
    # 评估窗口必须是基准前的实测窗口，不能是预测目标时段
    assert d["basisWindow"] == {"start": hs, "end": he}
    assert d["risk"]["ap"] == plan["objectIds"][0]
    assert "预测基准前的实测窗口" in d["disclaimer"]

    # 上下文不符 / 记录失效 → 拒绝
    assert ask(p120["id"], fc, scenario="商场").get("code") == 40002
    assert ask("FC-not-exist", fc).get("code") == 40003

    # 无风险的一轮 → 明确说"不生成方案"，不编造
    p5 = _data(main.forecast_preview(main.ForecastPreviewRequest(
        scenario=SCENARIO, baseTime=base, history=main._TimeRange(start=hs, end=he), horizonMinutes=5)))
    r5 = ask(p5["id"], (p5["forecast"]["start"], p5["forecast"]["end"]))
    assert r5.get("code") == 0
    assert r5["data"]["plans"] == [] and "不生成处置方案" in r5["data"]["message"]

    # 方案可直接用于 /api/plan/simulate（窗口 = 基准前实测窗口）
    sim = main.plan_simulate(main.PlanSimulateRequest(
        plan_id=plan["id"], scenario=SCENARIO, window_start=hs, window_end=he,
        parameter_values={plan["parameters"][0]["id"]: str(plan["parameters"][0]["suggestedValue"])}))
    assert sim.get("code") == 0


def test_forecast_context_boundaries_are_enforced():
    """预测后续接口的边界（审计 10-29 第 7、8、9 项）。

    覆盖：数据不足的缓存 / 目标部分越界 / 起止反向 / alertId 与时段冲突 /
    预测来源方案的仿真绑定（窗口与 AP）。
    """
    base, hs, he = "2026-01-02 14:12:00", "2026-01-02 13:12:00", "2026-01-02 14:12:00"
    prev = _data(main.forecast_preview(main.ForecastPreviewRequest(
        scenario=SCENARIO, baseTime=base, history=main._TimeRange(start=hs, end=he), horizonMinutes=120)))
    fc = (prev["forecast"]["start"], prev["forecast"]["end"])
    alert_id = (prev.get("alerts") or [{}])[0].get("id")

    def analyze(tw=None, alert=None):
        return main.forecast_analyze(main.ForecastAnalyzeRequest(
            forecastId=prev["id"], scenario=SCENARIO, baseTime=base, alertId=alert,
            history=main._TimeRange(start=hs, end=he),
            forecast=main._TimeRange(start=fc[0], end=fc[1]),
            targetWindow=(main._TimeRange(start=tw[0], end=tw[1]) if tw else None)))

    def plans(tw=None, alert=None):
        return main.forecast_plans(main.ForecastPlanRequest(
            forecastId=prev["id"], scenario=SCENARIO, baseTime=base,
            history=main._TimeRange(start=hs, end=he),
            forecast=main._TimeRange(start=fc[0], end=fc[1]), alertId=alert,
            targetWindow=(main._TimeRange(start=tw[0], end=tw[1]) if tw else None)))

    # 目标部分越界 / 起止反向 / alertId 与时段冲突
    assert analyze(tw=("2026-01-02 16:00:00", "2026-01-02 18:00:00")).get("code") == 40003
    assert plans(tw=("2026-01-02 16:00:00", "2026-01-02 18:00:00")).get("code") == 40003
    assert analyze(tw=("2026-01-02 15:50:00", "2026-01-02 14:40:00")).get("code") == 40002
    assert plans(tw=("2026-01-02 15:50:00", "2026-01-02 14:40:00")).get("code") == 40002
    assert analyze(tw=("2026-01-02 14:15:00", "2026-01-02 14:20:00"), alert=alert_id).get("code") == 40002

    # 数据不足的缓存：不能解释成"没有风险"
    p8 = _data(main.forecast_preview(main.ForecastPreviewRequest(
        scenario=SCENARIO, baseTime="2026-01-08 12:00:00",
        history=main._TimeRange(start="2026-01-08 11:00:00", end="2026-01-08 12:00:00"),
        horizonMinutes=60)))
    assert p8["status"] == "insufficient_data"
    for call in (
        main.forecast_analyze(main.ForecastAnalyzeRequest(
            forecastId=p8["id"], scenario=SCENARIO, baseTime=p8["baseTime"], alertId=None,
            history=main._TimeRange(start=p8["history"]["start"], end=p8["history"]["end"]),
            forecast=main._TimeRange(start=p8["forecast"]["start"], end=p8["forecast"]["end"]))),
        main.forecast_plans(main.ForecastPlanRequest(
            forecastId=p8["id"], scenario=SCENARIO, baseTime=p8["baseTime"],
            history=main._TimeRange(start=p8["history"]["start"], end=p8["history"]["end"]),
            forecast=main._TimeRange(start=p8["forecast"]["start"], end=p8["forecast"]["end"]))),
    ):
        assert call.get("code") == 40003, call
        assert "数据不足" in str(call.get("message"))

    # 预测来源方案：仿真必须绑定依据窗口与 AP
    plan = _data(plans())["plans"][0]
    assert main.plan_simulate(main.PlanSimulateRequest(
        plan_id=plan["id"], scenario=SCENARIO,
        window_start=fc[0], window_end=fc[1], parameter_values={})).get("code") == 40002
    assert main.plan_simulate(main.PlanSimulateRequest(
        plan_id=plan["id"], scenario=SCENARIO,
        window_start=hs, window_end=he, ap_id="AP-01", parameter_values={})).get("code") == 40002
    assert main.plan_simulate(main.PlanSimulateRequest(
        plan_id=plan["id"], scenario=SCENARIO,
        window_start=hs, window_end=he, parameter_values={})).get("code") == 0


def test_kpi_only_fault_has_consistent_state_and_span():
    """KPI 触发故障时，状态与区间必须与结论一致（审计 10-29 第 4 项）。

    真实反例：AP-01 01-06 10:12~10:13，6 行采样、标签全 normal、
    RSRP -106.07 / SINR -10.64 → 判覆盖故障，却返回 current_state=normal、spans=[]。
    """
    d = _data(main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start="2026-01-06 10:12:00", window_end="2026-01-06 10:13:00",
        ap_id="AP-01")))
    assert d["is_anomaly"] is True
    assert d["anomaly_spans"], "判故障却没有异常区间"
    assert d["current_state"] != "normal"
    assert d["last_anomaly_at"] is not None
    assert any(s.get("source") == "kpi" for s in d["anomaly_spans"])


def test_forecast_analyze_respects_enable_llm():
    """NETTUNE_ENABLE_LLM=0 时必须走规则研判，即使配了 Key（审计 10-29 六-6）。"""
    base, hs, he = "2026-01-02 14:12:00", "2026-01-02 13:12:00", "2026-01-02 14:12:00"
    prev = _data(main.forecast_preview(main.ForecastPreviewRequest(
        scenario=SCENARIO, baseTime=base, history=main._TimeRange(start=hs, end=he), horizonMinutes=120)))

    calls = {"n": 0}
    original_chat, original_enable = main.call_llm_chat, main.ENABLE_LLM
    original_key = main.llm_agent.LLM_API_KEY

    def fake_chat(*a, **kw):
        calls["n"] += 1
        return "【LLM】模型回复"

    def ask():
        return _data(main.forecast_analyze(main.ForecastAnalyzeRequest(
            forecastId=prev["id"], scenario=SCENARIO, baseTime=base, alertId=None,
            history=main._TimeRange(start=hs, end=he),
            forecast=main._TimeRange(start=prev["forecast"]["start"], end=prev["forecast"]["end"]))))

    try:
        main.call_llm_chat = fake_chat
        main.llm_agent.LLM_API_KEY = "dummy-key"

        main.ENABLE_LLM = False
        lost = ask()
        assert lost["source"] == "rule" and calls["n"] == 0, (lost["source"], calls["n"])

        calls["n"] = 0
        main.ENABLE_LLM = True
        got = ask()
        assert got["source"] == "llm" and calls["n"] == 1, (got["source"], calls["n"])
    finally:
        main.call_llm_chat = original_chat
        main.ENABLE_LLM = original_enable
        main.llm_agent.LLM_API_KEY = original_key


def test_forecast_id_is_unique_per_full_context():
    """预测标识必须唯一且确定性（审计 10-29 六-1）。

    原先：① 小数分钟（30.1/30.9）被 int() 成 30，两轮不同请求撞同一 ID；
         ② md5 截断 8 位，理论上可构造碰撞 → 合法旧结果被覆盖后无法研判。
    现在：只接受整数分钟；哈希用完整 32 位。
    """
    base, hs, he = "2026-01-02 14:12:00", "2026-01-02 13:12:00", "2026-01-02 14:12:00"

    def preview(h, start=hs, end=he):
        return main.forecast_preview(main.ForecastPreviewRequest(
            scenario=SCENARIO, baseTime=base,
            history=main._TimeRange(start=start, end=end), horizonMinutes=h))

    # 小数分钟 → 明确拒绝（不再静默取整）
    for bad in (30.1, 30.9):
        r = preview(bad)
        assert r.get("code") == 40002, (bad, r.get("code"))
        assert "整数分钟" in str(r.get("message"))

    # 整数正常，且同上下文可复现
    first = _data(preview(30))
    again = _data(preview(30))
    assert first["id"] == again["id"]
    # 完整哈希：ID 里带 32 位十六进制
    digest = first["id"].rsplit("_", 1)[-1]
    assert len(digest) == 32, digest
    # 不同历史范围 → 不同 ID；同基准不同时长 → 不同 ID
    assert _data(preview(30, "2026-01-02 13:28:00", "2026-01-02 14:06:00"))["id"] != first["id"]
    assert _data(preview(60))["id"] != first["id"]


def test_forecast_analyze_uses_question_and_target_window():
    """forecast/analyze 必须真正使用 question 与 targetWindow。

    实测缺陷：前端（forecast/api.ts）一直会发这两个字段，但
    ForecastAnalyzeRequest 没声明 → Pydantic 静默忽略 → 研判只能输出通用文案，
    "用户问什么"和"关注哪个时段"完全没参与研判。

    风险规避：只在**有值**时追加，未传时提示词与文案与旧版逐字节一致。
    """
    prev = main.forecast_preview(main.ForecastPreviewRequest(
        scenario=SCENARIO, baseTime="2026-01-02 14:12:00",
        history=main._TimeRange(start="2026-01-02 13:12:00", end="2026-01-02 14:12:00"),
        horizonMinutes=30))
    prev = _data(prev)
    fid = prev["id"]
    alert = (prev.get("alerts") or [{}])[0]
    base = dict(forecastId=fid, scenario=SCENARIO, baseTime="2026-01-02 14:12:00",
                alertId=alert.get("id"),
                history=main._TimeRange(start="2026-01-02 13:12:00", end="2026-01-02 14:12:00"),
                forecast=main._TimeRange(start=prev["forecast"]["start"], end=prev["forecast"]["end"]))

    # ① 不传：与旧行为一致（规则文案里没有"关于你的问题"，回显为空）
    plain = _data(main.forecast_analyze(main.ForecastAnalyzeRequest(**base)))
    assert "关于你的问题" not in plain["reply"]
    assert plain["question"] == "" and plain["targetWindow"] is None

    # ② 传：规则文案必须回应用户的问题与关注时段，并回显
    asked = _data(main.forecast_analyze(main.ForecastAnalyzeRequest(
        **base, question="这段会不会影响视频会议？",
        targetWindow=main._TimeRange(start="2026-01-02 14:15:00", end="2026-01-02 14:35:00"))))
    assert "这段会不会影响视频会议？" in asked["reply"]
    assert "14:15:00" in asked["reply"] and "14:35:00" in asked["reply"]
    assert asked["question"] == "这段会不会影响视频会议？"
    assert asked["targetWindow"] == {"start": "2026-01-02 14:15:00", "end": "2026-01-02 14:35:00"}

    # ③ LLM 提示词：用桩捕获 prompt，确认只在有值时注入
    captured = []
    orig_call, orig_key = main.call_llm_chat, llm_agent.LLM_API_KEY
    orig_enable = main.ENABLE_LLM

    def fake_call(system_prompt, user_message, **kwargs):
        captured.append(user_message)
        return "【桩】研判文字"

    try:
        main.call_llm_chat = fake_call
        llm_agent.LLM_API_KEY = "sk-test"
        # 本用例测的是"提示词注入"，需要显式开启 LLM：
        # 生产代码现在同时尊重 ENABLE_LLM（NETTUNE_ENABLE_LLM=0 时不调模型）。
        main.ENABLE_LLM = True
        main.forecast_analyze(main.ForecastAnalyzeRequest(**base))
        main.forecast_analyze(main.ForecastAnalyzeRequest(
            **base, question="会不会影响视频会议？",
            targetWindow=main._TimeRange(start="2026-01-02 14:15:00", end="2026-01-02 14:35:00")))
    finally:
        main.call_llm_chat, llm_agent.LLM_API_KEY = orig_call, orig_key
        main.ENABLE_LLM = orig_enable

    assert len(captured) == 2
    assert "用户的问题" not in captured[0], "未传问题时不应改变提示词"
    assert "用户的问题：会不会影响视频会议？" in captured[1]
    assert "用户关注时段：2026-01-02 14:15:00 ~ 2026-01-02 14:35:00" in captured[1]


def test_bare_time_range_routes_to_diagnosis():
    """只给一个时间区间、没有动作词时，应默认做"该时段研判"而不是闲聊。

    实测反馈：用户只输入 "01-01 10:00–11:59" 时，返回的是帮助文案
    「你好，我是 AI-NetTune 网络运维助手…」；而这块面板叫"问题研判与证据"，
    给一段时间最可能就是"分析这段时间"。
    """
    for i, msg in enumerate(("01-01 10:00–11:59", "01-01 10:00到11:59")):
        d = _data(main.agent_chat(main.ChatRequest(
            message=msg, session_id=f"bare-range-{i}", scenario=SCENARIO,
            window_start=WEAK_START, window_end=WEAK_END)))
        assert d["intent_debug"]["intent"] == "fault_diagnosis", \
            f"「{msg}」应走研判，实得 {d['intent_debug']['intent']}"
        assert d["reply"] and "AI-NetTune 网络运维助手" not in d["reply"], "不应返回帮助文案"

    # 真正的闲聊仍要走 chat，不能被这条规则吞掉
    chatter = _data(main.agent_chat(main.ChatRequest(
        message="你好", session_id="bare-range-chat", scenario=SCENARIO)))
    assert chatter["intent_debug"]["intent"] == "chat"


def test_time_range_accepts_various_dashes():
    """时间区间分隔符必须兼容各种破折号/连字符。

    实测问题：前端白名单只有 到|至|~|—|－|-，不认 en dash "–"（U+2013）
    与减号 "−"（U+2212）。用户从聊天里复制"01-01 10:00–11:59"会被判为
    "请用'到'明确起止时间"，问题直接发不出去。
    """
    expect = {"start": "01-01 10:00", "end": "01-01 11:59"}
    for sep in ("到", "至", "~", "—", "－", "–", "−", "-"):
        got = main.llm_agent.extract_time_range(f"01-01 10:00{sep}11:59")
        assert got == expect, f"分隔符 {sep!r} 解析失败，实得 {got}"


def test_forecast_horizon_independent():
    """同一基准下，重叠的未来时刻必须得到相同的预测值。

    实测缺陷：τ 与归一化峰值原先按请求的 horizonMinutes 计算，导致
    基准 14:12 的同一个 14:22 —— horizon=30 给 -102.25、horizon=120 给 -100.83，
    越阈时刻也从 14:33 变成 15:30。用户切换"预测时长"就会看到两套结果。
    """
    def value_at(horizon: int, clock: str) -> float:
        d = _preview("2026-01-02 14:12:00", minutes=horizon, history=60)
        spec = next(s for s in d["spectra"]
                    if s["observationId"] == "AP-03" and s["metric"] == "power")
        point = next(p for p in spec["predicted"] if p["start"].endswith(clock))
        return point["values"][0]

    for clock in ("14:22:00", "14:30:00"):
        short = value_at(30, clock)
        long = value_at(120, clock)
        assert abs(short - long) < 1e-9, \
            f"{clock} 在不同预测时长下应一致，实得 {short} vs {long}"


def test_intent_diagnosis_verbs_route_to_diagnosis():
    """"分析/排查/诊断/研判"应走研判（要结论与方案），但要指标时不抢。

    背景：原先这类问法落到 query_kpi，只给均值不给研判与方案；
    而"分析一下各 AP 的 RSRP"这种明显要数值的问法不能被改成研判。
    """
    cases = [
        ("分析一下 AP-01 在 2026-01-01 10:00 到 12:00 的情况", "fault_diagnosis"),
        ("排查一下 AP-01", "fault_diagnosis"),
        ("诊断 AP-02 的问题", "fault_diagnosis"),
        ("分析一下当前各AP的RSRP", "query_kpi"),
        ("看看当前各AP的RSRP和吞吐量", "query_kpi"),
        ("为什么 AP-01 这么差", "query_kpi"),
        ("画一下这个时间窗的热力图", "get_heatmap"),
        ("预测下一个小时的功率变化", "forecast"),
    ]
    for msg, want in cases:
        got = main.llm_agent.rule_intent(msg)["intent"]
        assert got == want, f"「{msg}」应走 {want}，实得 {got}"


def test_analyze_anomaly_ap_filter():
    """指定 AP 时只研判该 AP；未指定时行为与原来完全一致。

    实测缺陷：意图层抽出了 ap_id（intent_debug.parsed.ap_id = AP-01），
    但分发时不传 → 问 AP-01 得到的是"窗口内最差的 AP"。
    """
    base = dict(scenario=SCENARIO, window_start=WEAK_START, window_end=WEAK_END)

    # 该窗口（01-01 10:00~11:59）采样只落在 AP-02 上
    only = _data(main.analyze_anomaly(main.AnomalyRequest(**base, ap_id="AP-02")))
    assert only["affected_aps"] == ["AP-02"]
    assert only["ap_filter"] == "AP-02"

    # 指定一个该窗口没有采样的 AP → 明确回答"无采样"，而不是硬答别人的异常
    none = _data(main.analyze_anomaly(main.AnomalyRequest(**base, ap_id="AP-01")))
    assert none["is_anomaly"] is False
    assert none["affected_aps"] == []
    assert "无采样" in none["reason"]

    # 不指定 AP → 与改动前一致（整窗研判，AP-02 覆盖故障）
    default = _data(main.analyze_anomaly(main.AnomalyRequest(**base)))
    assert default["anomaly_type"] == "覆盖故障" and default["affected_aps"] == ["AP-02"]
    assert default.get("ap_filter") in (None, "")


def test_chat_respects_ap_id():
    """对话里点了 AP 就必须答那台，不能答成窗口内最差的 AP。"""
    def ask(msg: str) -> dict:
        return _data(main.agent_chat(main.ChatRequest(
            message=msg, session_id="ap-" + msg[:6], scenario=SCENARIO,
            window_start=WEAK_START, window_end=WEAK_END)))

    hit = ask("AP-02 在 2026-01-01 10:00 到 12:00 的情况")
    assert hit["intent_debug"]["parsed"]["ap_id"] == "AP-02"
    assert "AP-02" in hit["reply"] and "劣化" in hit["reply"]

    # 该窗口没有 AP-01 的采样 → 必须如实说没有，而不是把 AP-02 的异常当成 AP-01 的
    miss = ask("AP-01 在 2026-01-01 10:00 到 12:00 的情况")
    assert miss["intent_debug"]["parsed"]["ap_id"] == "AP-01"
    assert "未找到 AP-01" in miss["reply"], f"应明确无采样，实得：{miss['reply'][:60]}"
    assert "覆盖故障" not in miss["reply"]

    # 研判类问法：跨故障边界时，答该 AP 正常 + 补充同窗口其他 AP 异常
    diag = main.tool_fault_diagnosis(SCENARIO, "2026-01-01 09:30:00",
                                     "2026-01-01 10:30:00", "AP-01")
    assert diag["reply"].startswith("已按 AP-01 过滤")
    assert "未检测到明显异常" in diag["reply"]
    assert "补充：同窗口 AP-02" in diag["reply"]


def test_unknown_scenario_is_rejected_not_silent():
    """未知场景必须报 40001，而不是静默返回空数据。

    实测问题（唐苗检测报告 R01）：scenario=火星 时 topoology/basic 返回 code=0 + nodes=[]，
    前端无法区分"场景名写错"与"该窗口没数据"，会渲染空白页面。
    修法：数据访问层（filter_rows）抛 UnknownScenarioError → 全局映射 40001。
    同时区分"协议允许的场景"与"数据集实际覆盖的场景"（当前只有校园）。
    """
    # 数据访问层拦截
    try:
        main.filter_rows("火星", "2026-01-01 00:00:00", "2026-01-02 00:00:00")
        raise AssertionError("未知场景应抛 UnknownScenarioError")
    except main.UnknownScenarioError as exc:
        assert "火星" in str(exc)

    # 合法场景不受影响
    assert main.filter_rows(SCENARIO, "2026-01-01 10:00:00", "2026-01-01 10:59:59")

    # 健康检查如实区分两个概念
    cap = main.health()["data"]["capabilities"]
    assert cap["scenarios_supported"] == ["校园", "商场", "体育馆"]
    assert cap["scenarios_with_data"] == ["校园"], "数据集当前只有校园"


def test_filter_rows_index_equivalence():
    """filter_rows 的二分索引必须与逐行扫描**逐条一致**（顺序、对象、边界）。"""
    def linear(scenario, s, e, ap_id=None, user_id=None):
        rows = []
        for r in main.DF:
            if r["scenario"] != scenario:
                continue
            if ap_id and r["ap_id"] != ap_id:
                continue
            if user_id and r["user_id"] != user_id:
                continue
            if not (s <= r["timestamp"] <= e):
                continue
            rows.append(r)
        rows.sort(key=lambda x: x["timestamp"])
        return rows

    cases = [
        (SCENARIO, "2026-01-01 10:00:00", "2026-01-01 11:59:59", None, None),
        (SCENARIO, "2026-01-01 10:00:00", "2026-01-01 11:59:59", "AP-02", None),
        (SCENARIO, "2026-01-01 10:00:00", "2026-01-01 11:59:59", None, "UE-01"),
        (SCENARIO, "2026-01-01 10:00:00", "2026-01-01 10:00:00", None, None),   # 单分钟
        (SCENARIO, "2026-01-31 00:00:00", "2026-01-31 23:59:59", None, None),   # 无数据
    ]
    for h in range(24):     # 覆盖 24 个不同小时
        d = 1 + h % 30
        cases.append((SCENARIO, f"2026-01-{d:02d} {h % 24:02d}:00:00",
                      f"2026-01-{d:02d} 23:59:59", None, None))

    for sc, s, e, ap, uid in cases:
        old = [id(r) for r in linear(sc, s, e, ap, uid)]
        new = [id(r) for r in main.filter_rows(sc, s, e, ap, uid)]
        assert old == new, f"索引与线性扫描结果不一致：{sc} {s}~{e} ap={ap} uid={uid}"


def test_analyze_anomaly_cache_is_isolated():
    """研判短缓存：内容一致、深拷贝隔离、不同窗口/AP 不串。"""
    req = main.AnomalyRequest(scenario=SCENARIO, window_start=WEAK_START, window_end=WEAK_END)
    first = main.analyze_anomaly(req)
    second = main.analyze_anomaly(req)
    assert first["data"] == second["data"]
    assert first["data"] is not second["data"], "必须返回深拷贝，否则调用方会污染缓存"

    # 调用方就地改字典（agent_chat 会 setdefault("multi_turn")），不能影响后续缓存命中
    first["data"]["__polluted__"] = True
    assert "__polluted__" not in main.analyze_anomaly(req)["data"]

    # 不同窗口 / 不同 AP 过滤必须各自独立
    other_window = main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start="2026-01-01 10:00:00",
        window_end="2026-01-01 10:59:59"))
    assert other_window["data"] != second["data"]
    ap_filtered = main.analyze_anomaly(main.AnomalyRequest(
        scenario=SCENARIO, window_start=WEAK_START, window_end=WEAK_END, ap_id="AP-01"))
    assert ap_filtered["data"]["ap_filter"] == "AP-01"
    assert second["data"].get("ap_filter") in (None, "")


def test_report_id_is_unique():
    """同一秒内连续导出报告不能同名覆盖（原先 RPT_YYYYMMDD_HHMMSS 秒级）。"""
    def export():
        return (main.export_report(main.ReportRequest(
            scenario=SCENARIO, start_time=WEAK_START, end_time=WEAK_END,
            format="html", include_chart=False)).get("data") or {})["report_id"]

    a, b = export(), export()
    assert a != b, f"两次导出应得到不同 report_id，实得 {a}"
    assert a.startswith("RPT_") and len(a.split("_")[-1]) >= 4


def test_query_kpi_verdict_matches_data():
    """KPI 查询的结论文案必须与数据一致。

    实测问题：原实现无论数据多差都写"当前时段网络整体运行平稳"，
    在 RSRP=-111.2 dBm、SINR=-15.0 dB 的故障窗口里照样说"平稳"，与数字直接矛盾。
    """
    fault = main.tool_query_kpi(SCENARIO, WEAK_START, WEAK_END)["reply"]
    assert "当前时段运行平稳" not in fault, f"故障窗口不应说平稳：{fault[:60]}"
    assert "明显劣化" in fault and "弱覆盖采样占比" in fault

    normal = main.tool_query_kpi(SCENARIO, "2026-01-02 03:00:00", "2026-01-02 03:59:59")["reply"]
    assert "当前时段运行平稳" in normal, f"正常窗口应说平稳：{normal[:60]}"


def test_forecast_insufficient_data_messages_are_specific():
    """频谱未加载 与 基准越界 必须是两条不同的说明。

    实测问题：两句合并成「当前时刻超出频谱数据范围（频谱覆盖 2026-01-01 ~ 01-07）」，
    在"频谱表缺失 + baseTime 完全合法"时也这么报，把"文件没加载"误导成"时间选错了"；
    且范围是硬编码字符串，数据换了就过期。
    """
    def preview(base: str, hs: str):
        return _data(main.forecast_preview(main.ForecastPreviewRequest(
            scenario=SCENARIO, baseTime=base,
            history=main._TimeRange(start=hs, end=base), horizonMinutes=30)))

    # ① 真越界：说明基准时刻与真实覆盖范围（取 _SPEC，而非硬编码）
    out = preview("2026-01-08 10:00:00", "2026-01-08 09:00:00")
    assert out["status"] == "insufficient_data"
    assert "超出频谱数据覆盖范围" in out["message"]
    assert "2026-01-08 10:00:00" in out["message"], "应点名越界的基准时刻"
    assert main._SPEC["max_ts"].strftime("%Y-%m-%d %H:%M:%S") in out["message"], "范围应来自实际数据"

    # ② 频谱缺失：即便 baseTime 完全合法，也必须说"未加载"而不是"超出范围"
    saved = main._SPEC["df"]
    try:
        main._SPEC["df"] = None
        missing = preview("2026-01-02 14:10:00", "2026-01-02 13:12:00")
    finally:
        main._SPEC["df"] = saved
    assert missing["status"] == "insufficient_data"
    assert "未加载" in missing["message"] and "spectrum_7d.csv" in missing["message"]
    assert "超出频谱数据覆盖范围" not in missing["message"], "文件缺失时不能说成时刻越界"


def test_forecast_rejects_future_history():
    """历史窗口不得晚于基准（否则等于用未来观测预测过去）。

    实测缺陷：baseTime=13:30 却传 history=13:35~14:35 时，接口原先照常返回 ready。
    """
    base = main.ForecastPreviewRequest(
        scenario=SCENARIO, baseTime="2026-01-02 13:30:00",
        history=main._TimeRange(start="2026-01-02 13:35:00", end="2026-01-02 14:35:00"),
        horizonMinutes=30)
    r = main.forecast_preview(base)
    assert r["code"] != 0, "历史晚于基准应被拒绝"
    assert "不得晚于 baseTime" in r["message"]

    # 起止颠倒也要拒绝
    rev = main.forecast_preview(main.ForecastPreviewRequest(
        scenario=SCENARIO, baseTime="2026-01-02 13:30:00",
        history=main._TimeRange(start="2026-01-02 13:30:00", end="2026-01-02 12:30:00"),
        horizonMinutes=30))
    assert rev["code"] != 0 and "必须早于" in rev["message"]

    # 正常请求仍通过
    ok = main.forecast_preview(main.ForecastPreviewRequest(
        scenario=SCENARIO, baseTime="2026-01-02 13:30:00",
        history=main._TimeRange(start="2026-01-02 12:30:00", end="2026-01-02 13:30:00"),
        horizonMinutes=30))
    assert ok["code"] == 0 and ok["data"]["status"] == "ready"


def test_hour_slice_requires_per_ap_samples():
    """小时切片单独判异常时，必须按 (小时, AP) 检查样本量，而不是只看整小时总行数。

    实测缺陷：构造"某 AP 只有 1 条采样、其他 AP 凑够总行数"时，
    那条孤立采样仍被判成"覆盖故障/中等"。
    """
    one = [{"ap_id": "AP-09", "timestamp": "2026-01-02 10:00:00", "rsrp": -120.0,
            "sinr": -3.0, "throughput": 1.0, "prb_usage": 95.0, "packet_loss": 12.0,
            "network_status": "weak_coverage"}]
    res = main.judge_ap("AP-09", one, None)
    # judge_ap 本身没有样本量约束（那是上层职责），但上层过滤必须拦住它
    kept = [x for x in [res]
            if x["anomaly_type"] not in ("正常", "性能提示")
            and len(one) >= main.MIN_HOUR_ROWS_PER_AP]
    assert kept == [], "单行 AP 不应进入小时异常结论"
    assert main.MIN_HOUR_ROWS_PER_AP >= 5


def test_unified_fault_definition():
    """统一故障定义（docs/fault_definition.md v2）的关键行为。

    背景：研判原先只看网络表、预警只看频谱表，在故障边界窗口上结论相反
    （56 个边界窗口有 4 个相反）。统一后要求：
      ① 频谱维度越阈即可判故障（补上研判漏报）
      ② 两维都满足才升级为"严重"
      ③ 不属于主判据的"边缘弱用户"只能算提示级
      ④ 窗口按闭区间处理（否则会漏掉最后一分钟）
    """
    def run(start, end):
        return _data(main.analyze_anomaly(main.AnomalyRequest(
            scenario=SCENARIO, window_start=start, window_end=end)))

    # ① 频谱已越阈但标签还没到 → 必须报出来（改前是"正常"）
    early = run("2026-01-01 10:00:00", "2026-01-01 10:15:00")
    assert early["is_anomaly"] is True, "频谱维度已越阈，研判应报故障"
    assert early["anomaly_type"] == "覆盖故障"

    # ④ 闭区间：该窗口只有 20:18/19/20 三分钟达标，若切掉最后一分钟会漏判
    cong = run("2026-01-05 20:00:00", "2026-01-05 20:20:00")
    assert cong["anomaly_type"] == "网络拥塞", f"应判网络拥塞，实得 {cong['anomaly_type']}"

    # ② 整窗两维都越阈 → 严重
    both = run(WEAK_START, WEAK_END)
    assert both["anomaly_type"] == "覆盖故障" and both["level"] == "严重"

    # ③ 边缘弱用户（RSRP<-100 且吞吐<50）不在主判据内 → 只能算提示
    hint = run("2026-01-04 10:00:00", "2026-01-04 10:15:00")
    assert hint["is_anomaly"] is False, "边缘弱用户不应算故障"
    assert hint["current_state"] == "performance_warning"

    # 任何故障结论都必须带触发证据（含占比或频谱频段数）
    for d in (early, cong, both):
        assert d["kpi_evidence"], f"{d['anomaly_type']} 结论缺证据"
        metrics = {e["metric"] for e in d["kpi_evidence"]}
        assert metrics & {"weak_coverage_ratio", "congestion_ratio",
                          "spectrum_power_bands", "spectrum_occupancy_bands"}, \
            f"证据里缺少主判据指标，实得 {metrics}"


def test_anomaly_state_semantics():
    """四种状态必须能区分：当前故障 / 窗口内已恢复 / 性能提示 / 正常。

    队友反馈：默认分析"过去 N 分钟"，窗口里出现过异常 ≠ 此刻仍在故障；
    且"标签 normal 但指标偏低"既不该报故障，也不该被吞成"正常"。
    """
    def state(start, end):
        return _data(main.analyze_anomaly(main.AnomalyRequest(
            scenario=SCENARIO, window_start=start, window_end=end)))

    # 1) 当前仍在故障：窗口末尾仍在异常区间内
    active = state("2026-01-01 10:00:00", "2026-01-01 11:59:59")
    assert active["is_anomaly"] is True
    assert active["current_state"] == "fault_active", active["current_state"]
    assert active["last_anomaly_at"], "应给出最后一次异常时刻"

    # 2) 窗口内曾发生、末尾已恢复（故障 10:00~12:00，窗口 11:30~13:30）
    recovered = state("2026-01-01 11:30:00", "2026-01-01 13:30:00")
    assert recovered["is_anomaly"] is True, "窗口内确实出现过故障"
    assert recovered["current_state"] == "recovered_in_window", recovered["current_state"]
    assert recovered["anomaly_spans"], "应保留异常区间用于展示"

    # 3) 正常窗口
    normal = state("2026-01-02 10:17:41", "2026-01-02 11:17:41")
    assert normal["current_state"] == "normal"
    assert normal["performance_hints"] == []

    # 4) 性能提示：整窗标签 normal，但过半采样 SINR<5（01-01 02:00 实测）
    hint = state("2026-01-01 02:00:00", "2026-01-01 03:00:00")
    assert hint["is_anomaly"] is False, "性能提示不应算作异常"
    if hint["current_state"] == "performance_warning":
        assert hint["performance_hints"], "标了性能提示就必须给出提示项"
        item = hint["performance_hints"][0]
        assert item["metric"] == "sinr_low_share"
        assert 0.0 < item["value"] <= 1.0
        assert item["cause"] and item["suggestion"]


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


def test_spatial_frames_share_extent():
    """空间序列的所有帧必须共用同一坐标范围（同一格子 = 同一物理位置）。

    实测缺陷：原先每帧各自按当帧采样点算范围，X 从 14.3~494.3 变到 1.6~484.1，
    逐格点趋势外推就作用在不同物理位置上，序列图的"扩张"不成立。
    """
    d = _data(main.spatial_frame_series(
        scenario=SCENARIO, baseTime="2026-01-02 10:08:00", historyMinutes=90,
        frameMinutes=10, pastFrames=8, futureFrames=6, metric="rsrp"))
    extent = (round(d["x_range"][0], 3), round(d["x_range"][1], 3),
              round(d["y_range"][0], 3), round(d["y_range"][1], 3))
    # 顶层范围应等于场景固定范围（响应里保留 1 位小数）
    scene = main._scene_extent(SCENARIO)
    assert scene is not None
    assert extent == (round(scene[0], 1), round(scene[1], 1),
                      round(scene[2], 1), round(scene[3], 1)), \
        f"顶层范围应取场景固定范围，实得 {extent} vs {scene}"

    # 用同一 extent 重建任意两帧，范围必须完全一致
    import spatial_frames as spf
    from datetime import datetime, timedelta
    base = datetime(2026, 1, 2, 10, 8)
    ranges = []
    for i in range(2):
        end = base - timedelta(minutes=(7 - i) * 10)
        start = end - timedelta(minutes=10)
        built = spf.build_grid(
            main.filter_rows(SCENARIO, start.strftime(ISO), end.strftime(ISO)),
            "rsrp", extent=scene)
        ranges.append((tuple(round(v, 3) for v in built["x_range"]),
                       tuple(round(v, 3) for v in built["y_range"])))
    assert ranges[0] == ranges[1], f"各帧坐标范围应一致，实得 {ranges}"

    # 统一坐标后，"恶化区域扩张"仍应可见：未来末帧的空间落差 ≥ 当前帧
    def spread(frame):
        vals = [v for row in frame["values"] for v in row if v is not None]
        return max(vals) - min(vals)
    assert spread(d["future"][-1]) >= spread(d["past"][-1]) - 1.0


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


def test_forecast_congestion_alert_fires():
    """拥塞事件中途必须能报出预警。

    背景：预警要求"≥40/50 频段同时越阈"，而占用率阈值原为 85% 时，
    4 个真值拥塞事件的越阈频段数最多只有 39/37/37/39（差 1~3 个），
    拥塞预警**从未触发过**。阈值下调到 80% 后应能正常报出。
    """
    d = _preview("2026-01-01 20:25:00", minutes=30)
    assert d["status"] == "ready"
    alerts = d.get("alerts") or []
    assert alerts, "拥塞事件中途应报出预警"
    first = alerts[0]
    assert "拥塞" in first["title"], f"应为拥塞类预警，实得 {first['title']}"
    assert "80.0%" in first["summary"], f"摘要应体现新阈值，实得 {first['summary'][:80]}"

    # 正常时段不得误报（阈值下调的代价必须可控）
    for base in ("2026-01-02 10:34:00", "2026-01-04 09:00:00", "2026-01-06 15:00:00"):
        quiet = _preview(base, minutes=30)
        assert not (quiet.get("alerts") or []), f"{base} 属正常时段，不应预警"


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


def test_report_trend_extends_for_short_window():
    """短窗口报告：小时趋势向前补取数据，并明确标注"趋势参考区间"。

    背景：2 小时窗口只有 2 个小时点，趋势图没有信息量。改为向前补取最多 6 小时，
    同时必须标注这段是窗口外数据，其余章节仍对应所选窗口。
    """
    import os
    from data_layout import BASE_DIR
    reports_dir = Path(os.getenv("NETTUNE_REPORTS_DIR") or str(BASE_DIR / "reports"))

    def html_of(start: str, end: str) -> str:
        payload = main.export_report(main.ReportRequest(
            scenario=SCENARIO, start_time=start, end_time=end,
            format="html", include_chart=True)).get("data") or {}
        name = payload["file_url"].rstrip("/").split("/")[-1]
        return (reports_dir / name).read_text(encoding="utf-8")

    # ① 2 小时窗口 → 趋势段向前补取，折线点数明显多于 2，且带区间标注
    short = html_of("2026-01-02 08:00:00", "2026-01-02 09:59:59")
    seg = short.split("四、小时趋势")[-1][:2500]
    assert "趋势参考区间" in seg, "补取数据时必须标注参考区间"
    assert "其余章节严格对应所选窗口" in seg, "必须说明其余章节仍对应所选窗口"
    import re
    poly = re.search(r'points="([^"]+)"', seg)
    assert poly and len(poly.group(1).split()) >= 3, "补取后应画出可读的折线"

    # ② 6 小时窗口 → 不补取、不标注
    long_html = html_of("2026-01-01 10:00:00", "2026-01-01 15:59:59")
    seg_long = long_html.split("四、小时趋势")[-1][:2500]
    assert "趋势参考区间" not in seg_long, "窗口本身足够长时不应补取"


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
