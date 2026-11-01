# API 接口文档
Base URL: http://localhost:8000
数据格式: JSON (UTF-8)
时间格式: "yyyy-MM-dd HH:mm:ss"
单位: rsrp(dBm) sinr(dB) throughput(Mbps) latency(ms) packet_loss(%) prb_usage(%)

## 接口总览
|接口|方法|路径|用途|
|---|---|---|---|
|1|GET|/api/data/query|查询监测数据|
|2|POST|/api/analyze/anomaly|多 KPI 联合故障研判|
|3|POST|/api/agent/chat|自然语言对话|
|4|POST|/api/export/report|导出分析报告|
|5|GET|/api/data/heatmap|信号热力图数据|
|6|GET|/api/network/topology/basic|基础网络拓扑数据|
|7|GET|/api/data/events|故障事件清单（来自 data/fault_events.csv，案例起点的单一事实来源）|
|8|GET|/api/predict/result|时序预测结果查询（契约 v1 · **占位**：固定返回 predict_list=[] / model=placeholder，不调用任何模型）|
|9|GET|/api/network/topology/enhanced|拓扑增强数据（契约v1）|
|10|POST|/api/analyze/forecast|区域信号/频谱态势预测（Prophet 时序模型）|
|11|POST|/api/forecast/preview|预测预览（频谱预测预警 v2，baseTime 防未来）|
|12|POST|/api/forecast/analyze|预测研判（基于同一轮预测的文字分析，v2）|
|13|GET|/api/health|部署自检（数据文件/依赖/大模型就绪状态，不依赖数据文件、永远可访问）|
|14|POST|/api/plan/simulate|方案对比仿真（改参数后在同一窗口重算"原配置 vs 方案"）|
|15|GET|/api/forecast/spatial_frames|区域电磁态势帧序列（过去N帧 + 未来N帧，二维网格场）|
|16|GET|/api/navigate/signal|信号导航建议（位置感知 + 覆盖分析 + 移动方向/距离/预期增益）|
|17|POST|/api/forecast/plans|预测风险→处置方案（方案带 forecastId / 风险时段 / 基准前评估窗口与免责说明；无风险时返回空列表）|

## 通用响应格式
成功: {"code": 0, "message": "success", "data": {...}}
失败: {"code": 40001, "message": "参数错误：缺少scenario", "data": null}

> **成功判据（唯一判据，务必按此实现客户端）**：
> **HTTP 200 + `code == 0` 才算成功；`code != 0` 一律视为失败**，读取 `message` 展示原因。
> 不要把 HTTP 状态码当唯一依据 —— 部分接口（如 `/api/network/topology/enhanced` 缺 `time`）
> 有意返回 **HTTP 200 + `code=40001`**，以保持"统一响应包络"；仅参数格式在路由层就被拒时
> 才会出现 HTTP 4xx（如非法时间格式返回 HTTP 400 + `code=40001`）。
> 前端实现见 `frontend/src/services/backend.ts` 的 `request()`：`if (envelope.code !== 0) throw ...`。


错误码约定:
|错误码|类型|说明|
|---|---|---|
|0|成功|请求处理成功|
|40001|参数错误|必填参数缺失|
|40002|参数错误|参数格式非法|
|40003|参数错误|参数值超出合法范围|
|50001|服务端错误|服务内部异常|




## 接口1：查询监测数据
GET /api/data/query?scenario=校园&start_time=2026-09-28 00:00:00&end_time=2026-09-28 23:59:59&ap_id=AP-01&limit=100
可选参数: metrics=rsrp,sinr,throughput（不传则返回全部KPI列）
 offset: 分页偏移量，默认0，从第0条开始
limit: 单页最大条数，默认100，最大1000
 metrics: 返回字段裁剪，逗号分隔，如 rsrp,sinr,throughput；不传则返回全部18列


响应:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 120,
    "columns": ["timestamp", "ap_id", "user_id", "rsrp", "sinr", "throughput", "network_status"],
    "records": [
      {"timestamp": "2026-09-28 10:00:00", "ap_id": "AP-01", "user_id": "UE-001", "rsrp": -95, "sinr": 8.5, "throughput": 12.3, "network_status": "normal"},
      {"timestamp": "2026-09-28 10:00:05", "ap_id": "AP-01", "user_id": "UE-001", "rsrp": -105, "sinr": 3.2, "throughput": 4.1, "network_status": "congestion"}
    ]
  }
}
```
说明：`total` 为符合筛选条件的总记录数，不受分页参数影响，用于前端计算总页数。

## 接口 2：多 KPI 联合故障研判

POST /api/analyze/anomaly

请求:

```json
{
  "scenario": "校园",
  "window_start": "2026-01-01 00:00:00",
  "window_end": "2026-01-03 23:59:59",
  "metrics": ["rsrp", "sinr", "throughput", "prb_usage"]
}
```

响应（v2：含多候选原因 \+ 证据链）:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "is_anomaly": true,
    "anomaly_type": "覆盖故障",
    "level": "严重",
    "confidence": 0.86,
    "kpi_evidence": [
      {"metric": "rsrp", "avg_value": -115.8, "threshold": -105, "status": "异常"}
    ],
    "cause": "AP-02弱覆盖采样占比79%、RSRP均值-115.8dBm，判定为覆盖空洞",
    "suggestion": "建议检查AP天线倾角/功率或增加补盲AP",
    "affected_aps": ["AP-02"],
    "network_status": "weak_coverage",
    "candidates": [
      {
        "rank": 1,
        "cause": "AP-02天线倾角/发射功率不足，造成覆盖空洞",
        "confidence": 0.76,
        "support_evidence": ["弱覆盖采样占比79%超过30%阈值", "RSRP均值-115.8dBm低于-105dBm"],
        "against_evidence": ["SINR均值-8.3dB也偏低，干扰因素暂不能排除"],
        "pending_checks": ["现场核查天线倾角与发射功率配置"]
      },
      {
        "rank": 2,
        "cause": "AP-02周边新增遮挡物/环境变化导致信号衰减",
        "confidence": 0.53,
        "support_evidence": ["故障呈时段性出现，与遮挡场景变化特征相符"],
        "against_evidence": ["拥塞采样占比0%较低，非负荷因素"],
        "pending_checks": ["现场勘查周边遮挡物或建筑变更"]
      },
      {
        "rank": 3,
        "cause": "AP-02邻区干扰或负荷牵引导致覆盖感知差",
        "confidence": 0.38,
        "support_evidence": ["PRB利用率42.5%正常，干扰证据有限"],
        "against_evidence": ["RSRP均值-115.8dBm与SINR-8.3dB组合更指向覆盖问题"],
        "pending_checks": ["核查邻区PRB占用与切换参数"]
      }
    ],
    "ap_candidates": [
      {"ap_id": "AP-02", "candidates": [{"rank": 1, "cause": "...", "confidence": 0.76, "support_evidence": [...], "against_evidence": [...], "pending_checks": [...]}]}
    ],
    "hourly_trend": [
      {"hour": "2026-01-01 10", "anomaly_type": "覆盖故障", "level": "中等",
       "network_status": "weak_coverage", "rsrp_avg": -101.1, "sinr_avg": 10.1, "thr_avg": 408.3, "prb_avg": 57.1}
    ],
    "ap_comparison": [
      {"ap_id": "AP-01", "rsrp_avg": -99.9, "sinr_avg": 10.4, "thr_avg": 391.5, "prb_avg": 52.2,
       "weak_ratio": 0.326, "cong_ratio": 0.027, "anomaly_type": "覆盖故障", "level": "中等"}
    ],
    "alarm_history": [
      {"time": "2026-01-01 23", "ap_id": "AP-01", "anomaly_type": "覆盖故障",
       "level": "中等", "cause": "AP-01弱覆盖采样占比48%、RSRP均值-106.2dBm，判定为覆盖空洞"}
    ]
  }
}
```

### 接口 2 字段说明（v2 新增）

|字段|类型|说明|
|---|---|---|
|candidates|array|主异常 AP 的多候选原因（rank1 即主结论）。每项含 cause /confidence/support\_evidence（支持证据）/against\_evidence（反对证据）/pending\_checks（待验证项）|
|ap\_candidates|array|所有异常 AP 的证据链汇总，每项 \{ap\_id, candidates\}，供前端证据链图按 AP 展开|
|hourly\_trend|array|小时级趋势（回答 "何时开始变差"），每项含 hour \+ 该小时最严重异常的类型 / 等级 / KPI 均值|
|ap\_comparison|array|多 AP 横向对比（哪台 AP 最差），含 weak\_ratio/cong\_ratio|
|alarm\_history|array|小时 ×AP 告警历史（倒序，最多 50 条）|
|kpi\_evidence|array|命中的 KPI 异常证据（metric/avg\_value/threshold/status）|

## 接口 3：自然语言对话

POST /api/agent/chat

请求:

```json
{
  "message": "帮我看看10点到10点10分网络有没有问题",
  "session_id": "sess_001",
  "scenario": "校园"
}
```

