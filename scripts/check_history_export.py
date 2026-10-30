"""校验导出的历史记录 JSON（用于核对"升级前落库的旧记录"能不能被新页面正确读取）。

场景：真实用户的记录在浏览器 IndexedDB 里，自动化测试碰不到；可行的验证路径是
「页面导出 JSON → 本脚本离线校验」——它按当前 schema 检查字段完整性，列出会被
新三段式（建议方案 / 本次评估参数 / 效果）降级显示的旧记录，并给出归一化建议。

用法：
    # 1. 页面上「批量管理 → 导出」拿到 aintune-history-*.json
    python scripts/check_history_export.py path\\to\\export.json
    python scripts/check_history_export.py export.json --json

退出码：0 = 无阻塞问题；1 = 存在会导致显示异常/不可见的记录（附明细）
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

QUESTION_KINDS = {"analysis", "forecast", "forecast-analysis", "spatial", "backtest"}
MEMBER_KINDS = {"parameters", "simulation"}


def _plans(record: dict) -> list[dict]:
    payload = record.get("payload") or {}
    response = payload.get("response") or {}
    plans = response.get("plans")
    return [p for p in plans if isinstance(p, dict)] if isinstance(plans, list) else []


def main() -> int:
    ap = argparse.ArgumentParser(description="校验历史记录导出文件")
    ap.add_argument("path", help="导出 JSON 的路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"✗ 文件不存在：{path}")
        return 1
    try:
        # 页面导出的 JSON 可能带 UTF-8 BOM（Windows 记事本另存也会），用 utf-8-sig 兼容
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 不是合法 JSON：{exc}")
        return 1

    records = data.get("records") if isinstance(data, dict) else data
    if not isinstance(records, list):
        print("✗ 结构不符：期望 {records: [...]} 或直接是数组")
        return 1

    kinds = Counter(str(r.get("kind")) for r in records if isinstance(r, dict))
    by_id = {r["id"]: r for r in records if isinstance(r, dict) and isinstance(r.get("id"), str)}

    issues: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            issues.append({"id": "?", "level": "error", "what": "记录不是对象"})
            continue
        rid, kind = rec.get("id"), str(rec.get("kind"))
        if not isinstance(rid, str) or not rid:
            issues.append({"id": "?", "level": "error", "what": "缺少 id"})
            continue
        if not isinstance(rec.get("createdAt"), str):
            issues.append({"id": rid, "level": "warn", "what": "缺 createdAt（列表排序会退化）"})
        parent = rec.get("parentId")
        if kind in MEMBER_KINDS and isinstance(parent, str) and parent not in by_id:
            issues.append({"id": rid, "level": "error", "what": f"父记录 {parent} 不在导出里（成员会成为孤儿）"})
        if kind == "analysis" and rec.get("role") == "evidence" and isinstance(parent, str) and parent not in by_id:
            issues.append({"id": rid, "level": "warn",
                           "what": f"孤儿 evidence 记录（父 {parent} 缺失）：修复前在列表/导出里不可见，现在会被列为独立条目"})
        for plan in _plans(rec):
            if not plan.get("simulation"):
                issues.append({"id": rid, "level": "info",
                               "what": f"方案「{plan.get('name')}」没有 simulation：会显示“尚未保存仿真效果”，属旧格式常态"})
            for param in (plan.get("parameters") or []):
                if isinstance(param, dict) and "suggestedValue" not in param:
                    issues.append({"id": rid, "level": "warn",
                                   "what": f"参数 {param.get('label') or param.get('id')} 缺 suggestedValue（“建议值”会退化为空）"})
        payload = rec.get("payload") or {}
        if kind in MEMBER_KINDS and "inputAt" not in payload:
            issues.append({"id": rid, "level": "info",
                           "what": "缺 payload.inputAt（旧格式）：效果与参数的先后关系只能按 createdAt 推断"})

    level_count = Counter(i["level"] for i in issues)
    report = {
        "file": str(path), "records": len(records), "kinds": dict(kinds),
        "issues": {"error": level_count.get("error", 0), "warn": level_count.get("warn", 0),
                   "info": level_count.get("info", 0)},
        "details": issues,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"文件：{path}")
        print(f"记录数：{len(records)}（{', '.join(f'{k}:{v}' for k, v in kinds.items())}）")
        print(f"问题：error {report['issues']['error']} / warn {report['issues']['warn']} / info {report['issues']['info']}")
        for level in ("error", "warn", "info"):
            rows = [i for i in issues if i["level"] == level]
            if not rows:
                continue
            print(f"\n[{level}] {len(rows)} 条")
            for row in rows[:20]:
                print(f"  - {row['id']}: {row['what']}")
            if len(rows) > 20:
                print(f"  … 其余 {len(rows) - 20} 条省略（用 --json 看全部）")

    if report["issues"]["error"]:
        print("\n✗ 存在 error 级问题：这些记录在新页面里可能显示不全或成为孤儿，建议先备份再迁移。")
        return 1
    print("\n✓ 没有 error 级问题；info/warn 项属旧格式常态，页面已做兼容。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
