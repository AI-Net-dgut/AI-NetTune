"""分析报告生成（Markdown / HTML）。

输入是一份"上下文快照"（由 main.py 的 export_report 组装），输出 (扩展名, 正文)。
设计要点：
  * 报告必须包含**研判结论 + 证据链 + 候选原因 + 分 AP 对比 + 趋势 + 方案**，
    不能只有几个均值（旧实现只有 4 行）；
  * `format=html` 输出真正的 HTML（旧实现只是把 .md 改名成 .html，打开是纯文本）；
  * `include_chart` 为真时，Markdown 用文本条形图、HTML 用内联 SVG（不依赖外部图片/网络）；
  * 口径与数据来源如实写在报告末尾，避免被当成实测结论。
"""

from __future__ import annotations

from html import escape

SPARK = "▁▂▃▄▅▆▇█"


def _fmt(value, unit="", digits=1):
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{unit}"
    return f"{value}{unit}"


def _pct(value):
    return "—" if value is None else f"{value * 100:.0f}%"


def _sparkline(values):
    """文本条形图（Markdown 用）。"""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return SPARK[0] * len(vals)
    return "".join(SPARK[min(7, int((v - lo) / (hi - lo) * 8))] for v in vals)


def _svg_trend(points, unit, title):
    """内联 SVG 折线图（HTML 用）。points: [(label, value)]

    数据点少于 3 个时**不画折线**：两个点必然连成一条贯穿全宽的斜线，
    看起来像"明确的下降趋势"，实际只是两点之差（实测报告里出现过
    「08 → 09」两小时被画成陡降线）。改为画散点并标注点数不足。
    """
    pts = [(label, v) for label, v in points if v is not None]
    if not pts:
        return ""
    width, height, pad_l, pad_b, pad_t = 660, 170, 52, 34, 18
    lo, hi = min(v for _l, v in pts), max(v for _l, v in pts)
    # 纵轴最小跨度：避免把 3~4 dB 的正常波动拉满整幅图而放大观感。
    # RSRP/SINR 这类 dB 量纲给 6 dB 下限，其它量纲按均值的 10% 兜底。
    if unit in ("dBm", "dB"):
        min_span = 6.0
    else:
        base = max(abs(lo), abs(hi), 1.0)
        min_span = base * 0.1
    if hi - lo < min_span:
        mid = (hi + lo) / 2
        lo, hi = mid - min_span / 2, mid + min_span / 2
    inner_w, inner_h = width - pad_l - 16, height - pad_b - pad_t

    def px(i):
        return pad_l + (inner_w * i / (len(pts) - 1) if len(pts) > 1 else inner_w / 2)

    def py(v):
        return pad_t + inner_h * (1 - (v - lo) / (hi - lo))

    poly = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, (_l, v) in enumerate(pts))
    grid, labels = [], []
    for k in range(4):
        val = lo + (hi - lo) * k / 3
        y = py(val)
        grid.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - 16}" y2="{y:.1f}" '
                    f'stroke="#eaf0f7" stroke-width="1"/>')
        labels.append(f'<text x="{pad_l - 8}" y="{y + 3:.1f}" text-anchor="end" '
                      f'font-size="10" fill="#607995">{val:.1f}</text>')
    # 刻度去重：len(pts)==2 时 (0, 1, 1) 会把最后一个标签画两遍（同一位置叠字）
    ticks = []
    for i in sorted({0, len(pts) // 2, len(pts) - 1}):
        label = pts[i][0]
        ticks.append(f'<text x="{px(i):.1f}" y="{height - 12}" text-anchor="middle" '
                     f'font-size="10" fill="#607995">{escape(str(label))}</text>')
    # 少于 3 个点时只画散点 + 明确提示，不画折线（两点连线会被误读成趋势）
    if len(pts) >= 3:
        series_svg = f'<polyline fill="none" stroke="#2e69d2" stroke-width="2" points="{poly}"/>'
    else:
        dots = "".join(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="3.5" fill="#2e69d2"/>'
                       for i, (_l, v) in enumerate(pts))
        series_svg = (dots
                      + f'<text x="{width - 16}" y="{pad_t + 10}" text-anchor="end" font-size="10" '
                        f'fill="#a06a1c">仅 {len(pts)} 个数据点，不足以判断趋势</text>')
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="{escape(title)}" style="background:#fff">'
        + "".join(grid)
        + series_svg
        + "".join(labels) + "".join(ticks)
        + f'<text x="{pad_l}" y="12" font-size="10" fill="#607995">'
          f'{escape(title)}（{escape(unit)}）</text></svg>'
    )


def _md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def _sections(ctx):
    """把上下文整理成结构化区块，供 Markdown / HTML 两种渲染共用。"""
    d = ctx["diagnosis"] or {}
    hourly = ctx.get("hourly_trend") or []
    comparison = ctx.get("ap_comparison") or []
    alarms = ctx.get("alarm_history") or []
    candidates = d.get("candidates") or []
    plans = ctx.get("plans") or []

    overview_rows = [
        ["采样条数", ctx["sample_count"], "条"],
        ["RSRP 均值", _fmt(ctx["means"].get("rsrp")), "dBm"],
        ["SINR 均值", _fmt(ctx["means"].get("sinr")), "dB"],
        ["吞吐量均值", _fmt(ctx["means"].get("throughput")), "Mbps"],
        ["PRB 利用率均值", _fmt(ctx["means"].get("prb_usage")), "%"],
        ["丢包率均值", _fmt(ctx["means"].get("packet_loss"), digits=2), "%"],
        ["时延均值", _fmt(ctx["means"].get("latency")), "ms"],
    ]

    evidence_rows = [[e.get("metric", ""), _fmt(e.get("avg_value")), e.get("threshold", ""),
                      e.get("status", "")] for e in (d.get("kpi_evidence") or [])]

    comparison_rows = [[a.get("ap_id", ""), _fmt(a.get("rsrp_avg")), _fmt(a.get("sinr_avg")),
                        _fmt(a.get("thr_avg")), _fmt(a.get("prb_avg")),
                        _pct(a.get("weak_ratio")), _pct(a.get("cong_ratio")),
                        f'{a.get("anomaly_type", "")}（{a.get("level", "")}）']
                       for a in comparison]

    trend_rows = [[h.get("hour", ""), _fmt(h.get("rsrp_avg")), _fmt(h.get("sinr_avg")),
                   _fmt(h.get("thr_avg")), _fmt(h.get("prb_avg")),
                   f'{h.get("anomaly_type", "")}'] for h in hourly]

    alarm_rows = [[a.get("time", ""), a.get("ap_id", ""), a.get("anomaly_type", ""),
                   a.get("level", ""), a.get("cause", "")] for a in alarms[:10]]

    plan_rows = []
    for p in plans:
        sim = p.get("simulation") or {}
        params = "；".join(f'{q["label"]} {q["currentValue"]}→{q["suggestedValue"]}{q["unit"]}'
                           for q in (p.get("parameters") or []))
        plan_rows.append([p.get("name", ""), params or "—",
                          sim.get("summary") or "未提供仿真结果"])

    return {
        "overview": overview_rows,
        "evidence": evidence_rows,
        "comparison": comparison_rows,
        "trend": trend_rows,
        "alarms": alarm_rows,
        "plans": plan_rows,
        "candidates": candidates,
        "diagnosis": d,
    }


def build_markdown(ctx, include_chart=True):
    s = _sections(ctx)
    d = s["diagnosis"]
    L = []
    L.append("# AI-NetTune 网络分析报告")
    L.append("")
    L.append(f"- 报告编号：{ctx['report_id']}")
    L.append(f"- 生成时间：{ctx['generated_at']}（北京时间）")
    L.append(f"- 场景：{ctx['scenario']}")
    L.append(f"- 分析范围：{ctx['start']} ~ {ctx['end']}（北京时间）")
    L.append(f"- 数据来源：{ctx['source_note']}")
    L.append("")

    L.append("## 一、数据概览")
    L.append("")
    L.append(_md_table(["项目", "数值", "单位"], s["overview"]))
    L.append("")

    L.append("## 二、研判结论")
    L.append("")
    if d.get("is_anomaly"):
        L.append(f"- **结论**：{d.get('anomaly_type', '')}（严重程度：{d.get('level', '')}）")
        L.append(f"- **受影响 AP**：{('、'.join(d.get('affected_aps') or [])) or '—'}")
        L.append(f"- **判断依据**：{d.get('cause', '')}")
        L.append(f"- **处置建议**：{d.get('suggestion', '')}")
    else:
        L.append(f"- **结论**：未检测到明显异常")
        L.append(f"- **说明**：{d.get('cause') or '各项指标均在正常范围'}")
    L.append("")
    if s["evidence"]:
        L.append("### 证据链（越过研判阈值的指标）")
        L.append("")
        L.append(_md_table(["指标", "均值", "阈值", "判定"], s["evidence"]))
        L.append("")
    if s["candidates"]:
        L.append("### 候选原因（按规则评分排序，未经现场核实）")
        L.append("")
        for c in s["candidates"]:
            L.append(f"{c.get('rank', '-')}. **{c.get('cause', '')}**"
                     f"（规则评分 {c.get('confidence', '-')}，非概率）")
            for item in (c.get("support_evidence") or []):
                L.append(f"   - 支持：{item}")
            for item in (c.get("against_evidence") or []):
                L.append(f"   - 反证：{item}")
            for item in (c.get("pending_checks") or []):
                L.append(f"   - 待核查：{item}")
        L.append("")

    L.append("## 三、分 AP 对比")
    L.append("")
    L.append(_md_table(["AP", "RSRP(dBm)", "SINR(dB)", "吞吐(Mbps)", "PRB(%)",
                        "弱覆盖占比", "拥塞占比", "研判"], s["comparison"]))
    L.append("")

    L.append("## 四、小时趋势")
    L.append("")
    L.append(_md_table(["小时", "RSRP(dBm)", "SINR(dB)", "吞吐(Mbps)", "PRB(%)", "研判"],
                       s["trend"]))
    L.append("")
    if include_chart:
        series = [(h.get("hour", "")[-5:], h.get("rsrp_avg")) for h in (ctx.get("hourly_trend") or [])]
        spark = _sparkline([v for _l, v in series])
        if spark:
            L.append("```text")
            L.append("RSRP 小时趋势（纵轴为相对高低，非等距刻度）")
            L.append(spark)
            vals = [v for _l, v in series if v is not None]
            L.append(f"最低 {min(vals):.1f} dBm / 最高 {max(vals):.1f} dBm")
            L.append("```")
            L.append("")
    if len(ctx.get("hourly_trend") or []) < 3:
        L.append(f"> 注：窗口内只有 {len(ctx.get('hourly_trend') or [])} 个小时级数据点，"
                 f"不足以判断趋势；如需趋势请把查询窗口放长到 3 小时以上。")
        L.append("")

    if s["alarms"]:
        L.append("## 五、告警历史（最近 10 条）")
        L.append("")
        L.append(_md_table(["时刻", "AP", "类型", "程度", "原因"], s["alarms"]))
        L.append("")

    if s["plans"]:
        L.append("## 六、处理方案与预计效果")
        L.append("")
        L.append(_md_table(["方案", "参数建议", "预计效果"], s["plans"]))
        L.append("")
        L.append("> 方案效果为经验系数外推，不是实测；具体口径见 `docs/api.md` 接口 14。")
        L.append("")

    L.append("---")
    L.append("")
    L.append("## 附：口径与限制")
    L.append("")
    L.append("- 数据来源为**仿真数据集**，不是现网实测；结论用于演示与流程验证。")
    L.append("- 研判由确定性规则引擎输出（非大模型生成），候选原因**未经现场核实**。")
    L.append("- 规则评分是规则命中强度的相对评分，**不代表发生概率**。")
    L.append("- 本报告不含预测预警内容；预测结果请在「预测预警」弹窗查看，并注意其可信边界。")
    L.append("")
    return "\n".join(L) + "\n"


def build_html(ctx, include_chart=True):
    s = _sections(ctx)
    d = s["diagnosis"]

    def table(headers, rows):
        head = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>"
            for row in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    parts = []
    parts.append("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                 "<title>AI-NetTune 网络分析报告</title><style>"
                 "body{font-family:'Microsoft YaHei',Arial,sans-serif;max-width:900px;"
                 "margin:32px auto;padding:0 20px;color:#22303f;line-height:1.65}"
                 "h1{border-bottom:3px solid #2e69d2;padding-bottom:10px}"
                 "h2{margin-top:32px;color:#1b4f9c}table{border-collapse:collapse;width:100%;"
                 "margin:12px 0;font-size:14px}th,td{border:1px solid #dbe5f0;padding:7px 10px;"
                 "text-align:left}th{background:#f2f7fd}code{background:#f4f6f9;padding:1px 4px}"
                 ".meta{color:#5a6b7d;font-size:14px}.note{background:#f7fafd;border-left:4px solid "
                 "#2e69d2;padding:10px 14px;color:#42536a;font-size:14px}</style></head><body>")

    parts.append("<h1>AI-NetTune 网络分析报告</h1>")
    parts.append(
        f'<p class="meta">报告编号：{escape(ctx["report_id"])}　|　生成时间：'
        f'{escape(ctx["generated_at"])}（北京时间）<br>场景：{escape(ctx["scenario"])}　|　'
        f'分析范围：{escape(ctx["start"])} ~ {escape(ctx["end"])}（北京时间）<br>'
        f'数据来源：{escape(ctx["source_note"])}</p>')

    parts.append("<h2>一、数据概览</h2>")
    parts.append(table(["项目", "数值", "单位"], s["overview"]))

    parts.append("<h2>二、研判结论</h2>")
    if d.get("is_anomaly"):
        parts.append(
            f"<p><strong>结论：</strong>{escape(str(d.get('anomaly_type', '')))}"
            f"（严重程度：{escape(str(d.get('level', '')))}）</p>"
            f"<p><strong>受影响 AP：</strong>"
            f"{escape('、'.join(d.get('affected_aps') or []) or '—')}</p>"
            f"<p><strong>判断依据：</strong>{escape(str(d.get('cause', '')))}</p>"
            f"<p><strong>处置建议：</strong>{escape(str(d.get('suggestion', '')))}</p>")
    else:
        parts.append("<p><strong>结论：</strong>未检测到明显异常</p>"
                     f"<p>{escape(str(d.get('cause') or '各项指标均在正常范围'))}</p>")
    if s["evidence"]:
        parts.append("<h3>证据链（越过研判阈值的指标）</h3>")
        parts.append(table(["指标", "均值", "阈值", "判定"], s["evidence"]))
    if s["candidates"]:
        parts.append("<h3>候选原因（按规则评分排序，未经现场核实）</h3><ol>")
        for c in s["candidates"]:
            parts.append(f"<li><strong>{escape(str(c.get('cause', '')))}</strong>"
                         f"（规则评分 {escape(str(c.get('confidence', '-')))}，非概率）")
            items = [("支持", c.get("support_evidence")), ("反证", c.get("against_evidence")),
                     ("待核查", c.get("pending_checks"))]
            groups = [(label, xs) for label, xs in items if xs]
            if groups:
                parts.append("<ul>")
                for label, xs in groups:
                    for item in xs:
                        parts.append(f"<li>{label}：{escape(str(item))}</li>")
                parts.append("</ul>")
            parts.append("</li>")
        parts.append("</ol>")

    parts.append("<h2>三、分 AP 对比</h2>")
    parts.append(table(["AP", "RSRP(dBm)", "SINR(dB)", "吞吐(Mbps)", "PRB(%)",
                        "弱覆盖占比", "拥塞占比", "研判"], s["comparison"]))

    parts.append("<h2>四、小时趋势</h2>")
    parts.append(table(["小时", "RSRP(dBm)", "SINR(dB)", "吞吐(Mbps)", "PRB(%)", "研判"],
                       s["trend"]))
    # 窗口短于 3 小时时只有 1~2 个小时点，趋势判断意义有限，必须说明清楚，
    # 否则读者会把"两小时的小幅下滑"当成明确趋势。
    if len(ctx.get("hourly_trend") or []) < 3:
        parts.append(f'<p style="color:#8a6a2a;font-size:12px">'
                     f'注：窗口内只有 {len(ctx.get("hourly_trend") or [])} 个小时级数据点，'
                     f'不足以判断趋势；如需趋势请把查询窗口放长到 3 小时以上。</p>')
    if include_chart:
        series = [(str(h.get("hour", ""))[-5:], h.get("rsrp_avg"))
                  for h in (ctx.get("hourly_trend") or [])]
        svg = _svg_trend(series, "dBm", "RSRP 小时趋势")
        if svg:
            parts.append(svg)

    if s["alarms"]:
        parts.append("<h2>五、告警历史（最近 10 条）</h2>")
        parts.append(table(["时刻", "AP", "类型", "程度", "原因"], s["alarms"]))

    if s["plans"]:
        parts.append("<h2>六、处理方案与预计效果</h2>")
        parts.append(table(["方案", "参数建议", "预计效果"], s["plans"]))
        parts.append('<p class="note">方案效果为经验系数外推，不是实测；'
                     '具体口径见 docs/api.md 接口 14。</p>')

    parts.append('<h2>附：口径与限制</h2><div class="note"><ul>'
                 "<li>数据来源为<strong>仿真数据集</strong>，不是现网实测；结论用于演示与流程验证。</li>"
                 "<li>研判由确定性规则引擎输出（非大模型生成），候选原因<strong>未经现场核实</strong>。</li>"
                 "<li>规则评分是规则命中强度的相对评分，<strong>不代表发生概率</strong>。</li>"
                 "<li>本报告不含预测预警内容；预测结果请在「预测预警」弹窗查看。</li>"
                 "</ul></div>")
    parts.append("</body></html>")
    return "\n".join(parts)


def build_report(ctx, fmt="md", include_chart=True):
    """返回 (扩展名, 正文)。fmt: md / markdown / html。"""
    if fmt in ("md", "markdown"):
        return "md", build_markdown(ctx, include_chart)
    return "html", build_html(ctx, include_chart)