响应:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "reply": "10:00-10:10时段检测到异常：RSRP均值-108dBm低于正常阈值，吞吐量仅2.3Mbps，判定为覆盖恶化（置信度0.87），建议检查AP-01天线。",
    "intent": "anomaly_query",
    "chart": {
      "type": "line",
      "option": {
        "xAxis": {"data": ["10:00", "10:02", "10:04", "10:06", "10:08", "10:10"]},
        "series": [{"name": "RSRP", "data": [-95, -101, -108, -110, -106, -102]}]
      }
    },
    "query_ref": {"scenario": "校园", "start_time": "2026-09-28 10:00:00", "end_time": "2026-09-28 10:10:00"}
  }
}
```

## 接口 4：导出分析报告

POST /api/export/report

请求:

```json
{
  "scenario": "校园",
  "start_time": "2026-01-01 10:00:00",
  "end_time": "2026-01-01 11:59:59",
  "format": "html",
  "include_chart": true
}
```

|字段|必填|说明|
|---|---|---|
|scenario / start_time / end_time|是|分析范围；`start_time` 不得晚于 `end_time`|
|format|否|`md` / `markdown` / `html`，默认 `md`；其它值返回 40002|
|include_chart|否|默认 `true`：Markdown 输出文本条形图，HTML 输出内联 SVG（不依赖外部图片）|

响应:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "report_id": "RPT_20260101_101500",
    "file_url": "/api/download/reports/RPT_20260101_101500.html",
    "format": "html",
    "include_chart": true,
    "sample_count": 360,
    "bytes": 6893
  }
}
```

### 报告内容（v2）

旧实现只写 4 行均值，且 `format=html` 时仅把扩展名改成 `.html`、内容仍是 Markdown（打开是纯文本）。
现在正文由 `report_model.py` 生成，两种格式各自渲染：

|章节|内容|
|---|---|
|一、数据概览|采样条数 + RSRP/SINR/吞吐/PRB/丢包/时延均值|
|二、研判结论|结论、受影响 AP、判断依据、处置建议；**证据链**表；**候选原因**（含支持/反证/待核查）|
|三、分 AP 对比|每 AP 的 RSRP/SINR/吞吐/PRB/弱覆盖占比/拥塞占比/研判|
|四、小时趋势|按小时的指标与研判 + 可选图表|
|五、告警历史|最近 10 条（时刻/AP/类型/程度/原因）|
|六、处理方案与预计效果|方案名、参数建议、仿真结论（复用接口 14 口径）|
|附：口径与限制|数据为仿真、研判为规则引擎、置信度不代表概率、报告不含预测预警|

> 研判部分直接复用接口 2 的结果，保证"报告口径 = 页面口径"。
> 该窗口没有采样时**不生成报告**，返回 40002 并说明原因（不产出 0 均值的假报告）。

### 下载路径变更（v2）

挂载点从 `/download/reports` 改为 **`/api/download/reports`**：
vite dev 的代理只代理 `/api` 前缀，旧路径在开发模式下会 404（前端此前也从未调用过该接口）。

### 报告目录

默认 `<项目根>/reports`，可用环境变量 `NETTUNE_REPORTS_DIR` 指到别处
（安装在 Program Files 或只读容器时必需）。目录不可写时返回 50001 并带上目标路径，不抛 500 堆栈。

## 接口 5：信号热力图数据（含空间估计）

GET /api/data/heatmap?scenario = 校园 \& start\_time=2026\-01\-01 00:00:00\&end\_time=2026\-01\-03 23:59:59\&metric=rsrp

可选参数:

- `start_time` / `end_time`：时间范围，不传则覆盖全量数据

- `metric`：热力指标，可选 `rsrp` / `sinr`，默认 `rsrp`

响应（method=kriging 时为空间估计升级版；采样点 <30 或拟合失败自动降级 method=grid_mean，字段结构一致）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "metric": "rsrp",
    "method": "kriging",
    "grid_count": 504,
    "values": [
      [30, 30, -102.5],
      [50, 30, -101.4],
      [70, 30, -100.97]
    ],
    "variance": [
      [30, 30, 44.08],
      [50, 30, 43.37],
      [70, 30, 44.32]
    ],
    "sample_points": 129600
  }
}
```

### 接口 5 字段说明

|字段|类型|说明|
|---|---|---|
|metric|string|本次热力图的指标类型|
|method|string|预测方法：`kriging`（OrdinaryKriging 空间插值，估计未测位置指标）；采样点 <30 或拟合失败时为 `grid_mean`（旧逻辑，仅输出有采样点的网格均值）|
|grid\_count|int|网格点位总数。kriging 为全区域网格（504=24×21，20m 网格）；grid_mean 为有采样点的格子数|
|values|array|网格点位数组，每项格式为 `[x坐标, y坐标, 指标预测值]`，可直接接入 ECharts heatmap 渲染|
|variance|array \| null|kriging 时与 values 等长的方差数组 `[x, y, 方差]`，即插值置信度（数值越小越可信）；grid_mean 时为 null|
|sample\_points|int|参与插值的 UE 采样点数|

## 接口 6：基础网络拓扑数据

GET /api/network/topology/basic?scenario = 校园 \& timestamp=2026\-01\-01 00:00:00

可选参数:

- `timestamp`：指定时刻，不传则默认取第一条数据的时刻

响应:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "timestamp": "2026-01-01 00:00:00",
    "nodes": [
      {"id": "AP-01", "type": "ap", "x": 120, "y": 220},
      {"id": "UE-01", "type": "ue", "x": 94.8, "y": 192.62},
      {"id": "AP-02", "type": "ap", "x": 380, "y": 160},
      {"id": "UE-02", "type": "ue", "x": 372.32, "y": 129.2}
    ],
    "links": [
      {"source": "AP-01", "target": "UE-01", "rsrp": -92.5, "sinr": 10.2, "throughput": 128.5},
      {"source": "AP-02", "target": "UE-02", "rsrp": -91.8, "sinr": 11.5, "throughput": 135.2}
    ]
  }
}
```

### 接口 6 字段说明

|字段|类型|说明|
|---|---|---|
|timestamp|string|本次拓扑对应的时刻|
|nodes|array|节点列表，包含 AP 与 UE 两类；每项含 `id` / `type` / `x` / `y`|
|links|array|AP\-UE 连接链路列表；每项含两端节点 ID 及 `rsrp` / `sinr` / `throughput` 核心指标|

## 接口 7：故障事件清单

GET /api/data/events?scenario=校园

参数：

- `scenario`（**可选**）：按场景过滤；不传返回全部。若提供则必须在 `VALID_SCENARIOS` 白名单内，否则 `40002`。

> 更正（独立审计）：本节此前写成"实时增量监测数据"，带 `last_time` 参数、返回 `{total, columns, records}` 的 KPI 增量行 —— 与实现完全不符。接口 7 实际只读 `data/fault_events.csv`，返回**事件清单**，是预置案例窗口的单一事实来源。字段为 **snake_case**（下方以真实返回为准）。

响应：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "count": 8,
    "events": [
      {"event_id": "EV-001", "scenario": "校园", "ap_id": "AP-02", "fault_type": "weak_coverage",
       "start_time": "2026-01-01 10:00:00", "end_time": "2026-01-01 12:00:00",
       "ramp_minutes": 20.0, "strength": 12.0, "labeled_from_expect": ""}
    ]
  }
}
```

### 接口 7 字段说明

|字段|类型|说明|
|---|---|---|
|count|int|事件条数|
|events|array|事件数组（字段名与 CSV 表头一致，snake_case）|
|events[].event_id|string|事件编号，如 EV-001|
|events[].scenario|string|场景|
|events[].ap_id|string|故障 AP，如 AP-02|
|events[].fault_type|string|`weak_coverage` / `congestion`|
|events[].start_time / end_time|string|事件起止（yyyy-MM-dd HH:mm:ss）|
|events[].ramp_minutes|number|渐变时长（分钟），可能为 null|
|events[].strength|number|注入强度，可能为 null|
|events[].labeled_from_expect|string|标注来源说明，可为空|
|note|string|仅当 `fault_events.csv` 缺失时出现：提示用 `data_generator.py` 重新生成（此时 `count=0`，仍返回 `code=0`，不让工作台打不开）|

**错误码说明**：

- `0`：成功（无事件或文件缺失均为 `count=0`）

- `40002`：`scenario` 不在白名单

- `50001`：服务端内部异常（如 CSV 表头损坏无法解析）



> 说明（独立审计）：下面"降级机制 / chart 图表类型"两节属于**接口 3（/api/agent/chat）**，
> 之前被误放在本接口章节里，现已标注归属。

### 附：接口 3 的降级机制
1.  关键词规则优先匹配，不依赖LLM即可完成意图识别
2.  未命中关键词、输入模糊时，默认兜底为 `query_kpi`（KPI概况查询）；对与数据无关的问题兜底 `chat`
3.  LLM不可用时，自动降级为关键词规则+兜底逻辑，核心功能可用

### 附：接口 3 的 chart 图表类型
- `line`：折线趋势图，对应故障研判场景
- `heatmap`：热力图，对应信号分布场景
- `graph`：关系图，对应网络拓扑场景

---

## 接口 8：时序预测结果查询（预测结果接口契约 v1）

GET /api/predict/result?scenario=校园&start_time=2026-01-04 00:00:00&end_time=2026-01-04 23:59:59&target_type=ap&target_id=AP-01&metric=rsrp

功能：根据时间范围、预测对象与指标，返回时序预测结果（预测值、置信区间与状态标注）。
契约 v1 已冻结。**当前实现是占位**：`main.py` 里固定返回 `model="placeholder"`、`predict_list=[]`、`total=0`，**不调用** `forecast_model`，也没有转调接口 10。

> 更正（独立审计）：本节此前写"内部转调接口 10 的 Prophet 预测数据做字段映射"，与实现不符；README 同样把接口 8 描述成"查询壳"。真要做真实预测请用接口 10（`/api/analyze/forecast`），或调用接口 11（`/api/forecast/preview`）取预测帧。

### 请求参数

|参数|类型|必填|说明|
|---|---|---|---|
|scenario|string|是|业务场景，如 校园（与 VALID_SCENARIOS 白名单一致）|
|start_time|string|是|预测起始时间，格式 yyyy-MM-dd HH:mm:ss|
|end_time|string|是|预测结束时间，格式 yyyy-MM-dd HH:mm:ss|
|target_type|string|是|预测对象类型：ap / ue|
|target_id|string|否|指定单个对象 ID（如 AP-01 / UE-01）；不传返回全部对象|
|metric|string|是|预测指标：rsrp / sinr / throughput|
|interval|string|否|预测结果聚合粒度，默认 15min（支持 5min / 15min / 30min / 60min）|

### 响应

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "target_type": "ap",
    "target_id": "AP-01",
    "metric": "rsrp",
    "model": "placeholder",
    "predict_list": [
      {
        "time": "2026-01-04 00:00:00",
        "position": "AP-01",
        "predict_value": -88.5,
        "confidence_min": -92.1,
        "confidence_max": -85.2,
        "status": "normal"
      }
    ],
    "total": 96
  }
}
```

