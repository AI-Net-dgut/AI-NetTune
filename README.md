# AI-NetTune

**基于 LLM 意图驱动的多模态无线网络智能分析与管控平台**

面向**网络测试与优化人员**的多接入点无线组网（5G 现网 / 校园 / 商场等）场景 Web 智能管控平台：支持自然语言提问，快速定位故障、输出多候选原因与证据链及处置建议；融合位置感知与电磁频谱预测（区域信号/频谱态势预测），提前预警；以**文本、图表、地图、拓扑及序列态势图**多模态呈现专业网络状态（非专业用户亦可借此看懂网络状态）。平台依据实时状态与用户需求形成"监测—分析—决策—优化"动态管理闭环，调整后网络与图表实时响应。

---

## 项目简介

本项目将大语言模型（LLM）智能体意图驱动思想迁移至无线网络监测与管控领域，主受众为**网络测试与优化人员**：

* **数据层**：支持仿真数据生成与 CSV 导入，覆盖多接入点组网监测场景（当前数据集为 3 个 AP + 3 条移动用户轨迹，逐分钟采样；18 维数据字典：核心 KPI + AP/UE 坐标 + 故障深度分析辅助字段，含网络状态标签，见 [docs/data_dict.md](docs/data_dict.md)）；多日时间跨度 + 坐标字段已就绪，支撑拓扑可视化与时空分析；

* **智能层**：通信 KPI 语义层将 RSRP、SINR、吞吐量等专业指标翻译为自然语言；多 KPI 联合研判引擎结合通信专业知识输出**可解释**的故障结论（覆盖故障 / 网络拥塞 / 边缘弱信号，多候选原因与支持/反证证据链）与**处置恢复建议**；融合历史与实时数据预测未来电磁频谱态势（区域信号/频谱态势预测）并**提前预警**；采用 **Model-free 免建模架构**——不依赖信道与传播模型，直接以历史与实时数据驱动预测与优化，规避建模误差；

* **交互层**：用户通过自然语言提问，即可完成数据查询、故障研判、报告导出，输出**文本、图表、地图、拓扑及序列态势图多模态**研判结果；

* **可靠性**：大模型仅负责语义理解，数据运算交由确定性工具执行，规避幻觉问题；大模型 API 不可用时，表单查询、告警、可视化等核心功能可降级完整使用。

## 项目价值

不止"看得见"，更要"能处置、有效益"：

* **快速定位**：多 KPI 联合研判定位故障区域（覆盖异常 / 拥塞 / 边缘弱信号），输出多候选原因与证据链及处置恢复建议，缩短排障时间；

* **提前预警**：基于历史与实时数据预测未来电磁频谱态势，故障发生前预警，变"事后排障"为"事前防范"；

* **动态管理**：依据实时状态与用户需求制定配置调整方案，形成"监测—分析—决策—优化"闭环，支持方案对比验证，改善网络质量、提升用户体验。

## 功能特性

* ✅ 通信 KPI 可视化（折线图 / 信号热力图 / 网络拓扑图 / 频谱地图）

* ✅ **多接入点拓扑图**：3 个基站（AP）与 3 条移动用户轨迹（UE）的位置分布、连接关系；点选 UE 查看信号 / 干扰 / 速率参数（借鉴 Cell-Free 拓扑可视化风格）

* ✅ **区域电磁频谱视图**：区域信号质量热力分布（各区域频谱态势一览）

* ✅ **真实 vs 预测对比图**：历史真实帧与预测帧并排对比（Signal / Interference / Rate）+ MAE / RMSE 误差指标（🚧 随时序预测落地）

* ✅ 区域信号质量预测与信号导航指引（位置感知 + 电磁频谱分析），故障**提前预警**

* ✅ 监测→定位→处置闭环：快速定位故障区域，输出处置恢复建议，降低运维成本

