# AI-NetTune 部署手册

> 目标：换一台干净的机器，照着这一份文档就能把前后端跑起来。
> 适用：Windows 10/11（macOS/Linux 命令基本一致，只需替换路径写法）。
> 遇到问题先看第 9 节「故障排查」，再跑 `python scripts/doctor.py`。

---

## 1. 准备清单

| 项 | 版本要求 | 说明 |
|---|---|---|
| Python | **3.13**（最低 3.10） | 后端；本项目在 3.13.10 上验证 |
| Node.js | **20+**（本项目用 24） | 前端构建 |
| 数据文件 | 见第 2 节 | **仓库里没有**（108 MB 频谱表不适合进 Git） |

---

## 2. 第一步：放数据（最容易漏的一步）

### 2.1 需要哪些文件

后端启动时会自动在 `data/` 目录下按候选文件名探测。**最少需要这两个**：

| 文件名 | 大小 | SHA256（前 16 位） | 缺失后果 |
|---|---|---|---|
| `network_data_30d.csv` | 14.6 MB | `E4D8547AEEC1199C` | 接口 1/2/5/6/7/9 与回放全部返回空数据 |
| `spectrum_7d.csv` | 103.6 MB | `14E6649284E0217F` | 预测预警降级为 `insufficient_data` |

可选（演示备用，后端不会自动读）：

| 文件名 | 大小 | 用途 |
|---|---|---|
| `network_data_7d.csv` | 3.4 MB | 7 天版网络表，与频谱表时间范围完全一致 |
| `network_data.csv` | 1.5 MB | 3 天版，早期的 4 段故障注入数据 |

校验整套文件（含逐文件 SHA256）：

```powershell
Get-ChildItem data\*.csv | ForEach-Object {
  "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.Substring(0,16), $_.Name
}
```

### 2.2 文件从哪来

数据由 `data_generator.py` 生成，或从团队共享盘/发布包获取。仓库 `.gitignore` 忽略了 `data/`，所以**全新 clone 后 data 目录是空的**——这是最常见的"服务起来了但所有接口都返回空数组"的原因。

### 2.3 文件名规范化（重要）

Windows 重复下载同名文件会得到 `network_data_30d(1).csv`。旧版代码把这个 `(1)` 硬编码进了路径，换一次下载就读不到数据。

现在后端已兼容两种名字（见 `data_layout.py` 的候选列表），但仍建议整理成规范名：

```powershell
python scripts/prepare_data.py           # 预览要改什么
python scripts/prepare_data.py --apply   # 真正重命名
```

### 2.4 自定义位置（可选）

不想放 `data/` 时，用环境变量指定（相对路径按项目根目录解析）：

```powershell
$env:NETTUNE_DATA_DIR    = "D:\AI-NetTune-data"          # 整个数据目录换位置
$env:NETWORK_DATA_PATH   = "D:\data\net_30d.csv"          # 单独指定网络表
$env:SPECTRUM_DATA_PATH  = "D:\data\spectrum_7d.csv"      # 单独指定频谱表
```

---

## 3. 第二步：装后端依赖

```powershell
cd F:\AI-NetTune\AI-NetTune
python -m pip install -r requirements.txt
```

**装不上 prophet / PyKrige 时**（需要编译 Stan 模型，国内网络/缺少 C++ 工具链都可能失败）：

```powershell
python -m pip install -r requirements-min.txt
```

此时后端仍可正常启动，只是能力降级，并且 `/api/health` 会如实标注：

| 缺什么 | 影响 | 自动降级为 |
|---|---|---|
| `prophet` | 接口 10 时序预测 | `method=moving_average` |
| `PyKrige` | 接口 5 空间估计 | `method=grid_mean`（网格均值） |

> 说明：`spatial_model.py` 与 `forecast_model.py` 都做了导入保护，**缺包不会导致服务起不来**。

---

## 4. 第三步：启动后端

