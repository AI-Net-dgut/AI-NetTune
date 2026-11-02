"""仿真内"配置生效"闭环：把方案参数**应用到仿真网络**，让之后的数据/图表/告警跟着变。

为什么需要（申报书创新点④"闭环优化"）：
    方案原本只能"在同一段历史数据上估效果"，配置本身从未生效 —— 用户改完参数，
    再看图表/告警仍是原始数据，闭环是断的。本模块补上这一环。

与 simulation_engine 的分工（第二轮复查 P0 的修复）：
    **本模块不再维护任何系数**，全部调用 simulation_engine.calculate_effects /
    transform_network_row。此前两套系数（评估 +1.2/°、生效 −0.5/°）导致
    "评估改善、应用恶化"，已彻底移除。

设计约束：
- **绝不篡改原始历史**：原始行不可变，偏移只在读取时按需叠加；回滚即恢复。
- 每次 apply / rollback 递增 `revision`，供缓存键、预测 ID、结果留痕使用。
- 零开销：无生效配置时 adjust_rows 直接返回原对象。

存储：data/sim_config.json（data/ 已在 .gitignore 内，不入库）。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

import simulation_engine

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.getenv("NETTUNE_SIM_CONFIG") or (ROOT / "data" / "sim_config.json"))
_LOCK = threading.Lock()
_ACTIVE: dict | None = None          # 当前生效的配置版本（None = 无）
_VERSIONS: list[dict] = []           # 历史版本（最近 50 条）
_REVISION = 0                        # 配置修订号：每次应用/回滚 +1（缓存与留痕用）


def _load() -> None:
    global _ACTIVE, _VERSIONS, _REVISION
    try:
        if not CONFIG_PATH.exists():
            return
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        _VERSIONS = [v for v in (data.get("versions") or []) if isinstance(v, dict)][-50:]
        _REVISION = int(data.get("revision") or 0)
        active_id = data.get("active")
        _ACTIVE = next((v for v in _VERSIONS if v.get("id") == active_id and v.get("status") == "active"), None)
    except Exception:  # noqa: BLE001 - 配置损坏时回落到"未生效"，不能拖垮服务
        _ACTIVE, _VERSIONS, _REVISION = None, [], 0


def _save() -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"active": (_ACTIVE or {}).get("id"), "revision": _REVISION,
                   "versions": _VERSIONS[-50:]}
        CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass    # 只读环境：内存里仍然生效，只是重启后丢失


_load()


def revision() -> int:
    """当前配置修订号。所有依赖配置的缓存键、预测 ID 都必须包含它。"""
    return _REVISION


def current() -> dict | None:
    return _ACTIVE


def versions() -> list[dict]:
    return list(reversed(_VERSIONS))


def apply(scenario: str, ap_ids: list[str], params: dict, effective_from: str,
          baseline: dict | None = None, note: str = "", plan_id: str | None = None) -> dict:
    """把一组参数应用为**仿真生效配置**。

    params 是本次改动；target 由 `normalize_target(params, 当前目标)` 补成完整状态，
    因此"只改倾角"不会把功率意外重置。baseline 是原始工参（缺省用引擎默认值）。
    """
    global _ACTIVE, _REVISION
    if not ap_ids:
        raise ValueError("至少指定一个 AP")
    if not params:
        raise ValueError("至少指定一个参数")
    datetime.strptime(effective_from, "%Y-%m-%d %H:%M:%S")   # 格式校验
    try:
        checked = simulation_engine.validate_parameters(params)
    except simulation_engine.ParamError as exc:
        raise ValueError(str(exc)) from None
    with _LOCK:
        target = simulation_engine.normalize_target(checked, (_ACTIVE or {}).get("target"))
        version = {
            "id": f"SIMCFG-{uuid.uuid4().hex[:8]}",
            "scenario": scenario,
            "apIds": sorted({str(a).upper() for a in ap_ids}),
            "params": checked,                       # 本次改动（展示用）
            "target": target,                        # 完整目标状态（计算用）
            "baseline": simulation_engine.normalize_target(None, baseline or {}),
            "effectiveFrom": effective_from,
            "appliedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": note,
            "planId": plan_id,
            "algoVersion": simulation_engine.ALGO_VERSION,
            "status": "active",
        }
        if _ACTIVE:
            _ACTIVE["status"] = "superseded"
        _REVISION += 1
        version["revision"] = _REVISION
        _VERSIONS.append(version)
        _ACTIVE = version
        _save()
        return dict(version)


def rollback(version_id: str | None = None) -> dict:
    """停用配置（默认停用当前生效的），回到原始历史。"""
    global _ACTIVE, _REVISION
    with _LOCK:
        target = next((v for v in _VERSIONS if v.get("id") == version_id), None) if version_id else _ACTIVE
        if target is None:
            raise ValueError("没有可回滚的配置版本")
        if target.get("status") == "active":
            target["status"] = "rolled_back"
            target["rolledBackAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if _ACTIVE and _ACTIVE.get("id") == target.get("id"):
            _ACTIVE = None
        _REVISION += 1          # 回滚同样产生新修订：缓存必须失效
        _save()
        return dict(target)


def adjust_rows(rows: list[dict]) -> list[dict]:
    """生效后的行按**引擎转换**得到新行。无生效配置时原样返回同一对象（零拷贝）。"""
    active = _ACTIVE
    if not active or not rows:
        return rows
    ap_ids = {str(a).upper() for a in (active.get("apIds") or [])}
    effective = active.get("effectiveFrom") or ""
    target = active.get("target") or {}
    baseline = active.get("baseline") or {}
    if not simulation_engine.calculate_effects(baseline, target):
        return rows                       # 目标与基线一致：无需改动
    out: list[dict] = []
    touched = False
    for row in rows:
        if str(row.get("ap_id") or "").upper() in ap_ids and (row.get("timestamp") or "") >= effective:
            out.append(simulation_engine.transform_network_row(row, target, baseline))
            touched = True
            continue
        out.append(row)
    return out if touched else rows


def describe() -> dict:
    """给接口用的摘要（不暴露内部结构）。"""
    active = _ACTIVE
    base = {
        "revision": _REVISION,
        "algoVersion": simulation_engine.ALGO_VERSION,
        "versions": len(_VERSIONS),
        "supportedParams": sorted(simulation_engine.DEFAULT_PARAMS),
    }
    if not active:
        return {"active": None, **base}
    return {
        "active": {
            "id": active["id"], "scenario": active["scenario"], "apIds": active["apIds"],
            "params": active.get("target") or active.get("params"),
            "baseline": active.get("baseline"),
            "effectiveFrom": active["effectiveFrom"], "appliedAt": active["appliedAt"],
            "note": active.get("note", ""), "planId": active.get("planId"),
            "revision": active.get("revision"),
        },
        **base,
    }
