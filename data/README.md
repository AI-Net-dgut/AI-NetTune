# data/ 数据交付说明

> 本目录的大文件**不纳入 Git**（`.gitignore` 忽略 `data/`，仅保留本说明文件）。
> 部署前请按本文件把数据放齐，然后跑 `python scripts/doctor.py` 自检。

## 必需文件

| 文件名 | 行数 × 列数 | 大小 | SHA256 |
|---|---|---|---|
| `network_data_7d.csv` | 30,240 × 18 | 3.4 MB | `D4B18FC1A84BFFD73518FBB4C57BDB85D71CC8B0B30A95CB7C8826D13B0D7766` |
| `network_data_30d.csv` | 129,600 × 18 | 14.6 MB | `8878D43BDD7BF7DADEB58662EC62BFE4AF3AFA20896154CA9F7238A7D7880CA1` |
| `spectrum_7d.csv` | 1,512,000 × 9 | 103.4 MB | `23CA3B6D6730853F03967227B18AC641B9529DFBF5240D4B211B169B5FB86C73` |
| `fault_events.csv` | 8 × 9 | 1 KB | `37F425FFBBFE21302A70E0FFC171016C888E42F21A0AA971341F9CFE8C890DEF` |

> **哈希口径（2026-10 更新）**：上表为**仓库内当前文件**的实测 SHA256（Windows `Get-FileHash -Algorithm SHA256`）。
> 数据包曾被替换过两版，旧版本登记哈希（`5BFD190C…` / `F20F798E…` / `255BDDE7…`）已作废。
> **一致性判断以本表为准**：语义自洽性由 `scripts/audit_data.py` 校验（当前 一致 9 项 / 不符 0 项），
> 逐字节一致性以本表 SHA256 为准。如果拿到的是旧包，跑一遍 `python scripts/audit_data.py` 即可确认语义是否仍自洽。

**`fault_events.csv` 是"事件"的单一事实来源**（v2.8 新增，补上审查报告 D-2）：

| 列 | 说明 |
|---|---|
| `event_id` | EV-001 起顺序编号 |
| `scenario` | 场景（当前只有「校园」） |
| `ap_id` | 涉及 AP |
| `fault_type` | `weak_coverage` / `congestion` |
| `start_time` / `end_time` | 事件起止（绝对时间，与 seed 无关） |
| `ramp_minutes` | 渐入/渐出分钟数（当前 20） |
| `strength` | 弱覆盖=额外衰减 dB；拥塞=目标 PRB % |
| `labeled_from_expect` | 说明"何时才会打上异常标签"：渐变期未达阈值时标签仍为 normal |

> 此前该文件从未生成，`EV-001~004` 只存在于交接文档正文里；前端
> `CASE_WINDOWS` 的案例起点是手写时间戳，改数据即失效。现在生成器每次运行都会
> 同步输出本表，前端可直接读它来确定案例窗口。

**数据版本：v2.8（2026-10-21 重新生成）**

- 随机种子 `--seed 42`，**可逐字节复现**
- **30 天表的前 7 天与 7 天表完全一致**（同 seed 顺序生成，逐行 100% 相同）
- 事件前后有 **20 分钟渐入/渐出**（`RAMP_MINUTES=20`），事件起止时刻不变
- 两表异常组**完全对齐（差异 0/0）**

时间范围：

- `network_data_7d.csv`：`2026-01-01 00:00:00` ~ `2026-01-07 23:59:00`，场景仅「校园」
- `network_data_30d.csv`：`2026-01-01 00:00:00` ~ `2026-01-30 23:59:00`
- `spectrum_7d.csv`：`2026-01-01 00:00:00` ~ `2026-01-07 23:59:00`，3 AP × 50 频段

> ⚠️ **频谱只有 7 天**。01-08 之后的时刻调用 `/api/forecast/preview` 会返回 `insufficient_data`。
> 前端回放默认范围是 01-01 ~ 01-07（`frontend/src/replay/config.ts`），与频谱对齐——演示请勿越界。

## 关于"渐变征兆"（v2.8 新增）

旧数据（v2.7）的故障是**单分钟阶跃**：事件前 25 分钟 RSRP 低于 −105 dBm 的样本数为 0，
因此"提前 N 分钟预警"在数据上不可能触发。

v2.8 做了两件事：