```powershell
cd F:\AI-NetTune\AI-NetTune
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

- `--host 0.0.0.0`：允许局域网其他设备访问（答辩多机演示必须）。
- 开发时可以加 `--reload`（需要 `watchfiles`，`requirements.txt` 里的 `uvicorn[standard]` 已包含）。
- 启动会打印**启动自检横幅**，直接告诉你数据是否到位：

```
========================================================================
[启动自检] AI-NetTune backend
  数据目录 : F:\AI-NetTune\AI-NetTune\data
  网络表   : ...\data\network_data_30d.csv (129600 行)
  频谱表   : ...\data\spectrum_7d.csv (1512000 行, 至 2026-01-07 23:59:00)
  能力     : prophet=on kriging=on llm=off(降级规则意图)
========================================================================
```

**验收三个地址**：

| 地址 | 期望 |
|---|---|
| http://127.0.0.1:8000/api/health | `data.ok = true`，两个 `rows > 0` |
| http://127.0.0.1:8000/docs | Swagger 面板，13 个接口 |
| http://127.0.0.1:8000/api/data/query?scenario=校园&start_time=2026-01-01%2010:00:00&end_time=2026-01-01%2010:05:00 | `total > 0` |

---

## 5. 第四步：启动前端（开发模式，最省事）

```powershell
cd F:\AI-NetTune\AI-NetTune\frontend
npm install
npm run dev
```

浏览器打开 http://localhost:5173 。

- 前端所有请求走相对路径 `/api/...`，由 `vite.config.ts` 的 proxy 转到 `http://127.0.0.1:8000`。
- 改后端端口：复制 `.env.example` 为 `.env.local`，设置 `VITE_PROXY_TARGET=http://127.0.0.1:8001`。
- `server.host: true` 已开启，局域网设备可用 `http://<你的内网IP>:5173` 访问。

### 跨域（CORS）：局域网访问时必须改

`main.py` 的允许来源是写死的：

```python
allow_origins=["http://localhost:5173", "http://10.63.118.174:5173"]
```

用别的机器/别的 IP 访问前端时，**把这台机器的地址加进去**，否则浏览器会报 CORS 错误。
（答辩机器固定的话，提前改好并提交。）

---

## 6. 第五步（可选）：构建产物部署

`vite build` 的产物**不带任何代理配置**，所以必须二选一：

### 方案 A：同源反向代理（推荐，最稳）

前端和后端放同一个域名下，由 nginx 把 `/api` 转给后端；`.env` 保持 `VITE_API_BASE` 为空。

```nginx
server {
    listen 80;
    server_name ai-nettune.local;

    # 前端静态产物
    root  /srv/ai-nettune/dist;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }

    # 后端 API
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_read_timeout 120s;   # 预测接口单次约 1 秒，留足余量
    }
}
```

### 方案 B：前后端分开部署，直接指定后端地址

```powershell
cd frontend
$env:VITE_API_BASE = "http://192.168.1.10:8000"   # 后端地址
npm run build
# 产物在 frontend/dist，丢到任意静态服务器
```

> ⚠️ `VITE_API_BASE` 会在**构建时**内联进 JS，改地址必须重新构建。

### 方案 C：只想快速验证产物

```powershell
npm run build
npm run preview        # 注意：preview 不读 vite.config.ts 的 proxy，仍需方案 A 或 B
```

---

## 7. 环境变量总表