说明：占位实现阶段 `predict_list` 为空数组 `[]`、`total` 为 0、`model` 固定为 `"placeholder"`；字段结构保持契约不变，前端可先行对接联调。

### 字段说明

|字段|类型|说明|
|---|---|---|
|target_type / target_id|string|预测对象类型与标识，与请求参数一致|
|metric|string|本次预测指标|
|model|string|预测模型标识：占位阶段为 placeholder，接入 Prophet 后为 prophet、LSTM 后为 lstm|
|predict_list|array|预测结果列表，每项含 time / position / predict_value / confidence_min / confidence_max / status|
|predict_value|float|预测数值，单位同原指标（rsrp dBm、sinr dB、throughput Mbps）|
|confidence_min / confidence_max|float|置信区间上下限，与预测值同单位|
|status|string|状态标注：normal（正常）/ warning（预警）/ abnormal（异常），按预测值阈值判定|
|total|int|本次返回的预测点数量|

错误码：复用全局错误码体系——参数缺失 40001、格式非法 40002、范围越界 40003、服务异常 50001。

## 接口 9：拓扑增强数据（拓扑增强接口契约 v1）

GET /api/network/topology/enhanced?scenario=校园&time=2026-01-01 10:00:00

功能：返回指定场景、时刻下的网络拓扑增强数据。在基础拓扑（nodes + links）之上，扩展 AP/UE 属性、链路多指标与整体统计，供前端拓扑增强视图与态势分析使用。

### 请求参数

|参数|类型|必填|说明|
|---|---|---|---|
|scenario|string|是|业务场景，如 校园|
|time|string|**是**|指定时刻，格式 yyyy-MM-dd HH:mm:ss。**必填**：不传返回 40001（有意不静默取数据集端点，否则回放模式会露馅）|
|include_metrics|boolean|否|是否附加链路指标，默认 true|

### 响应

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "timestamp": "2026-01-01 10:00:00",
    "nodes": [
      {
        "id": "AP-01", "type": "ap", "x": 120.0, "y": 220.0,
        "properties": {"rsrp_avg": -92.5, "prb_usage": 62.3, "active_users": 11}
      },
      {
        "id": "UE-01", "type": "ue", "x": 94.8, "y": 192.62,
        "properties": {"rsrp": -90.1, "sinr": 8.2, "throughput": 54.7}
      }
    ],
    "links": [
      {
        "source": "AP-01", "target": "UE-01",
        "metrics": {"rsrp": -90.1, "sinr": 8.2, "throughput": 54.7, "latency": 18.5, "packet_loss": 1.2}
      }
    ],
    "stats": {"ap_count": 3, "ue_count": 3, "link_count": 3, "avg_rsrp": -91.2, "avg_sinr": 10.3}
  }
}
```

### 字段说明

|字段|类型|说明|
|---|---|---|
|timestamp|string|本次拓扑对应的时刻|
|nodes|array|节点列表，type 区分 ap / ue；x、y 为坐标（米）；properties 为扩展属性|
|nodes[].properties|object|AP：rsrp_avg（平均信号强度）/ prb_usage（资源占用）/ active_users（活跃用户数）；UE：rsrp / sinr / throughput 当前指标|
|links|array|AP-UE 连接链路；metrics 含 rsrp / sinr / throughput / latency / packet_loss 全量核心指标|
|stats|object|整体统计：节点数、链路数、平均 RSRP / SINR，供态势概览|

错误码：复用全局错误码体系——参数缺失 40001、格式非法 40002、范围越界 40003、服务异常 50001。

> 契约状态说明：接口 8、接口 9 契约 v1 已冻结，前端联调均以本契约为准；**接口 8 当前是占位实现**（`predict_list=[]`、`model="placeholder"`，不调用任何模型），字段变更需走契约评审。

## 接口 10：区域信号/频谱态势预测（Prophet 时序模型）

POST /api/analyze/forecast

功能：对每个 UE × 指标（rsrp/sinr/throughput）做 1 小时桶聚合，按 `split = max(int(n*0.8), n-horizon)` 切分（默认 720 点 → 696 训练 / 24 测试，**不是固定 80/20**）训练 Prophet、在测试段评估（输出 predicted 与 MAE/RMSE/MAPE），并在测试段之后延伸 24 小时未来预测（含置信区间 lower/upper）。与接口 8（/api/predict/result，契约 v1）的区别：接口 10 为**真实预测数据源**（Prophet，缺依赖/超时降级移动平均）；接口 8 为**占位**——字段结构保留 v1 契约，但固定返回 `predict_list=[]`，**不调用任何模型、也不转调接口 10**。

### 请求

```json
{
  "scenario": "校园",
  "window_start": "2026-01-01 00:00:00",
  "window_end": "2026-01-31 23:59:59"
}
```

|参数|类型|必填|说明|
|---|---|---|---|
|scenario|string|是|业务场景，如 校园（与 VALID_SCENARIOS 白名单一致）|
|window_start|string|是|分析窗口起始，格式 yyyy-MM-dd HH:mm:ss|
|window_end|string|是|分析窗口结束，格式 yyyy-MM-dd HH:mm:ss|

### 响应

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "scenario": "校园",
    "window_start": "2026-01-01 00:00:00",
    "window_end": "2026-01-31 23:59:59",
    "method": "prophet",
    "bin_size_min": 60,
    "metrics": ["rsrp", "sinr", "throughput"],
    "ues": ["UE-01", "UE-02", "UE-03"],
    "series": {
      "UE-01": {
        "rsrp": {
          "true_mean": -92.43,
          "pred_mean": -91.92,
          "mape": 0.55,
          "bins": ["2026-01-01 00:00", "...", "2026-01-30 23:00"],
          "true": [-92.1, "...", -93.0],
          "predicted": [-91.8, "...", -92.2],
          "forecast_bins": ["2026-01-31 00:00", "...", "2026-01-31 23:00"],
          "forecast": [-91.6, "...", -92.4],
          "lower": [-95.6, "...", -96.1],
          "upper": [-84.4, "...", -85.2]
        }
      }
    }
  }
}
```

### 字段说明

|字段|类型|说明|
|---|---|---|
|method|string|本次请求实际采用的预测模型，如实返回：`prophet`（全部序列 Prophet 成功）/ `moving_average`（全部序列都走了移动平均降级）/ `mixed`（部分序列降级）/ **`no_data`（窗口内可聚合的 1 小时桶不足 4 个，任何序列都没做预测）**。`no_data` **不等于**降级：此时 `series[ue][metric].method` 全为 `none`、`predicted/forecast` 为空数组，原因写在 `limitations` 里|
|bin_size_min|int|聚合桶粒度，固定 60（1 小时）|
|metrics / ues|array|本次预测的指标与 UE 列表|
|series[ue][metric]|object|单个 UE × 指标的预测结果（含 method 字段：prophet / moving_average / none）|
|bins / true|array|全量 1 小时桶实测序列（前端对比图横轴与实测线）|
|predicted|array|测试段预测值；长度 = `n - split`，其中 `split = max(int(n*0.8), n-horizon)`。默认 30 天窗口（n=720、horizon=24）时为 **24 点**；降级路径（moving_average）已与 Prophet 使用**同一套切分**，两条路径的 predicted 长度与 true_mean 可直接比较|
|total_points / train_points / test_points / split_points|number|**实际切分**（前端必须按这些值展示，不要写死"前 80%/后 20%"）：`split = max(int(n*0.8), n-horizon)`。实测 2 天窗口（48 桶）→ 38/10；7 天窗口（168 桶）→ **144/24（训练占 85.7%）**|
|true_mean / pred_mean / mape|number|测试段实测均值 / 预测均值 / 平均绝对百分比误差（%）|
|forecast_bins / forecast|array|未来 24 小时预测的时间轴与预测值|
|lower / upper|array|未来 24 小时置信区间下 / 上限（与 forecast 等长）；**降级为 moving_average 时无置信区间，lower/upper 恒等于 forecast**，前端不应绘制置信带|
|limitations|array|**本次结果的使用限制**，会如实说明：`no_data`=「桶不足 4 个…这不是模型降级」；降级=「Prophet 不可用或拟合超时，已降级为移动平均（常数预测）」；另外透出模型返回的 `degrade_reason`。前端应展示，不得折叠隐藏|

