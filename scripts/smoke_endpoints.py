"""16 个业务接口的 HTTP 冒烟脚本（正式版，可提交到仓库）。

用法：
    1) 先起后端：python -m uvicorn main:app --port 8000
    2) 另开终端：python scripts/smoke_endpoints.py
    3) 指定地址：python scripts/smoke_endpoints.py --base http://127.0.0.1:8021

为什么要有它（教训）：单元测试直接调用函数，测不出"路由绑定错"、
"接口新增校验后旧占位参数被拒"这类问题 —— 之前 _supported_range 被误插到
@app.post 与函数之间，49 个测试全绿，只有本脚本发现接口 2 返回的是元组。

设计要点：
- 12 号接口（预测研判）**不用占位 id**：先真的跑一次 preview，再拿返回的
  forecastId 调 analyze，验证的是真实链路而不是"随便给个 id 也能过"。
- 允许"业务码非 0 但属于预期"的用例，在 CASES 里显式标注 expect。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SCENARIO = "校园"
WINDOW = ("2026-01-01 10:00:00", "2026-01-01 11:59:59")


def q(text: str) -> str:
    return urllib.parse.quote(text)


def call(base: str, method: str, path: str, payload=None, timeout: int = 180):
    url = base + path
    try:
        if method == "GET":
            req = urllib.request.Request(url, method="GET")
        else:
            req = urllib.request.Request(
                url, data=json.dumps(payload, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}
    except Exception as exc:                     # 连接失败等
        return -1, {"error": str(exc)}


def build_cases(base: str):
    qs = "scenario=" + q(SCENARIO) + "&start_time=" + q(WINDOW[0]) + "&end_time=" + q(WINDOW[1])
    cases = [
        ("1 基础KPI查询", "GET", "/api/data/query?" + qs + "&limit=100", None, {0}),
        ("2 异常研判", "POST", "/api/analyze/anomaly",
         {"scenario": SCENARIO, "window_start": WINDOW[0], "window_end": WINDOW[1]}, {0}),
        ("3 自然语言对话", "POST", "/api/agent/chat",
         {"message": "分析一下 AP-02 在 2026-01-01 10:00 到 12:00 的情况", "scenario": SCENARIO}, {0}),
        ("4 报告导出", "POST", "/api/export/report",
         {"scenario": SCENARIO, "start_time": WINDOW[0], "end_time": WINDOW[1],
          "format": "html", "include_chart": True}, {0}),
        ("5 空间热力图", "GET", "/api/data/heatmap?" + qs, None, {0}),
        ("6 基础拓扑", "GET", "/api/network/topology/basic?" + qs, None, {0}),
        ("7 事件清单", "GET", "/api/data/events?scenario=" + q(SCENARIO), None, {0}),
        ("8 预测结果查询", "GET", "/api/predict/result?scenario=" + q(SCENARIO)
         + "&start_time=" + q(WINDOW[0]) + "&end_time=" + q(WINDOW[1])
         + "&target_type=ap&metric=rsrp", None, {0}),
        ("9 拓扑增强", "GET", "/api/network/topology/enhanced?scenario=" + q(SCENARIO)
         + "&time=" + q("2026-01-01 10:00:00"), None, {0}),
        ("10 时序预测", "POST", "/api/analyze/forecast",
         {"scenario": SCENARIO, "window_start": WINDOW[0], "window_end": WINDOW[1]}, {0}),
        ("11 预测预警", "POST", "/api/forecast/preview",
         {"scenario": SCENARIO, "baseTime": "2026-01-01 10:08:00",
          "history": {"start": "2026-01-01 09:08:00", "end": "2026-01-01 10:08:00"},
          "horizonMinutes": 60}, {0}),
        ("13 部署自检", "GET", "/api/health", None, {0}),
        ("14 方案仿真", "POST", "/api/plan/simulate",
         {"plan_id": "PLACEHOLDER", "scenario": SCENARIO,
          "window_start": WINDOW[0], "window_end": WINDOW[1]}, {40002}),   # 占位方案 id → 预期参数错误
        ("15 空间帧序列", "GET", "/api/forecast/spatial_frames?scenario=" + q(SCENARIO)
         + "&baseTime=" + q("2026-01-01 10:08:00")
         + "&frameMinutes=10&pastFrames=8&futureFrames=6&metric=rsrp", None, {0}),
        ("16 信号导航", "GET", "/api/navigate/signal?" + qs + "&metric=rsrp", None, {0}),
    ]

    # 12 号：先真跑一次 preview，再拿真实 forecastId 做研判（验证真实链路）
    hist = ("2026-01-01 09:08:00", "2026-01-01 10:08:00")
    base_time = "2026-01-01 10:08:00"
    _, prev = call(base, "POST", "/api/forecast/preview",
                   {"scenario": SCENARIO, "baseTime": base_time,
                    "history": {"start": hist[0], "end": hist[1]}, "horizonMinutes": 60})
    prev_data = (prev or {}).get("data") or {}
    if prev_data.get("id"):
        cases.append(("12 预测研判（真实链路）", "POST", "/api/forecast/analyze",
                      {"forecastId": prev_data["id"], "scenario": SCENARIO, "baseTime": base_time,
                       "alertId": None,
                       "history": {"start": hist[0], "end": hist[1]},
                       "forecast": {"start": prev_data["forecast"]["start"],
                                    "end": prev_data["forecast"]["end"]}}, {0}))
    else:
        cases.append(("12 预测研判（无可用预测记录）", "POST", "/api/forecast/analyze",
                      {"forecastId": "missing", "scenario": SCENARIO, "baseTime": base_time,
                       "history": {"start": hist[0], "end": hist[1]},
                       "forecast": {"start": base_time, "end": base_time}}, {40003}))

    # 17 号：预测风险→处置方案（此前冒烟只覆盖 16 个业务接口，缺这一条）
    if prev_data.get("id"):
        cases.append(("17 预测处置方案", "POST", "/api/forecast/plans",
                      {"forecastId": prev_data["id"], "scenario": SCENARIO, "baseTime": base_time,
                       "history": {"start": hist[0], "end": hist[1]},
                       "forecast": {"start": prev_data["forecast"]["start"],
                                    "end": prev_data["forecast"]["end"]}}, {0}))
    else:
        cases.append(("17 预测处置方案（无可用记录）", "POST", "/api/forecast/plans",
                      {"forecastId": "missing", "scenario": SCENARIO, "baseTime": base_time,
                       "history": {"start": hist[0], "end": hist[1]},
                       "forecast": {"start": base_time, "end": base_time}}, {40003}))
    return cases


def main() -> int:
    ap = argparse.ArgumentParser(description="AI-NetTune 接口冒烟")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="后端地址")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    status, _ = call(base, "GET", "/api/health", timeout=15)
    if status != 200:
        print(f"后端不可用（{base}）：请先启动 uvicorn")
        return 2

    cases = build_cases(base)
    print(f"{'接口':<26} {'HTTP':>5} {'业务码':>7} {'耗时':>8}  说明")
    print("-" * 78)
    ok = fail = 0
    for name, method, path, payload, expect in cases:
        t0 = time.time()
        status, body = call(base, method, path, payload)
        cost = (time.time() - t0) * 1000
        code = (body or {}).get("code", "?")
        good = status == 200 and code in expect
        if good:
            ok += 1
        else:
            fail += 1
        note = "" if good else f"预期业务码 {sorted(expect)}，实得 {code}"
        print(f"{name:<26} {status:>5} {str(code):>7} {cost:>7.0f}ms  {note}")
    print("-" * 78)
    print(f"通过 {ok} / {len(cases)}，失败 {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