* ✅ 时序预测：基于历史与实时监测数据，以**Prophet**（缺依赖时降级移动平均）预测未来信号质量（电磁频谱态势预测）。
  
* ✅ 多 KPI 联合故障研判，输出 "故障类型 → 证据链 → 候选原因 → 处置建议"

* ✅ 自然语言对话式监测（LLM 意图识别）+ 多模态输出

* ✅ 分析报告导出

* ✅ 仿真数据生成 + CSV 导入

* ✅ React + TypeScript 前端（首页 / 工作台 / 分析记录三页，已与后端的 **13 条**业务接口联动；未调用的 8/9/13/16 见下）

## 技术架构

**分层与依赖方向**（单向向下，上层不直接读 CSV）：

```
浏览器（React 19 + TypeScript + ECharts）
        │  fetch /api/*（同源相对路径，开发时由 Vite 代理到 :8000）
        ▼
FastAPI 路由层（17 个业务接口 + 统一错误码 / 限流 / 超时）
        │
        ▼                                        ╔══════════════════════════════════════════╗
工具体层  query_kpi │ fault_diagnosis │           ║  ★ LLM 层（本平台核心）                   ║
          get_heatmap │ get_topology │   ───────▶ ║                                          ║
          forecast │ chat                          ║  ① 意图识别：规则优先，模型兜底           ║
        │                                          ║  ② 回答生成：基于**已算好的证据**写整段话 ║
        │                                          ║  ③ harness 校验：数字/对象/断言/结构     ║
        │                                          ║  ④ 不通过 → 重写（≤3 次）→ 退回规则文案  ║
        │                                          ╚══════════════════════════════════════════╝
        ▼
模型层  阈值判据（40/50 频段 · 连续 3 分钟）│ plan_model（方案与仿真）
        │ forecast_model（Prophet，失败降级移动平均）│ spatial_model（Kriging，失败降级网格均值）
        ▼
数据层  network_data_30d.csv │ spectrum_7d.csv │ fault_events.csv
        （启动时读入内存 + 时间戳二分索引；原始数据只读，仿真偏移在读取时叠加）
```

### LLM 层（本平台核心，可降级、可核查）

LLM 不是"接了个聊天接口"，而是**贯穿全流程、且每一步都有兜底**的一层：

```
用户一句话
   │
   ├─① 意图识别 ── 规则命中？──是──▶ 直接用规则结果（快、可复现，界面标"意图·规则"）
   │                     └─否──▶ 调模型解析意图（界面标"大模型识别"）
   │
   ├─② 工具执行 ── 取数 → 判据 → 候选原因 → 方案 → 预测（**数字全部由规则算**）
   │
   ├─③ 回答生成 ── 只把"已算好的证据"喂给模型，让它写整段回答
   │                （【结论】【依据】【方案】【预计效果】【前提与风险】）
   │
   ├─④ harness 校验（answer_llm.verify，9 条）
   │      1 数字必须来自证据    2 对象必须来自证据   3 无根因时禁确定性断言（含否定句识别）
   │      4 必须含三节结构      5 长度上限           6 参数调整必须与证据一致（8→5 不能写 8→6）
   │      7 方向不能写反        8 不得自造方案名     9 效果数值必须来自证据
   │
   └─⑤ 判定 ── 通过 ──▶ 展示「AI 研判 · 数字已核对」
                 └─不通过 ──▶ 重写（≤3 次）──仍不通过──▶ 退回规则文案 + 界面显示具体违规项
```

| 设计点 | 做法 | 为什么 |
|---|---|---|
| **不信任模型算数** | 模型只写文字，数字全部由规则计算后注入证据包 | 避免"看起来很专业但数字是编的" |
| **失败可观测** | 返回 `answer_check{violations, source, attempts}`，界面有「AI 文本状态」折叠 | 不把"模型没启用/被拒收"伪装成"规则答案" |
| **分轴降级** | 意图、回答、方案解读、预测方法各自独立标注来源 | 任一层失败只影响该层，不整体失败 |
| **成本可控** | 规则优先，只有规则未命中才调模型 | 常见问法零调用；断网也能完整演示 |

