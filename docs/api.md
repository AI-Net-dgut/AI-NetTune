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
|7|GET|/api/data/realtime|实时增量监测数据|
|8|GET|/api/predict/result|时序预测结果查询（契约v1·查询壳：转调接口10数据，不新建模型）|
|9|GET|/api/network/topology/enhanced|拓扑增强数据（契约v1）|
|10|POST|/api/analyze/forecast|区域信号/频谱态势预测（Prophet 时序模型）|
|11|POST|/api/forecast/preview|预测预览（频谱预测预警 v2，baseTime 防未来）|
|12|POST|/api/forecast/analyze|预测研判（基于同一轮预测的文字分析，v2）|

## 通用响应格式
成功: {"code": 0, "message": "success", "data": {...}}
失败: {"code": 40001, "message": "参数错误：缺少scenario", "data": null}


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
  "start_time": "2026-09-28 00:00:00",
  "end_time": "2026-09-28 23:59:59",
  "format": "md",
  "include_chart": true
}
```

响应:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "report_id": "RPT_20260928_01",
    "file_url": "/download/reports/RPT_20260928_01.md"
  }
}
```

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

## 接口 7：实时增量监测数据

GET /api/data/realtime?last\_time=2026\-10\-06 00:00:00

可选参数:

- `last_time`：上次拉取的最大时间戳，格式 `yyyy-MM-dd HH:mm:ss`；不传则默认返回最近 10 条数据