### 备注

- 训练/测试切分：`split = max(int(n*0.8), n-horizon)`（**不是固定 80/20**；默认 720 点 → 696/24）。未来延伸固定 24 小时；序列不足 24 个点或 Prophet 拟合失败时自动降级 moving_average（降级时 method 如实返回 moving_average，lower/upper=forecast 无置信区间），**降级路径使用同一套切分**，指标可比
- Prophet 需要**可写的临时目录**（cmdstanpy 每次拟合写编译产物）；预热失败后该进程内**永久降级**、只能重启恢复，可用 `/api/health` 的 `capabilities.prophet_runtime` 检查
- 请求默认窗口为 **30 天全窗**（2026-01-01 00:00:00 ~ 2026-01-31 23:59:59），可直接观察到第 4 天起的故障预测；不传 window_start/window_end 时走默认窗口
- 数据源为 30 天仿真数据（`data/network_data_30d(1).csv`，main.py 默认读取；3d 版备份为 `data/network_data_3d_bak.csv`）
- 30d 数据第 4 天起每 2 天注入一组故障，anomaly 演示窗口请选择 1 月 4 日之后以观察到异常样本
- 错误码：复用全局错误码体系——参数缺失 40001、格式非法 40002、范围越界 40003、服务异常 50001

## 接口 11：预测预览（频谱预测预警 v2）

POST /api/forecast/preview

功能：生成/取得一轮频谱预测（支持频谱曲线 curve / 功率时频 power-time / 占用态势 occupancy 三种视图）。
**防未来口径（硬约束）**：模型与预警只能使用 `baseTime` 及以前的数据，禁止读取七天 CSV 的未来真值或未来异常标签。
`history` 为前端当前实际参考范围（可能由用户自定义，不保证总是 60 分钟）；`history.end` 必须等于 `baseTime`。前端不上传完整七天数据，不在切换视图时再次请求。

### 请求

```json
{
  "scenario": "校园",
  "baseTime": "2026-01-02 14:00:00",
  "history": {"start": "2026-01-02 13:00:00", "end": "2026-01-02 14:00:00"},
  "horizonMinutes": 30
}
```

|参数|类型|必填|说明|
|---|---|---|---|
|scenario|string|是|业务场景，如 校园（与 VALID_SCENARIOS 白名单一致）|
|baseTime|string|是|模拟主时钟或固定查看时刻，格式 yyyy-MM-dd HH:mm:ss；预测与预警只基于该时刻及以前的数据|
|history|object|是|历史参考范围：{start, end}，**end 不得晚于 baseTime**（晚于即 40002；早于允许但会用到更短的历史）。窗口上限 800 分钟（超过返回 40003，避免"帧数×频段数 ≤ 40000"被突破）。前端可自定义，不保证 60 分钟|
|horizonMinutes|int|**是**|预测时长分钟，合法范围 1~1440，**必须是整数**（小数返回 40002）。注意：后端无默认值，省略即参数校验失败；前端的默认值是 120（`config.ts`），不是 30|

