#!/usr/bin/env python
"""找出前端真正没被引用的文件，并生成可直接执行的删除命令。

为什么需要它：仓库里积压了约 2000 行死代码（旧拓扑图、旧 KPI 组件、旧 mock、
字节级重复文件等），靠人肉 grep 很容易删错。这个脚本从入口 `src/main.tsx`
做一次可达性分析，只报告**从入口走不到**的文件。

用法：
    python scripts/find_dead_code.py              # 只看报告
    python scripts/find_dead_code.py --commands    # 额外输出 PowerShell 删除命令

注意：脚本只读不删。删除前建议先 `git status` 确认工作区干净，删完跑
`npx tsc -p tsconfig.app.json --noEmit` 和 `npm run build` 验证。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "frontend"
SRC = ROOT / "src"
ENTRY = SRC / "main.tsx"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 覆盖 `import x from "y"` / `import "y"` / `import("y")` / `export ... from "y"`
IMPORT_RE = re.compile(r"""(?:from|import)\s*\(?\s*['"]([^'"]+)['"]""")
SUFFIXES = (".tsx", ".ts", ".jsx", ".js", ".css", ".json")


def resolve(spec: str, base: Path) -> Path | None:
    if not spec.startswith("."):
        return None                      # 裸包名，跳过
    target = (base / spec).resolve()
    candidates = [target]
    candidates += [target.with_name(target.name + s) for s in SUFFIXES]
    candidates += [target / f"index{s}" for s in SUFFIXES]
    for c in candidates:
        if c.is_file():
            return c
    return None


def scan() -> dict[Path, set[Path]]:
    graph: dict[Path, set[Path]] = {}
    files = [p for p in SRC.rglob("*") if p.is_file() and p.suffix in SUFFIXES]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        deps = set()
        for spec in IMPORT_RE.findall(text):
            hit = resolve(spec, f.parent)
            if hit is not None:
                deps.add(hit)
        graph[f] = deps
    return graph


def main() -> int:
    ap = argparse.ArgumentParser(description="前端死代码分析")
    ap.add_argument("--commands", action="store_true", help="输出 PowerShell 删除命令")
    args = ap.parse_args()

    if not ENTRY.is_file():
        print(f"找不到入口 {ENTRY}")
        return 1

    graph = scan()
    seen: set[Path] = set()
    stack = [ENTRY.resolve()]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(graph.get(cur, ()))

    all_files = set(graph)
    dead = sorted(all_files - seen, key=lambda p: p.relative_to(ROOT).as_posix())
    # 只删代码/样式；保留 d.ts（类型声明可能被 tsconfig 隐式加载）
    dead = [p for p in dead if not p.name.endswith(".d.ts")]

    print("=" * 78)
    print("前端死代码分析（从 src/main.tsx 做可达性分析）")
    print(f"源文件总数 {len(all_files)}，可达 {len(seen)}，不可达 {len(dead)}")
    print("=" * 78)

    if not dead:
        print("\n没有发现死代码。")
        return 0

    total = 0
    print("\n未被引用的文件：")
    for p in dead:
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        total += size
        rel = p.relative_to(ROOT).as_posix()
        print(f"  {size / 1024:>7.1f} KB  {rel}")
    print(f"\n合计 {len(dead)} 个文件，约 {total / 1024:.0f} KB（{total / 1024 / 1024:.2f} MB）")

    print("\n提示：")
    print("  * `.css` 只有在对应组件死掉后才会变成死代码，删除前确认没有全局 import。")
    print("  * 删完必须跑：npx tsc -p tsconfig.app.json --noEmit && npm run build")

    if args.commands:
        print("\n# ---- PowerShell 删除命令（先 git commit，再执行）----")
        print("cd " + str(ROOT).replace("/", "\\"))
        for p in dead:
            print(f"Remove-Item -LiteralPath '{p.relative_to(ROOT).as_posix()}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
