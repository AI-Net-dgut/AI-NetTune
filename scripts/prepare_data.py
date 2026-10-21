#!/usr/bin/env python
"""把浏览器重复下载产生的 "xxx(1).csv" 重命名为规范文件名。

背景：Windows 下重复下载同名文件会得到 `network_data_30d(1).csv`。
旧版 main.py 把这个 "(1)" 硬编码进了路径，导致换一次下载就静默读不到数据。
现在 main.py 已能兼容两种名字（见 data_layout.py），本脚本只是把目录整理成
规范名，便于交接和排错。

用法（在项目根目录执行）：
    python scripts/prepare_data.py            # 只预览，不改动
    python scripts/prepare_data.py --apply    # 真正重命名

安全策略：
  * 规范名已存在时**不覆盖**，只报告重复，由你决定删哪个；
  * 默认 dry-run，必须显式 --apply 才动文件。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data_layout import DATA_DIR  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 只处理这几个数据家族，避免误伤其他文件
FAMILIES = (
    "network_data",
    "network_data_3d",
    "network_data_7d",
    "network_data_30d",
    "spectrum",
    "spectrum_7d",
    "spectrum_30d",
)
# 形如 "network_data_30d(1).csv" / "network_data_30d (2).csv"
SUFFIX_RE = re.compile(r"^(?P<base>.+?)\s*\(\d+\)(?P<ext>\.csv)$", re.IGNORECASE)


def plan_moves() -> tuple[list[tuple[Path, Path]], list[str]]:
    moves: list[tuple[Path, Path]] = []
    notes: list[str] = []
    if not DATA_DIR.is_dir():
        return moves, [f"数据目录不存在：{DATA_DIR}"]

    for f in sorted(DATA_DIR.iterdir()):
        if not f.is_file():
            continue
        m = SUFFIX_RE.match(f.name)
        if not m:
            continue
        base = m.group("base")
        if base not in FAMILIES:
            continue
        target = f.with_name(base + m.group("ext").lower())
        if target.exists():
            notes.append(f"跳过 {f.name}：规范名 {target.name} 已存在，请自行确认保留哪一个")
            continue
        moves.append((f, target))

    return moves, notes


def main() -> int:
    ap = argparse.ArgumentParser(description="规范化 data/ 下的数据文件名")
    ap.add_argument("--apply", action="store_true", help="真正执行重命名（默认只预览）")
    args = ap.parse_args()

    print(f"数据目录：{DATA_DIR}")
    moves, notes = plan_moves()

    for note in notes:
        print(f"  [注意] {note}")

    if not moves:
        print("  [OK] 没有需要规范化的文件名。")
        return 0

    print(f"\n计划重命名 {len(moves)} 个文件：")
    for src, dst in moves:
        size = src.stat().st_size / 1024 / 1024
        print(f"  {src.name}  ->  {dst.name}   ({size:.1f} MB)")

    if not args.apply:
        print("\n以上为预览。确认无误后执行：python scripts/prepare_data.py --apply")
        return 0

    failed = 0
    for src, dst in moves:
        try:
            src.rename(dst)
            print(f"  [已重命名] {dst.name}")
        except OSError as exc:
            failed += 1
            print(f"  [失败] {src.name} -> {dst.name}：{exc}")
            print("         常见原因：文件被 Excel/资源管理器占用，或当前用户对该目录没有写权限。")

    if failed:
        print(f"\n{failed} 个文件未能重命名。main.py 仍可用旧名字启动（候选名兼容），但建议修好后重跑。")
        return 1

    print("\n[完成] 文件名已规范化，main.py 会优先使用规范名。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