### 响应（顶层）

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "fc-20260102-1400-30",
    "scenario": "校园",
    "baseTime": "2026-01-02 14:00:00",
    "history": {"start": "2026-01-02 13:00:00", "end": "2026-01-02 14:00:00"},
    "forecast": {"start": "2026-01-02 14:00:00", "end": "2026-01-02 14:30:00"},
    "generatedAt": "2026-01-02 14:00:01",
    "source": "simulation",
    "status": "ready",
    "message": "完成预测，未发现明显风险",
    "alerts": [],
    "spectra": [],
    "impacts": [],
    "spectrumViews": null
  }
}
```

|字段|类型|说明|
|---|---|---|
|id|string|唯一预测 ID：识别场景、基准、历史范围、预测时长、数据/模型版本|
|scenario / baseTime|string|与请求一致|
|history|object|与请求的历史起止完全一致|
|forecast|object|{start=baseTime, end=baseTime+horizonMinutes}|
|generatedAt|string|结果实际生成时间，可与 baseTime 不同|
|source|string|simulation 或 measurement；禁止用 demo 冒充真实接口|
|status|string|ready（完成预测）/ insufficient_data（数据不足）/ unavailable（不可用）；后两者必须返回空的 alerts/spectra/impacts，且省略 spectrumViews 或返回 null，不得夹带过时结论|
|alerts|array|风险数组，无风险时为空数组|
|alertsTruncated|bool|`alerts` 是否因超过前端上限（200 条）被截断；为 true 时 `message` 会写明"仅展示前 200 条"。**不要**忽略该字段判断风险是否完整|
|spectra|array|频谱数据数组，功率与占用率为不同记录（metric 区分）|
|impacts|array|SINR、时延等相关指标预测数组（无网络性能预测能力时可不返回）|
|spectrumViews|object \| null|无风险时的概览视图组（可选）；含义与 alerts 内 spectrumViews 相同|

### alerts[] 条目

|字段|类型|说明|
|---|---|---|
|id|string|风险 ID；同一持续风险保持同一 id 并更新，避免每分钟新增事件|
|target|string|风险对象（如 AP-02）|
|title / summary|string|风险标题与摘要|
|severity|string|high / medium / low|
|start / end|string|风险时段，必须在 forecast 范围内|
|spectrumId|string|主关联频谱记录 ID（兼容字段）；非空时必须属于该风险组|
|bandIds|array|受影响频段 ID 列表（引用 spectrumId 对应频率网格）；无超阈值频段时为空数组|
|impactId|string|关联的 impacts 指标 ID|
|spans|array|该风险**本段**的逐分钟区间 `[{start,end}]`；段长 >60 分钟时只给首末两个端点（见 spansTruncated）。用于表达"中间恢复的间隙不属于本段"|
|spansTruncated|bool|`spans` 是否被截断（仅保留首末端点）|
|segment / segmentCount|int|同一 AP+类型在本轮预测里的第几段 / 共几段；多段时 id 追加 `-k`、标题追加"（第 k/n 段）"。单段时 id 与旧版一致|
|spectrumViews|object|{powerId, occupancyId, recommendedView, reason}：powerId 必须指向 metric=power、occupancyId 必须指向 metric=occupancy；缺少数据时相应 ID 为 null；同一组引用的频谱记录必须来自同一 observationId；recommendedView 可为 curve/power-time/occupancy|

> 风险分数不能直接当作发生概率；不提供 spectrumViews 的旧响应仍可用（仅展示 spectrumId 关联的单类指标）。

#### 预警触发判据（v2.1，取代"统计历史标签"）

> 变更背景：原实现统计**已发生**的历史窗口末 10 分钟 `network_status` 标签，标题写"预测…持续"，
> 本质是事后提示——`问题状态与责任分工` 里"提前预判仍未实现"指的就是这条。

现判据（`main.py` 的 alerts 段）：

1. 取该 AP 的**预测帧**，逐分钟统计越阈频段数：
   - 弱覆盖：`power_dbm < −95`（`ALERT_WEAK_POWER_DBM`）
   - 拥塞：`occupancy_pct > 80`（`ALERT_CONGESTION_OCC_PCT`）。**v2.9 由 85 下调为 80**：
  预警还要求"≥40/50 频段同时越阈"，而实测 4 个拥塞事件的越阈频段数最多 39/37/37/39，
  差 1~3 个导致拥塞预警从未触发；全 7 天回测 80% → 触发 114 段且全部命中真值、0 误报
   - 越阈频段数 ≥ `max(5, int(频段数 × 0.8))`（`ALERT_MIN_CROSSING_RATIO = 0.8`；当前 50 段即 **40 段**）才算命中，避免单频段噪声触发。
     > 更正（独立审计）：本节此前误写为 `max(3, 频段数/10)`（50 段即 5 段），与上一行"≥40/50"及代码都不符；按 5 段实现的客户端会严重误报。
   - 还必须**连续 ≥3 分钟**满足上述条件（`ALERT_MIN_CROSSING_MINUTES = 3`）；单分钟抖动不算命中，且中间恢复的间隙会把风险**拆成多条**（见 `alerts[].segment`）。
2. 用**历史最后一帧**判断基准时刻是否已越阈，给出两种语义（与责任分工要求的"持续风险/提前预警分别验收"对应）：

| 情形 | title 形态 | start | severity |
|---|---|---|---|
| 基准时刻**已**越阈 | `AP-02 已处于弱覆盖，预计持续` | `baseTime` | 越阈频段占比 ≥50% → high，否则 medium |
| 基准时刻**未**越阈 | `AP-02 预计 8 分钟后功率跌破 -95dBm` | 首次越阈的那一分钟 | ≤10 分钟 high；≤30 分钟 medium；否则 low |

3. `end` = 最后一个越阈分钟 + 1（封顶 `forecast.end`），因此**时段始终落在预测窗口内**（前端校验要求）。
4. `bandIds` 取首次越阈那一分钟里越阈幅度最大的 5 个频段（弱覆盖取功率最低，拥塞取占用率最高）。

> **重要限制（数据侧）**：本判据能给出提前量的前提是历史窗口里存在"朝阈值滑动"的渐变趋势。
> 但当前仿真数据的事件是**单分钟阶跃**（事件前 20 分钟 `RSRP < −110 dBm` 的行数恒为 0），
> 事件前历史完全平坦 → 趋势门限判为无趋势 → 提前量恒为 0。
> 实测（v2.8 数据，2026-10 重新生成后）：**提前预警可触发**。基准时刻 `2026-01-01 10:11`
> （弱覆盖事件 10:00 起、渐变 20 分钟）给出「AP-02 预计 17 分钟后功率跌破 −95 dBm」（目标 10:28），
> 实际越阈约在 10:12；拥塞事件（01-01 20:00 起）实测提前 1–19 分钟。
>
> 更正（独立审计）：本节此前写"基准 10:08 会给出「预计 14 分钟」"，实测该基准 **0 条预警**
> （趋势尚未形成），首条预警出现在 10:11。数字已按实际返回更正。
>
> ⚠️ 旧结论作废：v2.7 数据是单分钟阶跃（事件前 25 分钟 RSRP < −105 的样本数恒为 0），
> 当时"提前预警 0 次、已发生·持续 4 次"是**数据缺陷导致的能力不可用**，不是模型限制。
> v2.8 给数据加了 20 分钟渐入/渐出后，该能力才真正可演示。
> 用合成渐变历史（−0.6 dBm/分）验证判据本身有效：提前量 23 分钟。
> 要让"提前预警"在演示中真实出现，需先修数据侧的渐变征兆（见审查报告 D-3）。


### spectra[] 条目

|字段|类型|说明|
|---|---|---|
|id|string|频谱记录 ID|
|observationId|string|观测点 ID（AP），同视图组校验用；旧版单图记录可不提供|
|observationLabel|string|观测点可读标签|
|metric|string|power 或 occupancy|
|unit|string|dBm 或 %；占用率色标必须为 0—100，功率色标由后端给定（历史与预测共用）|
|scale|object|{min, max} 功率量程|
|bands|array|频段数组，每项 {id, label, lowerMHz, upperMHz}；按频率升序且不重叠，ID 唯一，单位 MHz；**首版最多 128 段**|
|history|array|历史帧数组，每项 {start, end, values[]}；end ≤ baseTime，按时间升序不重叠，允许缺口|
|predicted|array|预测帧数组，每项 {start, end, values[]}；start ≥ baseTime；只有历史、没有未来预测也可返回|

> values 按 bands 顺序对应，缺失值用 null（不能用 0 代替）；占用率范围 0—100；帧描述实际采样/统计时段（不把统计区间伪装成精确瞬时测量）。
> **上限约束：单侧帧数 × 频段数 ≤ 40000**，超出由后端汇总，前端不会静默截断。

### impacts[] 条目

|字段|类型|说明|
|---|---|---|
|id / targetLabel / label / unit|string|指标标识、对象标签、名称与单位|
|history / predicted|array|点数组 {time, value}，时间在各自范围内且升序不重复；缺失 value=null|
|predicted[].lower / upper|number|可选置信区间，成对提供且 lower ≤ value ≤ upper；无可靠区间则省略|
|threshold|object|可选阈值 {value, direction: above\|below}，direction 为 above 或 below|

### 预测方法说明（v2.1，取代原"均值±1 交替"）

> 变更背景：原实现把未来帧生成为「历史末 10 分钟各频段均值 ±1 dB 交替」（占用率 ±1 个百分点），
> 指标预测为「末值恒定 + 固定 ±10% 区间」。实测 120 帧 × 50 频段只有 94 个不同取值，
> 且预测起点与历史末帧存在跳变，图表上呈现方波与水平线，不能支撑"预测"表述。

现算法（`main.py` 的 `_linear_trend` / `_gated_slope` / `_damped_predict`）：

1. **趋势拟合**：对每个频段/指标取历史窗口末 30 分钟，做最小二乘，得到斜率（每分钟）与末值。
2. **显著性门限**：若整段累计变化 < 0.8 × 拟合残差标准差，判定"无趋势"，斜率置 0（保持末值）。
   目的是避免把分钟级抖动外推成一条斜线。
3. **阻尼外推**：`offset = slope × τ × (1 − e^(−k/τ))`，τ = 15 分钟。
   前 5–10 分钟体现趋势，之后指数饱和到平台，不会把阶跃当成趋势一路推下去。
   *（若直接线性外推，本数据 30 分钟窗口拟合出 −1.97 dBm/分钟，外推 120 分钟得到 −353 dBm，物理上不存在。）*
4. **边界约束**：总偏移夹在 ±max_drift 内，结果再夹到物理范围。

| 指标 | 趋势窗口 | τ | max_drift | 物理范围 |
|---|---|---|---|---|
| power（dBm） | 30 min | 15 min | ±10 dB | −140 ~ −40 |
| occupancy（%） | 30 min | 15 min | ±20 个百分点 | 0 ~ 100 |
| sinr（dB） | 30 min | 15 min | ±6 dB | −20 ~ 40 |
| throughput（Mbps） | 30 min | 15 min | 末值 25% | 0 ~ 300 |

5. **经验区间**：`impacts[].predicted[].lower/upper` = 预测值 ± min(1.96×残差标准差, max(2.0, 末值×25%))。
   **这是经验范围，不是统计置信区间**，前端会如实标注。
6. **预测起点**：取历史**最后一帧的实际值**（不再用末 10 分钟均值），消除历史区与预测区接缝处的跳变。

验证（本机实测）：故障窗口 AP-02 功率 −115.3 → 平滑下降至 −125.3 并饱和；120 帧 × 50 频段产生 358 个不同取值；
正常时段几乎持平（−74.01 → −73.27）；24 小时档趋势被门限判为不显著，保持末值。


## 接口 12：预测研判（基于同一轮预测，v2）

POST /api/forecast/analyze

功能：根据同一轮预测（forecastId）及风险（alertId）生成文字研判；无风险时 alertId=null 分析整体态势。
后端按 forecastId 取得同一份预测和截至 baseTime 的历史证据，**不得用请求到达时的系统时间替换 baseTime**。

### 请求

```json
{
  "forecastId": "fc-20260102-1400-30",
  "scenario": "校园",
  "baseTime": "2026-01-02 14:00:00",
  "alertId": "risk-ap02-busy",
  "history": {"start": "2026-01-02 13:00:00", "end": "2026-01-02 14:00:00"},
  "forecast": {"start": "2026-01-02 14:00:00", "end": "2026-01-02 14:30:00"}
}
```

### 响应

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "forecastId": "fc-20260102-1400-30",
    "baseTime": "2026-01-02 14:00:00",
    "alertId": "risk-ap02-busy",
    "source": "llm",
    "reply": "风险、对象、时段、证据、候选原因、预防方案、预计效果与不确定性的文字描述"
  }
}
```

|字段|类型|说明|
|---|---|---|
|forecastId / baseTime / alertId|string|必须与请求一致（alertId 无风险时为 null）|
|source|string|llm 或 rule|
|reply|string|普通文字组织：风险、对象时段、证据、候选原因、预防方案、预计效果与不确定性|

错误码：接口 11/12 复用全局错误码体系——参数缺失 40001、格式非法 40002、范围越界 40003、服务异常 50001。

## 频谱数据文件与统计口径（v2，四条锁定口径）

预测预警 v2 的频谱数据文件：`data/spectrum_7d.csv`（7 天版；`--days 3/30` 对应 `spectrum.csv` / `spectrum_30d.csv`）。

### 字段说明（9 字段）

|字段|类型|说明|
|---|---|---|
|timestamp|string|1 分钟一帧的时间戳 yyyy-MM-dd HH:mm:ss，与 network_data 同时间轴|
|observation_id|string|观测点（AP）ID：AP-01 / AP-02 / AP-03|
|band_id|string|频段 ID：band-0 … band-49（对应 n78 网格）|
|lower_mhz / upper_mhz|number|频段上下边界 MHz|
|power_dbm|number|观测点在频段内的接收功率 dBm（非功率谱密度）|
|occupancy_pct|number|频段在 1 分钟帧内的**资源占用比例** %（业务负载指标，与 power_dbm 不是同一物理量，见下）|
|network_status|string|normal / weak_coverage / congestion；与网络表（`network_data_7d.csv`）逐分钟对齐（**注意**：频谱每 AP 每分钟都有帧，网络表只在该 AP 当分钟有用户接入时才有行，(分钟,AP) 槽位覆盖约 66.7%，详见下方"事件对齐说明"）。UE→AP 汇总规则：同一 (分钟,AP) 下任一 UE 异常即该 AP 异常；多类型并存时频段级混合标注（前 25 段 congestion、后 25 段 weak_coverage），单类型全段一致|
|scenario|string|业务场景，如 校园|

### 四条锁定口径

1. **频段固定网格**：5G n78 3300—3800 MHz，10 MHz/段，共 50 段，升序不重叠（首版 ≤128 段上限）。
2. **history 帧按前端传入窗口**：接口按 `history.start/end`（约 60 分钟）裁剪历史帧，不按 7 天全量返回；单侧帧数 × 频段数 ≤ 40000。
3. **统计方式**：`power_dbm` 为观测点（AP）在频段内的接收功率，由中心 3550 MHz 高斯包络 + 帧内随机波动生成，**非功率谱密度**（若采用 dBm/Hz 需另行扩展单位和语义）。

   > **v2.8 更正（D-5）**：旧版此处把 `occupancy_pct` 定义为"帧内接收功率 ≥ −95 dBm 的时间比例"，
   > 但生成器里两个字段是独立生成的，按该定义复算必然对不上（实测 normal 帧功率 100% ≥ −95 却只报
   > 20–55% 占用，两者相关系数仅 0.19，评委可一行代码击穿）。
   > 现更正为：**`occupancy_pct` 是该频段的资源占用比例（业务负载指标），
   > 与 `power_dbm`（信号强度指标）不是同一物理量，二者不需要、也不应该由同一定义推出。**
   > 实测三态排序清晰且物理合理（拥塞 > 正常 > 弱覆盖）：

   | 帧状态 | occupancy 均值 | occupancy 中位 | power 均值 |
   |---|---|---|---|
   | `congestion`（负载高） | 87.2% | 86.2% | −70.4 dBm |
   | `normal` | 37.8% | 37.5% | −66.6 dBm |
   | `weak_coverage`（用户少/退避） | 20.3% | 20.2% | −104.9 dBm |

   弱覆盖帧功率全段 < −95 dBm（正常包络基础上再降约 41 dB，中心段约 −101、边缘段约 −113），
   其占用率低是因为弱覆盖区用户少、业务量低——这是符合物理的，不是数据错误。
