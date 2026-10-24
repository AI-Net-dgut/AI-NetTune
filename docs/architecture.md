# 系统架构说明

本文记录 AI-NetTune 的实际分层、依赖方向、数据流，以及**已知的架构约束与风险**。
配套文档：`api.md`（接口契约）、`data_dict.md`（数据字典）、`fault_definition.md`（故障判据）、
`acceptance.md`（能力验收）。

## 1. 总览

```
┌──────────────────────────────────────────────────────────────────┐
│ 前端  React 19 + TS + Vite + ECharts + antd        （约 5.9k 行）│
│                                                                  │
│   pages(2)        页面装配：Workbench / 管理                    │
│     ↓                                                            │
│   components(19)  展示组件：图表、表格、方案模块、AI 面板        │
│     ↓                                                            │
│   chat(5) / forecast(14) / replay(13)   三个业务域              │
│     ↓                                                            │
│   services(9)  →  types(2)     HTTP 与字段映射（不反向依赖 UI）  │
└───────────────────────────────┬──────────────────────────────────┘
                                │ HTTP，17 条 /api 路由（16 条业务接口 + 1 条报告下载）
┌───────────────────────────────▼──────────────────────────────────┐
│ 后端  FastAPI + Python 3.13                                       │
│                                                                  │
│   main.py（≈3.0k 行）★ 组合根                                     │
│     · 配置常量 / CSV 加载 / 数据索引                              │
│     · 17 个 HTTP handler                                          │
│     · 工具注册表 TOOLS + 意图分发                                 │
│     · 报告 / 方案 / 空间 / LLM 包装                               │
│                                                                  │
│   领域模块（互不依赖，只被 main 引用）：                          │
│     plan_model(324)     方案仿真                                  │
│     report_model(340)   Markdown / HTML 报告渲染                  │
│     forecast_model(279) Prophet ↔ 移动平均降级                   │
│     llm_agent(214)      意图规则 + LLM 调用 + 兜底                │
│     spatial_frames(143) / spectrum_frames(139)  IDW 空间帧        │
│     signal_nav(126)     信号导航                                  │
│     spatial_model(88)   Kriging 热力图                            │
│     data_layout(53)     数据路径解析                              │
│     data_generator(638) 离线造数（不进服务链路）                  │
└──────────────────────────────────────────────────────────────────┘
```

**依赖方向**：`前端 → HTTP → main.py → 领域模块`，单向。
11 个后端模块中**只有 `main.py` 有内部 import**，其余 10 个互不引用（星形拓扑）。
前端 `services/` 不反向依赖 UI（已校验 0 处）。

## 2. 数据流

```
data/network_data_30d.csv ──load_data()──► DF: list[dict]（12.96 万行）
                                              │
                                    _build_df_index()
                                              │
                                    _DF_IDX[scenario] = (时间戳列表, 行下标列表)
                                              │
data/spectrum_7d.csv ──load_spectrum()──► _SPEC{df, bands, min_ts, max_ts, by_ap, ts_index}
                                              │
                        ┌─────────────────────┴─────────────────────┐
                        ▼                                           ▼
              filter_rows(scenario, s, e, ap, user)        spectrum_window(ap, s, e)
              （bisect 二分，输出按时间升序）              （按 AP 子表 + 时间轴二分）
                        │                                           │
                        └──────────────► 接口 / 工具 ◄──────────────┘
                                              │
              ┌───────────────┬───────────────┼───────────────┬──────────────┐
              ▼               ▼               ▼               ▼              ▼
         研判(接口2)      对话(接口3)     预测预警(11)     空间帧(15)     报告(接口4)
         judge_ap +      意图→TOOLS      forecast_model   spatial_frames  report_model
         频谱维判定                                     + spectrum_frames
```

**关键设计**：真实数据只从两个口子进入（`filter_rows` / `spectrum_window`），
上层全部基于 `list[dict]` 或 DataFrame 子表，因此**换数据源只需改这两处**。

## 3. 能力与降级

| 能力 | 依赖 | 不可用时 | 暴露方式 |
|---|---|---|---|
| 时序预测 | Prophet | 降级移动平均，`method` 字段如实返回 | `/api/health` → `capabilities.prophet` |
| 空间插值 | PyKrige | 降级 grid_mean | `capabilities.kriging` |
| 大模型 | API Key（`sk-…`） | 降级规则意图 + 模板文案 | `capabilities.llm_key_configured` |
| 频谱数据 | `spectrum_7d.csv` | 预测返回 `insufficient_data`（文案区分"未加载"与"越界"） | 启动横幅 + 接口 message |

**原则**：LLM 只负责表达与意图增强，**全部数值与结论由确定性规则产出**，LLM 不可用时结果仍成立。

## 4. 性能特征（实测，本机）

| 环节 | 优化前 | 现在 | 说明 |
|---|---|---|---|
| `filter_rows` | 27~51 ms（12.96 万行逐行扫描） | **0.03 ms** | 加载时建时间索引，bisect 定位 |
| 研判接口（1 小时窗口） | 45 ms | **29 ms** | 加上 60s 短缓存后重复请求 ≈2 ms |
| 开局加载 | ~9.3 s / 396 MB | 未变（见风险 2） | 频谱 151.2 万行全量读入 |
| 预测预警（30 分钟） | ~114 ms | ~114 ms | 主要耗时在 pandas 逐频段趋势拟合 |
| 热力图（Kriging） | 92 ms（60 分钟窗口）| **1~3 ms（同参重复请求）** | 结果缓存 120s；7 天窗口首次计算仍约 4~6s |