响应:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 3,
    "columns": ["timestamp", "ap_id", "user_id", "rsrp", "sinr", "throughput", "network_status"],
    "records": [
      {"timestamp": "2026-10-06 18:00:00", "ap_id": "AP-01", "user_id": "UE-03", "rsrp": -91.2, "sinr": 6.3, "throughput": 54.7, "network_status": "normal"},
      {"timestamp": "2026-10-06 18:00:00", "ap_id": "AP-02", "user_id": "UE-05", "rsrp": -94.8, "sinr": 5.7, "throughput": 49.2, "network_status": "normal"},
      {"timestamp": "2026-10-06 18:00:00", "ap_id": "AP-03", "user_id": "UE-07", "rsrp": -89.5, "sinr": 7.1, "throughput": 59.8, "network_status": "congestion"}
    ]
  }
}
```

### 接口 7 字段说明

|字段|类型|说明|
|---|---|---|
|total|int|本次返回的增量数据条数|
|columns|array|字段名列表，与历史查询接口完全一致，可复用前端解析逻辑|
|records|array|增量数据数组，所有数据的时间戳均大于请求传入的 `last_time`|

**错误码说明**：

- `0`：成功

- `40002`：参数错误，时间格式非法

- `50001`：服务端内部异常



### 降级机制
1.  关键词规则优先匹配，不依赖LLM即可完成意图识别
2.  未命中关键词、输入模糊时，默认兜底为 `query_kpi`（KPI概况查询）
3.  LLM不可用时，自动降级为关键词规则+兜底逻辑，核心功能可用

### chart 图表类型
- `line`：折线趋势图，对应故障研判场景
- `heatmap`：热力图，对应信号分布场景
- `graph`：关系图，对应网络拓扑场景

---

## 接口 8：时序预测结果查询（预测结果接口契约 v1）

GET /api/predict/result?scenario=校园&start_time=2026-01-04 00:00:00&end_time=2026-01-04 23:59:59&target_type=ap&target_id=AP-01&metric=rsrp

功能：根据时间范围、预测对象与指标，返回时序预测结果（预测值、置信区间与状态标注）。
契约 v1 已冻结。当前实现为**查询壳**：保持 v1 字段结构不变，内部转调接口 10（/api/analyze/forecast）的 Prophet 预测数据做字段映射，不新建模型、不升级契约版本；降级语义与接口 10 一致（moving_average 时无置信区间，confidence_min/max 等于预测值）。

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
|time|string|否|指定时刻，格式 yyyy-MM-dd HH:mm:ss；不传默认取数据集最新时刻|
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

> 契约状态说明：接口 8、接口 9 契约 v1 已冻结，前端联调均以本契约为准；接口 8 为查询壳（转调接口 10 数据、不新建模型），字段变更需走契约评审。

## 接口 10：区域信号/频谱态势预测（Prophet 时序模型）

POST /api/analyze/forecast

功能：对每个 UE × 指标（rsrp/sinr/throughput）做 1 小时桶聚合，前 80% 训练 Prophet 模型、后 20% 测试评估（输出 predicted 与 MAPE），并在测试段之后延伸 24 小时未来预测（含置信区间 lower/upper）。与接口 8（/api/predict/result，契约 v1）的区别：接口 10 为**已接入真实 Prophet 模型的预测数据源**；接口 8 为**查询壳**——保持 v1 契约不变，转调接口 10 的数据，不新建模型、不升契约版本。

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
|method|string|本次请求实际采用的预测模型，如实返回：`prophet`（全部序列 Prophet 成功）/ `moving_average`（全部降级移动平均或全部序列无法预测）/ `mixed`（部分序列降级）；空数据时返回 `no_data`。series 内每个序列自带 method 字段，与顶层一致粒度|
|bin_size_min|int|聚合桶粒度，固定 60（1 小时）|
|metrics / ues|array|本次预测的指标与 UE 列表|
|series[ue][metric]|object|单个 UE × 指标的预测结果（含 method 字段：prophet / moving_average / none）|
|bins / true|array|全量 1 小时桶实测序列（前端对比图横轴与实测线）|
|predicted|array|测试段（后 20%）预测值，24 点，与真实值对比用|
|true_mean / pred_mean / mape|number|测试段实测均值 / 预测均值 / 平均绝对百分比误差（%）|
|forecast_bins / forecast|array|未来 24 小时预测的时间轴与预测值|
|lower / upper|array|未来 24 小时置信区间下 / 上限（与 forecast 等长）；**降级为 moving_average 时无置信区间，lower/upper 恒等于 forecast**，前端不应绘制置信带|

### 备注

- 训练/测试切分固定 80/20；未来延伸固定 24 小时；序列不足 24 个点或 Prophet 拟合失败时自动降级 moving_average（降级时 method 如实返回 moving_average，lower/upper=forecast 无置信区间）
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
|history|object|是|历史参考范围：{start, end}，end 必须等于 baseTime；前端可自定义，不保证 60 分钟|
|horizonMinutes|int|否|预测时长分钟，合法范围 1~1440，默认 30（由前端 config.ts 控制）|

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
|spectrumViews|object|{powerId, occupancyId, recommendedView, reason}：powerId 必须指向 metric=power、occupancyId 必须指向 metric=occupancy；缺少数据时相应 ID 为 null；同一组引用的频谱记录必须来自同一 observationId；recommendedView 可为 curve/power-time/occupancy|

> 风险分数不能直接当作发生概率；不提供 spectrumViews 的旧响应仍可用（仅展示 spectrumId 关联的单类指标）。

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
|occupancy_pct|number|频段在 1 分钟帧内被占用时间比例 %|
|network_status|string|normal / weak_coverage / congestion；与 network_data_7d_events.csv 逐分钟逐 AP 对齐（含 EV 事件与背景异常标签）。UE→AP 汇总规则：同一 (分钟,AP) 下任一 UE 异常即该 AP 异常；多类型并存时频段级混合标注（前 25 段 congestion、后 25 段 weak_coverage），单类型全段一致|
|scenario|string|业务场景，如 校园|

### 四条锁定口径

1. **频段固定网格**：5G n78 3300—3800 MHz，10 MHz/段，共 50 段，升序不重叠（首版 ≤128 段上限）。
2. **history 帧按前端传入窗口**：接口按 `history.start/end`（约 60 分钟）裁剪历史帧，不按 7 天全量返回；单侧帧数 × 频段数 ≤ 40000。
3. **统计方式**：`power_dbm` 为观测点（AP）在频段内的接收功率，由中心 3550 MHz 高斯包络 + 帧内随机波动生成，**非功率谱密度**（若采用 dBm/Hz 需另行扩展单位和语义）；`occupancy_pct` 占用判定阈值为接收功率 ≥ −95 dBm 视为忙，分母为帧内采样次数（1 分钟 = 60 次 1s 采样）；正常帧 20–55%，拥塞故障帧 85–95%，弱覆盖故障帧 10–30%；**弱覆盖帧功率对齐后端高亮阈值，全段 < −95 dBm**（正常包络基础上再降约 41 dB，中心段约 −101、边缘段约 −113）；约 2% 帧存在背景干扰（数值突降/突升，状态仍为 normal，仅 3/30 天非 events 版生成，7 天 events 版背景异常直接来自网络表标签）。
4. **防未来口径**：模型与预警只用 `baseTime` 及以前的数据，不读取 7 天 CSV 的未来真值或未来异常标签。

> 事件对齐说明（v2.7）：频谱 `network_status` 由生成器 `--events data/network_data_7d_events.csv` 模式逐分钟逐 AP 全量取自网络事件表（含 EV 事件与背景异常标签、非整点事件段如 09:44 起）。聚合口径：同一 (时间,AP) 多 UE 多类型全部保留并按频段混合标注，保证按「分钟+AP+异常类型」去重后两表差异为 0（v2.6 曾因只取首个非 normal 导致 86 组类型缺失，v2.7 已修复）。
> 数据文件按 `.gitignore` 不入仓库，由数据侧另行转交。