4. **防未来口径**：模型与预警只用 `baseTime` 及以前的数据，不读取 7 天 CSV 的未来真值或未来异常标签。

> 事件对齐说明（v2.8）：频谱 `network_status` 由生成器 `--events data/network_data_7d.csv` 模式逐分钟逐 AP 取自网络表（含事件段与非整点起始段如 09:44 起）。聚合口径：同一 (时间,AP) 多 UE 多类型全部保留并按频段混合标注，保证按「分钟+AP+异常类型」去重后两表差异为 0（v2.6 曾因只取首个非 normal 导致 86 组类型缺失，v2.7 已修复）。
>
> ⚠️ **"逐分钟逐 AP 对齐"要分清两个层面（审查报告 D-6）**：
>
> | 层面 | 实际情况 |
> |---|---|
> | **频谱表覆盖** | ✅ 每个 AP **每分钟都有帧**（3 AP × 50 频段 × 每分钟 = 完整网格，1,512,000 行无缺失） |
> | **网络表覆盖** | ⚠️ 只有**该分钟有 UE 接入**该 AP 才有行 —— 实测 (分钟,AP) 槽位覆盖 **20,181 / 30,240 = 66.7%** |
>
> 所以"对齐"指的是：**频谱的 `network_status` 逐分钟取自网络表**（异常组差异 0/0），
> **不是**"每个 (分钟,AP) 槽位都有 KPI 采样行"。剩下约 1/3 的空槽位是**该 AP 当分钟没有服务用户**
> —— 这在真实网络里是正常现象（没用户就没有 KPI 上报），不是数据缺失。
>
> 影响面：依赖"按 (分钟,AP) 取窗口均值"的分析（研判、热力图）只看有采样的槽位，口径正确；
> 但**不要**把"槽位覆盖率"当成数据质量指标去解释 —— 它反映的是业务分布，不是缺失率。

> 数据文件按 `.gitignore` 不入仓库，由数据侧另行转交。

### 网络表事件口径（v2.8，取代旧的 EV 事件表口径）

> 更正：本节此前描述 `network_data_7d_events.csv`（EV-001~004、has_evolution 字段）但该文件从未存在。
> `data/fault_events.csv` **确实存在**（8 条 EV-001~008），由 `data_generator.py` 与网络表同步生成，
> 是接口 7 `/api/data/events` 的单一事实来源（见本文档接口 7）。
> 故障注入本身由 `data_generator.py` 的 `FAULT_SCHEDULE` 驱动，现按真实实现描述。

**事件来源**：`FAULT_SCHEDULE` 是写死的绝对时间表（如 `2026-01-01 10:00–12:00 AP-02 弱覆盖`、
`2026-01-01 20:00–22:00 AP-01 拥塞`），第 4 天起由 `build_fault_schedule()` 按天延伸。
事件起止时刻与 `--seed` 无关。

**渐变（v2.8 新增）**：事件强度按 `RAMP_MINUTES = 20` 分钟线性渐入/渐出，窗口两端为 0、中段为 1。

- 弱覆盖：`ap_rsrp_map[fault_ap] -= strength × ramp`；渐入/渐出期对"服务 AP 切换"也做位置过渡，
  避免窗口第一分钟因强制切换产生阶跃。
- 拥塞：PRB 与丢包从自然值按 `ramp` 过渡到目标值（目标 PRB ≈ 92%）。
- 标签规则不变：`serving_rsrp < −110` 才标 `weak_coverage`；`PRB > 85% 且丢包 > 5%` 才标 `congestion`。
  因此**渐入/渐出期数值已明显恶化但未达阈值时标签仍是 `normal`** —— 这是设计语义（可预测的渐变征兆），
  不是数据缺失。

**频谱的严重度映射（v2.8 新增）**：频谱功率不再只按标签二值跳变，而是按事件表的 RSRP/PRB 连续映射：

| 指标 | 严重度公式 | 未标注时的封顶 |
|---|---|---|
| 弱覆盖功率 | `clamp((−RSRP − 95) / 17.6, 0, 1)`，`power -= sev × 41 dB` | 0.80（保证功率 ≥ −95 dBm） |
| 拥塞占用率 | `clamp((PRB − 60) / 25, 0, 1)`，占用率向 85–95% 混合 | 0.75（保证占用率 < 85%） |

封顶的意义：**未标注帧不会被误判为越阈**（避免正常时段误报），但视觉上已经在恶化；
预测把这些趋势外推后才越过阈值 → 这就是"提前 N 分钟预警"的来源。

**数值自洽**：`weak_coverage` 行 RSRP ≤ −110 dBm（舍入到 −110.00 的边界行除外），
`normal` 行 RSRP > −110 dBm；两表按「分钟+AP+异常类型」去重差异 = 0（v2.8 实测 0/0）。

**背景异常**：线网表没有随机背景注入；约 2% 的背景干扰只存在于**非 events 模式**的频谱生成
（3 天/30 天版），events 模式（7 天版）关闭该逻辑，以保证与网络表全量对齐。

**验证工具**：`python scripts/audit_data.py`（两表对账 / 渐变分类 / 阈值自洽 / 占用率语义）。

### 新增响应字段（v2.9，三阶段整改）

以下字段为兼容性**新增**，旧字段全部保留，前端可选择性消费。

**接口 2 `/api/analyze/anomaly`**

| 字段 | 类型 | 含义 |
|---|---|---|
| `rule_score` | number | 规则评分（**主字段**）。衡量规则命中强度，**不是概率**；`confidence` 保留一版用于向后兼容 |
| `score_kind` | string | 固定 `"rule"`，语义标注 |
| `anomaly_scope` | string | `window`=整窗异常 / `hour`=仅个别小时越阈（整窗均值正常）/ `none` |
| `anomaly_spans` | array | **分钟级异常区间**：`[{ap_id, type, start, end, minutes, source}]`，start/end 精确到分钟（申报书要求"定位异常的起止时间"）。同一 AP、同一类型、间隔 ≤2 分钟的异常分钟合并；重叠区间会先并集，`source` 取 `label`（网络标签派生）/ `spectrum`（频谱越阈）/ `kpi`（KPI 判据）/ `merged`（两种来源重叠合并）。结论为"正常"时**一定为空** |
| `current_state` | string | **四态语义**（区分"出现过异常"与"此刻仍在故障"）：`fault_active`（窗口末尾 10 分钟内仍有异常）/ `recovered_in_window`（窗口内异常但末尾已恢复）/ `performance_warning`（指标偏低未达判据）/ `normal` |
| `last_anomaly_at` | string\|null | 本窗口内**最后一个**异常区间结束时刻（取所有区间的最大 `end`，不是排序末条）；无异常为 `null` |
| `performance_hints` | array | 未达故障判据但值得关注的提示：`[{ap_id, level, network_status, evidence}]`，用于 `performance_warning` 状态 |
| `plan_narrative` | string | 方案的自然语言说明（LLM 可用时由大模型改写，否则模板） |
| `plan_narrative_source` | string | **按实际结果**标注：`llm`（模型真的改写了）/ `template`（开关开着但模型失败或无 key 时也是它） |

**接口 3 `/api/agent/chat`**

| 字段 | 类型 | 含义 |
|---|---|---|
| `intent_debug.intent` | string | 识别到的意图 |
| `intent_debug.source` | string | **按实际结果**标注：`llm`（模型真的给出可用意图）/ `rule`（规则命中：含开关关闭、模型失败、输出不可解析三种情况）/ `fallback`（规则未命中且没走模型）。不要用"是否启用 LLM"推断该字段 |
| `intent_debug.degrade_reason` | string\|null | 没走成模型的具体原因：`未启用大模型（未配置 LLM_API_KEY 或 NETTUNE_ENABLE_LLM=0）` / `模型调用失败：TimeoutError` / `模型输出无法解析` / `模型输出的 action 不可用`；走模型成功时为 `null` |
| `intent_debug.rule_hit` | boolean | 关键词规则是否命中 |
| `intent_debug.llm_enabled` | boolean | 当前是否启用大模型 |
| `intent_debug.parsed` | object | 解析到的槽位：`scenario` / `ap_id` / `time_range` / `window` |
| `multi_turn.turns` | number | 该 session 累计轮次 |
| `multi_turn.inherited` | object | 本轮从上一轮继承的槽位（指代词如"它/那个/再"触发） |
| `multi_turn.session_new` | boolean | 本句之前该会话是否为空（= 新会话，或旧会话已被后端按 50 条上限淘汰）。前端据此提示"上文已失效"，不要假装还记得 |
| `kind` | string | `chain` 表示本次为**多步编排**（其余为单工具，无此字段） |
| `chain` | array | 编排步骤（**可直接渲染**，前端应逐条展示，不能只回"已执行两步"）：`[{tool, title, status, summary, window?, horizonMinutes?, baseTime?}]`，例如 `analyze_anomaly（查对象与证据）→ forecast_preview（看未来风险）` |
| `worst_ap` | string | 编排第一步识别出的最差对象（无则省略） |
| `horizon_minutes` | number | 编排里预测步骤**实际使用**的时长，沿用用户请求（"未来 3 小时" → 180），不是固定 60 |
| `reply` | string | 结论摘要（含对象名与风险条数），用于不展开步骤时也能看懂 |