| 变量 | 作用域 | 默认 | 说明 |
|---|---|---|---|
| `NETTUNE_DATA_DIR` | 后端 | `<项目根>/data` | 数据目录整体换位置 |
| `NETWORK_DATA_PATH` | 后端 | 自动探测 | 网络表文件，优先级最高 |
| `SPECTRUM_DATA_PATH` | 后端 | 自动探测 | 频谱表文件，优先级最高 |
| `LLM_API_KEY` | 后端 | 空 | 空 → 意图解析与研判降级为规则模板 |
| `LLM_URL` | 后端 | `https://api.deepseek.com/chat/completions` | OpenAI 兼容接口均可 |
| `LLM_MODEL` | 后端 | `deepseek-chat` | 模型名 |
| `NETTUNE_ENABLE_LLM` | 后端 | `1` | 设为 `0` 强制走纯规则链路 |
| `VITE_API_BASE` | 前端（构建时） | 空 | 空 = 同源 `/api`；填完整地址则直连后端 |
| `VITE_PROXY_TARGET` | 前端（dev） | `http://127.0.0.1:8000` | 仅 `npm run dev` 生效 |

设置 LLM（Windows，永久生效，需重开终端）：

```powershell
setx LLM_API_KEY "sk-你的key"
```

> 不要把 key 写进代码或 `.env` 提交到仓库。

---

## 8. 一键自检

```powershell
python scripts/doctor.py            # 全套检查（读 108MB 频谱表，约 5-10 秒）
python scripts/doctor.py --quick    # 跳过频谱表深度统计（更快）
```

检查项：Python 版本 / 8 个依赖 / 数据文件与列名 / 行数与时间范围 / 场景与状态分布 /
**两表时间轴是否重叠** / LLM 配置。退出码 `0` = 可启动，`1` = 有阻断问题。

---

## 9. 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| 服务起来了，但接口返回 `total: 0`、`records: []` | `data/` 没有网络表，或文件名不匹配 | 跑 `python scripts/doctor.py`；按第 2 节放文件 |
| 预测弹窗显示"暂不能预测 / 数据不足" | 频谱表缺失，或 `baseTime` 超出频谱范围 | 频谱只到 `2026-01-07 23:59`；演示请把回放范围控制在 01-01~01-07（前端 `replay/config.ts` 默认就是这段） |
| 浏览器里所有接口 404 | 用了 `npm run build` 的产物但没配反向代理 | 见第 6 节方案 A 或 B |
| 浏览器报 CORS 错误 | `main.py` 的 `allow_origins` 没包含当前访问地址 | 把该地址加进 `allow_origins` 后重启后端 |
| 对话回复"未找到场景「XX」" | 数据只有「校园」一个场景 | 前端场景只选「校园」；或补生成商场/体育馆数据 |
| 对话/研判迟迟不返回（约 30 秒） | 配了 `LLM_API_KEY` 但网络不通 | 检查外网；或 `setx NETTUNE_ENABLE_LLM 0` 走纯规则 |
| `/api/data/heatmap` 返回 `method=grid_mean` | 未装 PyKrige，或采样点 < 30 | 正常降级；需要 Kriging 就装 PyKrige |
| `/api/analyze/forecast` 很慢或超时 | Prophet 首次拟合要编译 Stan 模型 | **演示时不要直接调接口 10**；前端预测走 `/api/forecast/preview`，不受影响 |
| 报告下载 404 | 导出目录与静态挂载目录不一致 | 现已统一为 `<项目根>/reports`（绝对路径），确认进程对项目根有写权限 |
| 中文在终端显示乱码 | 终端编码不是 UTF-8 | `chcp 65001`，或用 Windows Terminal / VS Code 终端 |

---

## 10. 交付前自查清单

- [ ] `data/network_data_30d.csv`、`data/spectrum_7d.csv` 已到位（合计约 120 MB）
- [ ] `python scripts/doctor.py` 退出码 0
- [ ] `GET /api/health` 的 `data.ok` 为 `true`
- [ ] `GET /docs` 能看到 13 个接口
- [ ] 前端 `npm run dev` 打开后，工作台能出图、AI 区能问答
- [ ] 预测弹窗能打开且 `status=ready`
- [ ] `main.py` 的 `allow_origins` 包含答辩机器的访问地址
- [ ] `LLM_API_KEY` 已配置（或明确接受降级）
