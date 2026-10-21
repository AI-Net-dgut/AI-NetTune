# data/ 数据交付说明

> 本目录的大文件**不纳入 Git**（`.gitignore` 忽略 `data/`，仅保留本说明文件）。
> 部署前请按本文件把数据放齐，然后跑 `python scripts/doctor.py` 自检。

## 必需文件

| 文件名 | 行数 × 列数 | 大小 | SHA256 |
|---|---|---|---|
| `network_data_30d.csv` | 129,600 × 18 | 14.6 MB | `E4D8547AEEC1199C570FCD85783EBBF3117D46D5DA664BDA12D5CA7B46B8892B` |
| `spectrum_7d.csv` | 1,512,000 × 9 | 103.6 MB | `14E6649284E0217FF2E8DC304F6080FBAC1CDD645FDA4E68700108362E640F08` |

时间范围：

- `network_data_30d.csv`：`2026-01-01 00:00:00` ~ `2026-01-30 23:59:00`，场景仅「校园」
- `spectrum_7d.csv`：`2026-01-01 00:00:00` ~ `2026-01-07 23:59:00`，3 AP × 50 频段

> ⚠️ **两表时间范围不一致**：01-08 ~ 01-30 只有网络数据、没有频谱数据，
> 这段时间调用 `/api/forecast/preview` 会返回 `insufficient_data`。
> 前端回放默认范围是 01-01 ~ 01-07（`frontend/src/replay/config.ts`），与频谱对齐——演示请勿越界。

## 可选文件（演示备用，后端不会自动读取）

| 文件名 | 行数 × 列数 | 大小 | 用途 |
|---|---|---|---|
| `network_data_7d.csv` | 30,240 × 18 | 3.4 MB | 7 天版，与频谱时间范围完全一致 |
| `network_data.csv` | 12,960 × 18 | 1.5 MB | 3 天版（早期 4 段故障注入数据） |

## 历史遗留文件（建议清理，不要用于演示）

| 文件名 | 问题 |
|---|---|
| `network_data_3d_bak.csv` | 与 `network_data.csv` **SHA256 完全相同**，纯冗余 |
| `network_data_backup_20260929.csv` | **旧格式，只有 11 列**；吞吐量出现 799.9 Mbps、时延 2~200 ms、10 个 UE（`UE-001` 命名），与 `docs/data_dict.md` 的 18 列规范不符 |

## 文件从哪来

1. 团队共享盘 / 发布包（推荐，保证与演示机一致）；
2. 或由 `data_generator.py` 重新生成：

```powershell
python data_generator.py --days 30          # 生成网络表
python data_generator.py --days 7 --events  # 生成与事件对齐的频谱表
```

> 注意：生成器当前**没有 `--seed`**，每次生成的数据都不同；
> 重新生成后两表之间的对齐关系需要重新核对（见审查报告 D-1/D-8）。

## 文件名规范化

Windows 重复下载会得到 `network_data_30d(1).csv` 这类名字。后端已兼容（见 `data_layout.py` 的候选列表），
但建议整理成规范名：

```powershell
python scripts/prepare_data.py            # 预览
python scripts/prepare_data.py --apply    # 执行
```

## 校验

```powershell
python scripts/doctor.py     # 行数 / 列名 / 时间范围 / 状态分布 / 两表重叠
```

或直接比对 SHA256：

```powershell
Get-ChildItem data\*.csv | ForEach-Object {
  "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash, $_.Name
}
```

## 是否要把数据放进 Git？

不建议直接提交：单个 103.6 MB 的 CSV 会显著拖慢 clone/push，且多数平台对单文件有硬限制。
两种可行做法见 `DEPLOY.md` 第 2 节：

1. **发布包/共享盘交付**（当前做法，推荐）；
2. **Git LFS**（团队坚持要 `git clone` 即得数据时）：
   ```powershell
   git lfs install
   git lfs track "data/*.csv"
   git add .gitattributes
   git add data/network_data_30d.csv data/spectrum_7d.csv
   git commit -m "chore(data): 用 LFS 纳入演示数据"
   ```