**接口 11 `/api/forecast/preview`**

| 字段 | 类型 | 含义 |
|---|---|---|
| `narrative` | string | 预测结果的自然语言说明（含越阈提前量、不确定性说明） |
| `narrative_source` | string | `llm` / `template` |

**接口 15 `/api/forecast/spatial_frames`（新增）**

| 参数 | 默认 | 说明 |
|---|---|---|
| `scenario` / `baseTime` | 必填 | 场景与基准时刻 |
| `historyMinutes` | 48 | 历史总时长（6~1920 分钟） |
| `frameMinutes` | 6 | 每帧覆盖分钟数（1~240） |
| `pastFrames` | 6 | 历史帧数（1~12） |
| `futureFrames` | 3 | 预测帧数（1~12） |
| `metric` | rsrp | `rsrp` / `sinr` / `throughput` |

返回 `{shape:{grid,rows,cols}, x_range, y_range, past:[{start,end,points,values}], future:[...], note}`。
`values` 是 `rows×cols` 的二维场。未来帧**逐格点**外推（饱和 τ=3 帧），
所以恶化区域在空间上会扩张，而不是整场平移。

**接口 16 `/api/navigate/signal`（新增）**

参数 `scenario` / `start_time` / `end_time` / `ue_id` / `metric`；
返回 `{available, ue_id, current:{x,y,field_value,measured_value}, grid, advice:{action, direction, distance_m, target_x, target_y, expected, gain_db, text}, assumptions[]}`。

`advice.action` 为 `move`（有 ≥1 dB 的可行改善）或 `stay`（当前位置已接近最优）。
`assumptions` 必须原样展示 —— 这是基于区域信号场的**建议**，不是实时信道测量。

## 接口 13：部署自检（GET /api/health）

功能：给部署者/评委用的一键自检。返回数据文件是否到位、行数与时间范围、以及三项能力（prophet / kriging / LLM）是否可用。

设计要点：**本接口不依赖任何数据文件，缺少数据时也能正常返回**，因此可以在排障时第一时间调用。响应恒为 HTTP 200 + `code=0`，是否就绪看 `data.ok` 字段。

### 请求

无参数。