> 设计原则：**规则算数、模型组句、校验器把关**。


## 技术栈

| 层级       | 技术                                                         |
| ---------- | ------------------------------------------------------------ |
| 前端       | React + TypeScript + ECharts（graph 拓扑 / heatmap 热力 / bar+line 对比图） |
| 后端       | Python + FastAPI + Uvicorn                                   |
| AI         | 大模型 API（LLM 意图理解）+ 通信 KPI 语义层 + harness校验                 |
| 感知与预测 | 位置感知 + 电磁频谱分析（区域信号质量预测 / 信号导航指引）+ 时序预测（**Prophet**，移动平均仅作 baseline；LSTM 未实现） |
| 数据       | CSV（pandas 读取，18 维数据字典，含 AP/UE 坐标与故障深度分析字段） |

## 快速开始

> **完整部署步骤、环境变量、反向代理配置与故障排查见 [DEPLOY.md](DEPLOY.md)。**
> 数据文件不在仓库里（约 120 MB），**部署前必须先放数据**，见 [data/README.md](data/README.md)。

### 环境要求

| 组件 | 要求 | 说明 |
|---|---|---|
| **Python** | **≥ 3.10**（本项目在 3.13.10 上验证） | 代码用了 `str \| None` 等 3.10+ 语法，**3.9 会直接 `TypeError` 起不来** |
| Node.js | ≥ 20 | 前端 Vite 构建 |
| 数据 | 约 120 MB | 见下方「0. 先放数据」 |

> 不确定当前 `python` 是哪个版本、依赖是否齐全？用 `python scripts/doctor.py` 自检（退出码 0 = 可以启动）。
> 环境变量（LLM 密钥、数据目录、预测标定等）见根目录 [.env.example](.env.example)：可复制为 `.env`，
> 或用 `setx` 永久设置（见下方后端启动步骤）。前端变量见 [frontend/.env.example](frontend/.env.example)。
> Windows 上如果机器装了多个 Python，**推荐用 `scripts/start_backend.ps1` 启动** —— 它会自动挑 ≥3.10 的解释器，
> 并检查数据与依赖，避免用错解释器导致的莫名报错：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_backend.ps1
#   加 -Reload 开发热重载；加 -Port 8001 换端口
```

### 0. 先放数据

把 `network_data_30d.csv`（14.6 MB）与 `spectrum_7d.csv`（103.4 MB）放进 `data/`。
然后一条命令自检：

```powershell
python scripts/doctor.py        # 退出码 0 = 可以启动
```

如果拿到的是浏览器重复下载的 `network_data_30d(1).csv`，用 `python scripts/prepare_data.py --apply` 规范化文件名
（不规范化也能启动，后端候选名兼容）。

### 后端

```powershell
# 1. 安装依赖
python -m pip install -r requirements.txt
#    装不上 prophet / PyKrige 时用最小依赖（功能自动降级，服务仍可启动）：
#    python -m pip install -r requirements-min.txt

# 2. 启动服务（局域网联调用 0.0.0.0）
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

> 若上一步的 `python` 不是 3.10+，请改用 `powershell -ExecutionPolicy Bypass -File scripts\start_backend.ps1`。

**接入大模型**

```powershell
setx LLM_API_KEY "sk-你的key"     # 设置后需重开终端
setx LLM_MODEL "deepseek-chat"    # 可选，默认就是 deepseek-chat
```

不配置时，意图识别与文案生成**自动降级为规则模板**（接口照常返回，`source`/`narrative_source` 字段会如实标为 `rule`/`template`）。
配置后，规则未命中的自然语言提问、预测解释、方案解释与预测研判会改用大模型生成（单次约 2~5 秒）。
配置与排查细节见 [DEPLOY.md](DEPLOY.md) 第 7 节。