1. **网络表**：事件强度按 20 分钟线性渐入/渐出（起止时刻不变）。
2. **频谱表**：功率与占用率不再只按标签二值跳变，而是按网络表的 RSRP/PRB 连续映射"严重度"。
   未被标注为异常的帧，严重度封顶（weak 0.80 / congestion 0.75），
   **保证它们留在阈值以上**（功率 ≥ −95 dBm、占用率 < 85%），只体现"正在恶化"。

效果（实测）：基准时刻 `2026-01-01 10:08` 会给出
「AP-02 预计 14 分钟后功率跌破 −95 dBm」，而实际越阈发生在约 10:22。

## 可选文件（演示备用，后端不会自动读取）

| 文件名 | 行数 × 列数 | 说明 |
|---|---|---|
| `network_data.csv` | 12,960 × 18 | 3 天版（旧），仅作历史对照 |

## 历史遗留与可清理项（不要用于演示）

> 2026-10 核对：下面旧条目对应的文件**已不存在**，此处改为记录"当前目录里真正占盘的东西"。

| 对象 | 现状 | 处理 |
|---|---|---|
| `data/network_data.csv`（1.5 MB，12,960 行） | 旧 3 天表，**仍被 Git 跟踪**；`.gitignore` 的 `data/*` 已匹配它（`git check-ignore --no-index` 可验证），只是历史提交带进来了 | 执行 `git rm --cached data/network_data.csv` 后提交：本地文件保留，不再入库 |
| `data_backup_20261021_2342` 等 **5 个备份目录**（仓库根下，共约 626 MB） | 换数据时的自动备份，已被 `.gitignore` 的 `data_backup_*/` 忽略，不占 Git | 确认 `network_data_30d.csv` / `spectrum_7d.csv` 正常后可直接删除，释放约 626 MB |
| `network_data_3d_bak.csv`、`network_data_backup_20260929.csv` | **已不存在**（旧文档遗留描述） | 无需处理 |

清理命令（在仓库根执行；涉及 Git 的请先确认再提交）：

```powershell
git rm --cached data/network_data.csv     # 只从索引移除，本地文件保留
git commit -m "chore: 停止跟踪旧 3 天网络表（data/ 已由 .gitignore 覆盖）"

Remove-Item -Recurse -Force data_backup_2026102*   # 释放约 626 MB（已忽略，不影响 Git）
```

## 文件从哪来

1. 团队共享盘 / 发布包（推荐，保证与演示机一致）；
2. 或由 `data_generator.py` 重新生成（**必须带同一个 seed**，否则数值全变、对账结论失效）：

```powershell
# 7 天网络表 + 7 天频谱表（频谱状态对齐网络表）
python data_generator.py --days 7  --seed 42 --events data/network_data_7d.csv

# 30 天网络表（前 7 天会与上面 7 天表逐字节一致）
python data_generator.py --days 30 --seed 42 --events data/network_data_30d.csv
```

> 注意：第二次运行同样会生成 30 天频谱（约 460 MB，本项目不使用），可自行删除。

## 文件名规范化

Windows 重复下载会得到 `network_data_7d(1).csv` 这类名字。后端已兼容（见 `data_layout.py` 的候选列表），
但建议整理成规范名：

```powershell
python scripts/prepare_data.py            # 预览
python scripts/prepare_data.py --apply    # 执行
```

## 校验

```powershell
python scripts/doctor.py       # 行数 / 列名 / 时间范围 / 状态分布 / 两表重叠
python scripts/audit_data.py   # 两表异常组对账 / 渐变 / 阈值自洽 / 占用率语义
```

或直接比对 SHA256：

```powershell
Get-ChildItem data\*.csv | ForEach-Object {
  "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash, $_.Name
}
```

## 是否要把数据放进 Git？

不建议直接提交：单个 103.5 MB 的 CSV 会显著拖慢 clone/push，且多数平台对单文件有硬限制。
两种可行做法见 `DEPLOY.md` 第 2 节：

1. **发布包/共享盘交付**（当前做法，推荐）；
2. **Git LFS**（团队坚持要 `git clone` 即得数据时）：
   ```powershell
   git lfs install
   git lfs track "data/*.csv"
   git add .gitattributes
   git add data/network_data_7d.csv data/network_data_30d.csv data/spectrum_7d.csv
   git commit -m "chore(data): 用 LFS 纳入演示数据"
   ```