### 响应

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "ok": true,
    "data_dir": "F:\\AI-NetTune\\AI-NetTune\\data",
    "network": {
      "path": "F:\\AI-NetTune\\AI-NetTune\\data\\network_data_30d.csv",
      "exists": true,
      "rows": 129600,
      "size_mb": 14.6
    },
    "spectrum": {
      "path": "F:\\AI-NetTune\\AI-NetTune\\data\\spectrum_7d.csv",
      "exists": true,
      "rows": 1512000,
      "size_mb": 103.6,
      "max_timestamp": "2026-01-07 23:59:00",
      "band_count": 50
    },
    "capabilities": {
      "prophet": true,
      "kriging": true,
      "llm_enabled": true,
      "llm_key_configured": false,
      "llm_model": "deepseek-chat"
    },
    "issues": [],
    "hint": "数据文件缺失时请按 DEPLOY.md 第 2 节准备 data/ 目录；也可用 NETWORK_DATA_PATH / SPECTRUM_DATA_PATH 直接指定文件。"
  }
}
```

### 字段说明

|字段|类型|说明|
|---|---|---|
|ok|bool|网络表与频谱表均已加载且行数 > 0|
|data_dir|string|实际使用的数据目录（受 `NETTUNE_DATA_DIR` 影响）|
|network.path|string\|null|实际命中的网络表绝对路径；未找到为 null|
|network.rows|int|已加载行数（`0` 表示未加载）|
|network.size_mb|float\|null|文件大小|
|spectrum.max_timestamp|string\|null|频谱数据最后时刻；预测的 `baseTime` 超过它即返回 `insufficient_data`|
|spectrum.band_count|int|频段数（当前 50）|
|capabilities.prophet|bool|`false` 时接口 10 降级 `moving_average`|
|capabilities.kriging|bool|`false` 时接口 5 降级 `grid_mean`|
|capabilities.llm_*|bool/string|`llm_key_configured=false` 时意图解析与研判降级为规则模板（功能不中断）|
|issues|string[]|启动期收集到的具体问题（缺哪个文件、环境变量指向了不存在的路径等）|
|hint|string|修复建议|

### 备注

- 启动时后端还会打印一份等价的「启动自检」横幅到控制台。
- 命令行自检（更详细，含逐文件 SHA256 与两表重叠检查）：`python scripts/doctor.py`。
- 环境变量与数据交付方式见 `DEPLOY.md` 与 `data/README.md`。

## 接口 14：方案对比仿真（POST /api/plan/simulate）

功能：用户改完方案参数后，在**同一时间窗口、同一批采样点**上重算"保持原配置 vs 采用方案"的指标对比。
对应闭环里的"方案制定 — 动态调整"一环。

### 方案从哪来

`POST /api/analyze/anomaly`（接口 2）与 `POST /api/agent/chat`（接口 3，`fault_diagnosis` 意图）的响应里新增 `plans[]`：

|字段|类型|说明|
|---|---|---|
|id|string|方案 ID，形如 `PLAN-AP-02-tilt-1`；接口 14 用它反查参数定义|
|name / description|string|方案名与说明|
|scope|object|`{scenario, start, end}`，必须与研判范围一致|
|objectIds|string[]|作用对象（当前单个 AP）|
|goals|string[]|目标指标，取值同前端 `METRIC_META`（rsrpDbm / sinrDb / throughputMbps / resourceUtilizationPct …）|
|conditions / tradeoffs / tasks|string[]|适用条件、代价与限制、需要人工执行的任务|
|parameters|array|可编辑参数：`{id, objectId, key, label, description, type: number\|enum, unit, currentValue, suggestedValue, editable, min, max, step, options[]}`|
|simulation|object|按 `suggestedValue` 算好的仿真结果，结构同接口 14 的响应|

> 只有"覆盖故障 / 网络拥塞 / 边缘弱用户"才产出方案；判为"正常"时 `plans` 为空数组。
> 旧前端不受影响：没有 `plans` 时沿用本地 `fallbackPlan`。

### 接口 14 请求

```json
{
  "plan_id": "PLAN-AP-02-tilt-1",
  "scenario": "校园",
  "window_start": "2026-01-01 10:00:00",
  "window_end": "2026-01-01 11:59:59",
  "ap_id": "AP-02",
  "parameter_values": { "tilt": 2 }
}
```

### 接口 14 响应（`data`）

```json
{
  "id": "SIM-PLAN-AP-02-tilt-1",
  "status": "completed",
  "scope": { "scenario": "校园", "start": "2026-01-01 10:00:00", "end": "2026-01-01 11:59:59" },
  "method": "经验系数外推（非信道模型）：参数改动折算为指标偏移，逐样本叠加后在同一窗口重算",
  "summary": "下调天线下倾角： RSRP -115.56 → -108.36 dBm（+7.2）；弱覆盖样本占比 89% → 68%；吞吐 5.95 → 7.23 Mbps（+22%）。",
  "assumptions": ["对比双方使用同一时间窗口、同一批采样点，只有配置参数不同", "..."],
  "limitations": ["经验系数来自工程经验，不是本场景实测标定", "..."],
  "parameterValues": { "tilt": 2 },
  "metrics": [{ "key": "rsrpDbm", "objectId": "AP-02", "before": -115.56, "after": -108.36 }],
  "trend": { "metric": "rsrpDbm", "objectId": "AP-02",
             "points": [{ "time": "2026-01-01T10:00:00+08:00", "before": -78.2, "after": -74.6 }] },
  "coverage": { "area": { "xMin": 0, "yMin": 0, "xMax": 500, "yMax": 450 },
                "cellSize": { "x": 20, "y": 20 },
                "points": [{ "x": 30, "y": 30, "before": -92.1, "after": -88.5 }] }
}
```

### 仿真口径（务必如实表述）

|环节|做法|
|---|---|
|参数 → 指标偏移|**经验系数**折算，**不使用信道/传播模型**（与"Model-free"口径一致）|
|前后对比|把偏移**逐样本**叠加到同一窗口的真实采样上，再重新统计均值/弱覆盖占比|
|覆盖对比|20 m 网格上的 RSRP 前后对比，只输出有采样的格子|
|趋势对比|10 分钟桶的 RSRP 前后序列（时间带 `+08:00` 偏移）|

经验系数（`plan_model.py` 顶部常量，可调）：

|参数|系数|
|---|---|
|天线下倾角|每减小 1°，RSRP +1.2 dB|
|发射功率 / 波束增益|每 +1 dB，RSRP +1 dB|
|RSRP → SINR|每 +1 dB RSRP，SINR +0.4 dB|
|RSRP → 吞吐|每 +1 dB RSRP，吞吐 +3%|
|分流比例|每分流 1%，本区 PRB −0.8 个百分点|
|扩容|新增 1 载波，PRB −18 个百分点|
|PRB → 吞吐|PRB 每降 1 个百分点，吞吐 +2%|
|PRB → 时延 / 丢包|PRB 每降 1 个百分点，时延 −0.4%、丢包 −2%|

### 错误码

|场景|code|message|
|---|---|---|
|plan_id 不在本进程缓存（服务重启/方案过期）|40002|方案已过期（可能服务已重启），请重新发起一次研判后再仿真|
|scenario 不在白名单|40002|scenario仅支持…|
|start 晚于 end|40003|起始时间不能晚于结束时间|
|该窗口该 AP 无采样|40002|AP-xx 在该窗口没有采样，无法做同条件对比|

### 限制（必须知道）

- 参数值在服务端按方案定义**再夹一次范围**，非法值不会算进结论；但前端仍应先校验再提交。
- 方案缓存为进程内字典（上限 200 条，满后按 FIFO 淘汰 50 条），**服务重启后旧 plan_id 失效**。
- 天线下倾角 / 发射功率的"当前值"是默认工参（数据表没有工参字段），已在 `assumptions` 里写明。
- 未评估对邻区的连带影响与切换冲击，`limitations` 里已写明。

---

## 接口 17：预测风险 → 处置方案（POST /api/forecast/plans）

把**某一轮预测里的风险**变成可操作的处置方案（含可编辑参数与默认效果）。与接口 2/3 内嵌的 `plans[]`（现存问题方案）互不相干。

### 接口 17 请求

|参数|类型|必填|说明|
|---|---|---|---|
|forecastId|string|是|接口 11 返回的预测 ID；缓存不存在/已过期（服务重启或超出 32 轮）→ 40003|
|scenario|string|是|必须与该轮预测记录一致，否则 40002|
|baseTime|string|是|同上，必须一致|
|history|object|是|{start,end}，必须与该轮记录一致|
|forecast|object|是|{start,end}，必须与该轮记录一致|
|alertId|string|可选|指定要处置的风险；不存在 → 40002；若同时给了 `targetWindow`，该风险必须与时段**有交集**，否则 40002|
|targetWindow|object|可选|关注时段，必须**完整落在**预测范围内（部分越界 → 40003；起止反向或格式非法 → 40002）|

### 接口 17 响应（`data`）

```json
{
  "plans": [
    {"id": "PLAN-AP-03-tilt-1@833cd6c5…", "baseId": "PLAN-AP-03-tilt-1",
     "name": "下调天线下倾角", "origin": "forecast",
     "parameters": [{"id": "tilt", "label": "下倾角", "type": "number", "unit": "°",
                     "currentValue": 8, "suggestedValue": 5}],
     "simulation": {"id": "SIM-PLAN-AP-03-tilt-1@833cd6c5…", "status": "completed",
                    "summary": "…", "metrics": [{"key": "rsrpDbm", "objectId": "AP-03",
                    "before": -111.06, "after": -107.46}]},
     "basis": {"forecastId": "…", "risk": {"ap": "AP-03", "type": "覆盖故障",
               "start": "…", "end": "…", "title": "…"},
               "basisWindow": {"start": "…", "end": "…"}, "targetWindow": null}}
  ],
  "risk": {"ap": "AP-03", "type": "覆盖故障", "start": "…", "end": "…", "title": "…"},
  "basisWindow": {"start": "2026-01-02 13:12:00", "end": "2026-01-02 14:12:00"},
  "disclaimer": "方案依据：本轮预测预计 … 期间 AP-03 出现覆盖故障…；参数与效应基于预测基准前的实测窗口 …，属当前配置下的等效测算，不是该未来时段的实测结果…"
}
```

|字段|说明|
|---|---|
|plans|方案数组；**无风险时为空数组**并给 `message`，不编造方案|
|plans[].id|`<模板ID>@<轮次哈希>`：轮次哈希 = md5(forecastId\|alertId) 的 32 位，**同一 AP 的两轮预测不会互相覆盖**|
|plans[].baseId|模板原始 ID（如 `PLAN-AP-03-tilt-1`），便于对照与排查|
|plans[].origin|固定 `forecast`，前端据此与"现存问题方案"区分|
|plans[].basis|该方案的依据：forecastId / 风险（AP、类型、时段）/ 评估窗口 / 关注时段|
|plans[].simulation|后端已算好的默认效果：`id == "SIM-" + plan.id`；参数未改时前端可直接展示，改参数须重跑接口 14|
|basisWindow|**参数基线与效果评估所用窗口** = 预测基准前的实测窗口（不是预测目标时段）|
|disclaimer|免责说明全文；前端必须展示，不得折叠隐藏|

### 接口 17 错误码

|场景|code|说明|
|---|---|---|
|预测记录不存在/过期|40003|请重新执行一次预测|
|场景/基准/历史/预测范围不一致|40002|两轮记录不能混用|
|alertId 不存在，或与关注时段无交集|40002|不能拿窗外风险生成窗内方案|
|关注时段起止反向/格式非法|40002|格式 `yyyy-MM-dd HH:mm:ss`|
|关注时段部分越界|40003|请缩短时段或延长预测时长|
|该轮 `status=insufficient_data`|40003|数据不足，不能据此生成方案|
|基准前窗口该 AP 无采样|40002|无法给出可靠参数基线|
|该轮无风险|**0**|`plans=[]` + `message`，不视为错误|

### 与接口 14 的关系

接口 17 返回的 `plan.id` 可直接用于接口 14 `/api/plan/simulate`；但预测来源方案**绑定依据**：评估窗口必须等于 `basisWindow`、AP 必须等于 `basis.risk.ap`，否则 40002（防止手工改请求去读基准之后的真实数据或换 AP）。

---

## 接口 15：区域电磁态势帧序列（GET /api/forecast/spatial_frames）

把网络表按"空间网格 + 时间帧"聚合成二维场，供空间态势大图与缩略帧条使用。

### 接口 15 请求

|参数|类型|必填|说明|
|---|---|---|---|
|scenario|string|是|场景名，白名单外 → 40002|
|baseTime|string|是|基准时刻 `yyyy-MM-dd HH:mm:ss`；非法 → 40002|
|pastFrames|int|否|历史帧数，默认 8，上限 12；越界 → 40003|
|futureFrames|int|否|未来帧数，默认 6，上限 12；越界 → 40003|
|frameMinutes|int|否|每帧分钟数，默认 10，范围 1~240；越界 → 40003|
|historyMinutes|int|否|历史取样长度；当它小于 `frameMinutes×pastFrames` 时 `pastFrames` 会被夹到 1（响应里不含夹取提示，前端需按 `past.length` 展示）|
|metric|string|否|`rsrp` / `sinr` / `throughput` / `latency` / `prb_usage`；非法 → 40002|

### 接口 15 响应（`data`）

```json
{"scenario":"校园","baseTime":"2026-01-03 10:08:00",
 "frameMinutes":10,"pastFrames":8,"futureFrames":6,"metric":"rsrp",
 "xRange":[0,520],"yRange":[0,420],
 "past":[{"start":"…","end":"…","values":[[…]],"note":""}],
 "future":[{"start":"…","end":"…","values":[[…]],"note":""}],
 "note":"过去帧为实测插值，未来帧为趋势外推"}
```

|字段|说明|
|---|---|
|past / future|帧数组：`start`/`end`（该帧时间跨度）、`values`（二维网格，行优先）、`note`|
|xRange / yRange|网格坐标范围（米）|
|note|口径说明：过去=实测插值，未来=趋势外推|

### 接口 15 错误码

|场景|code|
|---|---|
|scenario 不在白名单 / baseTime 格式非法 / metric 非法|40002|
|pastFrames、futureFrames、frameMinutes 超出范围|40003（FastAPI 参数校验会先返回 40001「参数校验失败」，属已知口径差异）|

---

## 接口 16：信号导航建议（GET /api/navigate/signal）

给定当前位置与场景，给出"往哪个方向走、走多远、预期提升多少"。

### 接口 16 请求

|参数|类型|必填|说明|
|---|---|---|---|
|scenario|string|是|场景名，白名单外 → 40002|
|current_x / current_y|number|是|当前坐标（米），越界 → 40003|
|target_x / target_y|number|否|目标点；提供时给出直达建议|
|metric|string|否|`rsrp`（默认）/ `sinr` / `throughput` / `latency`|
|time|string|否|指定时刻；非法时间当前返回 40003（提示"该窗口没有数据"），与接口 15 的 40002 不一致，属已知差异|

### 接口 16 响应（`data`）

```json
{"current":{"x":120,"y":80,"value":-96.2,"level":"偏弱"},
 "advice":{"direction":"东北","degrees":42,"distanceM":120,"expected":{"value":-88.4,"gain":7.8}},
 "candidates":[{"x":240,"y":160,"value":-88.4,"gain":7.8,"distanceM":138}],
 "note":"基于当前时刻的网络表插值，非实测路测；移动后效果需现场验证"}
```

|字段|说明|
|---|---|
|current|当前点位与指标值、等级|
|advice|建议方向（中文方位 + 角度）、距离、预期指标与增益|
|candidates|候选取点（按增益排序）|
|note|口径：基于插值，不是路测|

### 接口 16 错误码

|场景|code|
|---|---|
|scenario 非法 / 坐标缺失|40002 / 40001|
|坐标越界，或该窗口无采样|40003|