启动会打印「启动自检」横幅，直接显示数据是否到位、哪些能力处于降级状态。

浏览器打开 [http://localhost:8000/docs](http://localhost:8000/docs) 查看 Swagger 接口测试面板；
打开 [http://localhost:8000/api/health](http://localhost:8000/api/health) 看部署自检结果（`data.ok` 应为 `true`）。

> 接口 1/2/3/5/6/9 读取 `data/` 下的网络表（当前 30 天版，129,600 条 × 18 列）；
> 接口 11/12（预测预警）额外读取 103.4 MB 的 `spectrum_7d.csv`（1,512,000 条 × 9 列）。
>
> 两张表覆盖范围不同：网络表 30 天（01-01 ~ 01-30，每天都有注入故障），
> 频谱表只有前 7 天（01-01 ~ 01-07）。**系统支撑范围取交集 = 01-01 ~ 01-07 23:59**：
> 预测预警对 01-08 之后返回 `insufficient_data`，研判对越界窗口裁剪或拒绝并返回
> `range_notice` 说明——两侧口径一致，不会出现"研判报故障、预测说没数据"。
> 事件级验收真值 `fault_events.csv` 也只登记这 7 天内的 8 个事件。
>
> 时序预测（接口 10）使用 Prophet，缺失时自动降级为移动平均（`method` 字段如实返回）。
> ⚠️ Windows 上 Prophet 首次拟合需编译 Stan 模型，可能耗时数分钟——**演示请勿直连接口 10**，
> 前端预测走 `/api/forecast/preview`，不依赖 Prophet。

### 前端

```powershell
cd frontend
npm install
npm run dev
```

开发模式下前端通过 `vite.config.ts` 的 proxy 把 `/api` 转发到 `http://127.0.0.1:8000`。
构建产物部署（`npm run build`）**不带代理**，需要用 `VITE_API_BASE` 或反向代理，见 [DEPLOY.md](DEPLOY.md) 第 6 节。

## API 接口一览

| 接口 | 方法 | 路径                  | 用途                                                         |
| ---- | ---- | --------------------- | ------------------------------------------------------------ |
| 1    | GET  | /api/data/query       | 查询监测数据                                                 |
| 2    | POST | /api/analyze/anomaly  | 多 KPI 联合故障研判                                          |
| 3    | POST | /api/agent/chat       | 自然语言对话                                                 |
| 4    | POST | /api/export/report    | 导出分析报告（Markdown / HTML；前端记录页有「导出本次记录」，工作台有按当前时段导出。**注意：报告按"时段整窗"重新研判，不携带提问的 AP 与手改参数快照**）|
| 5    | GET  | /api/data/heatmap     | 信号热力图（Kriging 空间估计 + 方差；缺 PyKrige 时降级 `grid_mean`） |
| 6    | GET  | /api/network/topology/basic    | 基础网络拓扑：AP / UE 节点 + 窗口内主服务链路 |
| 7    | GET  | /api/data/events      | 故障事件清单（读 `data/fault_events.csv`，案例窗口的单一事实来源） |
| 8    | GET  | /api/predict/result   | 时序预测结果查询（契约 v1 · **占位**：固定返回 `predict_list: []`） |
| 9    | GET  | /api/network/topology/enhanced | 拓扑增强数据（契约 v1：AP/UE 属性 + 链路多指标 + 整体统计） |
| 10   | POST | /api/analyze/forecast | 区域信号/频谱态势预测：1 小时桶 · 按 `split=max(int(n*0.8), n-horizon)` 切分（默认 696/24，非固定 80/20）训练 Prophet + 未来 24h（lower/upper）；Prophet 不可用/超时降级移动平均（`method` 如实返回，桶不足 4 个返回 `no_data`） |
| 11   | POST | /api/forecast/preview | 预测预警 v2：真实频谱数据 → 历史帧 + 未来帧 + 风险预警（`baseTime` 防未来） |
| 12   | POST | /api/forecast/analyze | 预测研判 v2：按 `forecastId`/`alertId` 生成文字研判（LLM 不可用降级规则模板） |
| 13   | GET  | /api/health           | 部署自检：数据文件 / 依赖 / 大模型就绪状态（不依赖数据，永远可访问） |
| 14   | POST | /api/plan/simulate    | 方案对比仿真：改参数后在同一窗口重算"原配置 vs 方案"（预测来源方案需按其依据窗口评估） |
| 15   | GET  | /api/forecast/spatial_frames | 区域电磁态势帧序列（过去 N 帧 + 未来 N 帧，二维网格场） |
| 16   | GET  | /api/navigate/signal  | 信号导航建议（位置感知 + 覆盖分析 + 移动方向/距离/预期增益） |
| 17   | POST | /api/forecast/plans   | 预测风险→处置方案（带 `origin=forecast`、`basis{forecastId,risk,basisWindow}`、`disclaimer`；无风险时返回空列表） |

详细请求 / 响应格式见 [docs/api.md](docs/api.md)。

### P1 新增接口契约（v1 · 已冻结）

> 契约版本说明：接口 8 / 9 契约 v1 已冻结。占位实现阶段字段结构保持不变；后续字段变更需走契约评审并升级契约版本（详见 [docs/api.md](docs/api.md) 接口 8 / 9 章节）。

#### 接口 8：时序预测结果查询（GET /api/predict/result）

* 请求参数：`scenario`（必填）、`start_time` / `end_time`（必填，yyyy-MM-dd HH:mm:ss）、`target_type`（ap/ue，必填）、`target_id`（可选）、`metric`（rsrp/sinr/throughput，必填）、`interval`（可选，默认 15min）
* 返回字段：`predict_list[]` 每项含 `time` / `position` / `predict_value` / `confidence_min` / `confidence_max` / `status`；另含 `model`（placeholder → prophet/lstm）与 `total`
* 状态标注：`normal`（正常）/ `warning`（预警）/ `abnormal`（异常）
* 当前状态：**占位**——契约 v1 字段结构保留，但实现固定返回 `model="placeholder"` / `predict_list=[]` / `total=0`，**不调用**任何预测模型（也不再转调接口 10）。需要真实预测请用接口 10 或接口 11

#### 接口 9：拓扑增强数据（GET /api/network/topology/enhanced）

* 请求参数：`scenario`（必填）、`time`（可选，默认最新时刻）、`include_metrics`（可选，默认 true）
* 返回字段：`nodes[]`（AP 带 `rsrp_avg` / `prb_usage` / `active_users` 属性，UE 带 `rsrp` / `sinr` / `throughput` 当前指标）+ `links[]`（`metrics` 含 rsrp / sinr / throughput / latency / packet_loss）+ `stats`（节点数 / 链路数 / 平均 RSRP / SINR）
* 当前状态：**已实现**（`main.py` 的 `get_enhanced_topology`，读网络表按指定时刻生成节点/链路/统计；契约 v1 字段保持不变）

## 数据字典（CSV 格式，18 列）

| 列名                 | 含义             | 单位                                |
| -------------------- | ---------------- | ----------------------------------- |
| timestamp            | 采样时间         | yyyy-MM-dd HH:mm:ss                 |
| ap_id                | 接入点编号       | AP-01 / AP-02 / AP-03               |
| ap_x / ap_y          | AP 坐标          | m                                   |
| user_id              | 用户编号         | UE-01 ~ UE-03                       |
| ue_x / ue_y          | UE 坐标          | m                                   |
| rsrp                 | 参考信号接收功率 | dBm                                 |
| sinr                 | 信干噪比         | dB                                  |
| throughput           | 吞吐量           | Mbps                                |
| latency              | 时延             | ms                                  |
| packet_loss          | 丢包率           | %                                   |
| prb_usage            | PRB 利用率       | %                                   |
| scenario             | 场景标签         | 校园 / 商场 / 体育馆                |
| network_status       | 网络状态         | normal / congestion / weak_coverage |
| retransmission_count | 重传次数         | 次                                  |
| traffic_demand       | 业务需求         | Mbps                                |
| active_users         | 活跃用户数       | 人                                  |

> 完整字段规范（含故障注入说明）见 [docs/data_dict.md](docs/data_dict.md)。

## 目录结构

```
AI-NetTune/
├── main.py            # 后端主程序（FastAPI，17 个接口）
├── data_layout.py     # 数据文件定位规则（候选文件名 / 环境变量，单一定义源）
├── data_generator.py  # 仿真数据生成器（v2.8：多日 + 坐标 + 故障注入 + 频谱混合标注）
├── forecast_model.py  # Prophet 时序预测（缺包降级 moving_average）+ 真实 MAE/RMSE/MAPE 回测
├── spatial_model.py   # Kriging 空间估计（缺包降级 grid_mean）
├── spectrum_frames.py # 频率维度帧序列（分频段功率场的逐频带趋势外推）
├── spatial_frames.py  # 区域维度帧序列（IDW 插值 + 逐格点趋势外推，接口15）
├── signal_nav.py      # 信号导航（位置感知 + 覆盖分析 + 移动建议，接口16）
├── plan_model.py      # 处置方案生成与对比仿真（原配置 vs 方案）
├── report_model.py    # 报告渲染（Markdown / HTML）
├── llm_agent.py       # LLM 意图解析 + 规则兜底
├── requirements.txt   # 后端依赖（requirements-min.txt 为不含 prophet/pykrige 的最小集）
├── DEPLOY.md          # 部署手册：数据准备 / 环境变量 / 反向代理 / 故障排查
├── scripts/
│   ├── doctor.py      # 部署自检（Python/依赖/数据/时间轴/LLM）
│   ├── audit_data.py  # 数据对账（两表异常组/渐变征兆/阈值自洽/占用率语义）
│   ├── find_dead_code.py # 前端死代码分析（可达性扫描 + 删除命令）
│   ├── prepare_data.py # 规整 data/ 下的 "(1)" 文件名
│   └── start_backend.ps1 # Windows 一键启动（自动挑 ≥3.10 解释器，-Reload / -Port）
├── tests/
│   ├── run_tests.py   # 零依赖测试运行器（不需要 pytest / httpx）
│   └── test_contract.py  # 接口契约测试（69 项，以 run_tests 输出为准）
├── data/              # 数据文件（.gitignore 忽略大文件，仅保留 README.md）
│   ├── README.md              # 数据交付清单（大小 / SHA256 / 时间范围）
│   ├── network_data_30d.csv   # 30 天，129,600 条 × 18 列（后端默认读取）
│   ├── spectrum_7d.csv        # 7 天，1,512,000 条 × 9 列（预测预警数据源）
│   └── fault_events.csv       # 故障事件清单（接口7 的单一事实来源，8 个事件）
├── docs/
│   ├── api.md         # API 接口契约文档（17 个接口）
│   └── data_dict.md   # 数据字典契约文档（18 维字段规范）
├── frontend/          # React 前端
└── README.md
```

> `data/` 下的大文件不进 Git（合计约 120 MB），全新 clone 后需按 [data/README.md](data/README.md) 放齐。

## 开发路线

| 周次    | 目标                                                         |
| ------- | ------------------------------------------------------------ |
| 第 1 周 | 接口契约 + 后端骨架打通（✅ 已完成）                          |
| 第 2 周 | 仿真数据生成（多日 + AP/UE 坐标 + 故障注入）+ 真实研判逻辑 + API 降级 + 热力图/拓扑接口（✅ 已完成 P0 主体） |
| 第 3 周 | LLM 意图驱动对话 + 多模态可视化（频谱地图 / 信号导航 / **拓扑图** / **真实vs预测对比**）+ 时序预测（**Prophet**，替代移动平均 baseline；LSTM 仅规划未实现）+ 前后端联调 |
| 第 4 周 | 打磨、部署与稳定性、演示与答辩准备                           |

### 拓扑图实现方案（三人协作）

| 步骤 | 内容                                                         | 负责人                  |
| ---- | ------------------------------------------------------------ | ----------------------- |
| 1    | CSV 扩展 `ap_x/ap_y`、`ue_x/ue_y` 坐标（✅ 已完成 v2.3）      | 唐苗                    |
| 2    | 接口 6 `GET /api/network/topology`：返回 `nodes`（AP/UE 的 id、type、x、y、rsrp、sinr、throughput）+ `links`（UE 与所属 AP 的连接，含 RSSI） | 唐苗                    |
| 3    | ECharts `graph` / `scatter` 拓扑图：AP 用三角/方块、UE 用圆点，图例 + X/Y 轴（m）；点选 UE 弹参数面板 | 姚欣彤                  |
| 4    | 点选 UE → 右侧联动"真实 vs 预测"对比图（Signal / Interference / Rate，橙=真实、蓝=预测） | 姚欣彤 + 程思源（口径） |
| 5    | 区域电磁频谱热力图层叠加到拓扑底图                           | 姚欣彤                  |

### 预测模块优先级（P0 / P1 / P2）

| 层级 | 内容                                                         |
| ---- | ------------------------------------------------------------ |
| P0   | 基础功能闭环（数据 / KPI 查询 / 研判 / LLM 意图 / 对话链路 / 基础 UI）——预测不阻塞，不加 |
| P1   | ✅ **已完成**：①接口 10 返回真实 Prophet / 移动平均结果与逐点 MAE / RMSE，「真实 vs 预测对比」已接入；②**帧序列**通过接口 15 `/api/forecast/spatial_frames` 提供（过去 8 帧 + 预测 6 帧，二维区域场），前端以「大图 + 时间轴滑块 + 自动播放 + 缩略帧条」呈现序列态势图，支持 30 分钟 / 1 小时 / 2 小时 / 4 小时 / 6 小时查看范围切换（6 小时以上未做回测，主界面已隐藏；接口上限 1440 分钟）；③拓扑图 UE 点选联动对比已接入；④信号导航（接口 16 `/api/navigate/signal`）提供位置感知 + 覆盖分析 + 移动建议。**说明**：`past_frames`/`future_frames` 字段名未沿用，改由接口 15 以 `past[]/future[]` 提供，语义等价 |
| P2   | 帧间**播放动画**、渐进过渡、地图缩放交互、指标面板美化（P0 未完成前不碰 P2） |

## 团队成员

| 成员                 | 分工                                                         |
| -------------------- | ------------------------------------------------------------ |
| 程思源（队长）       | AI 智能层与通信研判：LLM 意图理解（harness检验）、Function Calling 与工具链编排、三类故障研判引擎（多候选原因与证据链）、区域信号/频谱态势预测与空间估计、配置方案对比验证与通信分析规则、系统总体架构与演示答辩统筹 |
| 唐苗                 | 数据层与后端接口：仿真数据生成与校验（八类核心数据）、仿真实时数据流、后端 API 与数据服务、LLM 调用接口及 API 降级、报告导出/预测接口的契约与数据支撑、部署与代码评审,报告导出与预测结果接口等外围任务 |
| 姚欣彤               | 前端可视化层：React 页面、ECharts 多模态可视化（KPI / 对话结果 / 热力图 / 拓扑 / 序列态势图 / 预测结果 / 证据链图 / 方案对比页）、端到端联动、UI 美化与演示演练 |

## 参考

* 无线网络优化智能体类项目（LLM 驱动的网络拓扑 + 指标对比可视化）：本项目以其为场景参考，聚焦"监测 + 研判 + 动态管控"差异化方向。