短缓存：`_ANOMALY_CACHE`，TTL 60 秒，上限 128 条，key 含数据文件 mtime，
**出入均深拷贝**（调用方会就地改字典），可用 `NETTUNE_DISABLE_ANOMALY_CACHE=1` 关闭。
热力图缓存：`_HEATMAP_CACHE`，TTL 120 秒，上限 64 条，同样深拷贝、同样带数据版本。

## 5. 工程保障

| 手段 | 现状 |
|---|---|
| 契约测试 | `tests/test_contract.py` 49 项，覆盖接口关键语义 |
| HTTP 冒烟 | `_review/smoke_all_endpoints.py`，覆盖 **16 条业务接口**（`/api/download/reports` 为报告文件下载，不在冒烟内），每轮改动后执行 |
| 数据对账 | `scripts/audit_data.py`，两表异常组逐条对齐（当前 一致 9 / 不符 0） |
| 口径一致率 | `scripts/compare_verdicts.py`，研判 vs 频谱 56 窗口（当前 100%） |
| 能力验收 | `scripts/accept_forecast.py`，命中/漏报/提前量/误报/持续/一致率 |
| 死代码扫描 | `scripts/find_dead_code.py`（当前不可达 0） |
| 前端静态检查 | `tsc --noEmit` + `eslint`（当前均 0） |

## 6. 已知约束与风险

### R1　`main.py` 是单体（≈3.0k 行，17 个 handler + 组合根）

一个文件同时承担：配置、加载索引、HTTP、业务编排、工具注册、LLM 包装。

- **影响**：阅读成本高；多人并行易冲突；评审观感"不够工程化"。
- **建议**：拆为 `api/`（路由）+ `core/`（数据访问）+ `services/`（业务）。
  属**高风险管理动作**，答辩前不建议执行；已有边界（下方"边界约定"）可作为拆分依据。

### R2　数据全量驻留内存

频谱 151.2 万行 ≈ 396 MB，进程导入 ≈9.3 秒（每次启动都全量读 CSV）。

- **影响**：扩到 30 天频谱 ≈1.7 GB；多实例时每实例 400 MB；演示现场启动需等 9 秒。
- **建议**：POC 阶段可接受；真实接入时换时序库（TimescaleDB / InfluxDB）+ 按时间段查询。

### R3　全局可变状态 + 单进程假设

`DF`、`_SPEC`、`_SESSIONS`、`_FORECAST_CACHE`、`_PLAN_CACHE`、`_SCENE_EXTENTS`、`_ANOMALY_CACHE` 等
均在进程内存。

- **影响**：重启丢会话与缓存；多 worker 不同步；无法水平扩容。
- **建议**：单机演示无需处理；多实例前把会话/缓存外置（Redis）。

### R4　时间语义双轨

前端并存**主时钟（回放）**与 **baseTime（预测基准）**，`forecast/questionTime.ts` 与
`replay/historyQuestion.ts` 各有一套时间解析。

- **影响**：本类问题历史上多次引发缺陷（分隔符不识别、纯时间段走闲聊、支撑范围不一致）。
- **建议**：新增时间相关功能时**先看这两个文件**，并补一条解析用例。

### R5　数据覆盖范围不一致（已用机制收敛）

网络表 30 天、频谱表 7 天、真值 8 个事件（均在前 7 天）。
系统**支撑范围 = 交集 = 2026-01-01 00:00 ~ 01-07 23:59**：
预测越界返回 `insufficient_data`，研判越界裁剪并返回 `range_notice`。

- **影响**：超出前 7 天不可预测，属已声明边界。
- **建议**：若需全 30 天预测，应重新生成 30 天频谱（数据量 ×4），而非改判据。

### R6　无鉴权 / 报告落本地磁盘

`/api/*` 无认证；报告写到 `reports/` 目录，文件名 `RPT_<时间>_<4位随机>`（已消除同秒覆盖）。

- **建议**：公网部署前加反向代理鉴权；报告改对象存储。

## 7. 边界约定（拆分时按此切）

| 边界 | 归属 | 规则 |
|---|---|---|
| CSV 读取、索引、时间切片 | `main.py` 数据层 | **只有这里能碰 DF / _SPEC** |
| 研判、方案、报告、空间、预测 | 领域层 | 输入 `list[dict]` / 时间参数，不读全局 |
| 意图识别、工具分发 | 编排层 | 只依赖 `TOOLS` 注册表 |
| 响应结构 | 契约层 | 以 `api.md` 为准，字段变更先改文档 |

## 8. 变更风险分级（本项目约定）

| 级别 | 例子 | 要求 |
|---|---|---|
| 零风险 | 文案、报告模板、文档 | 加测试即可 |
| 低风险 | 加可选参数、加索引/缓存 | **必须证明输出与改动前一致** + HTTP 冒烟 |
| 中风险 | 改判据阈值、改分档 | 重跑 `accept_forecast` / `compare_verdicts`，记录基线对比 |
| 高风险 | 拆文件、换数据源、换模型 | 答辩期不做；须有等价性对照与回滚方案 |
