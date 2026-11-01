"""仿真内"配置生效"闭环：把方案参数**应用到仿真网络**，让之后的数据/图表/告警跟着变。

为什么需要（申报书创新点④"闭环优化"）：
    现在的方案只能"在同一段历史数据上估效果"，配置本身从未生效 —— 用户改完参数，
    再看图表/告警仍是原始数据，闭环是断的。本模块补上这一环：
    保存配置版本 → 记录生效时刻与对象 → 之后读到的数据按配置偏移 → 可回滚。

设计约束（与清单一致）：
- **绝不篡改原始历史**：原始行保持不可变，偏移只在"生效时刻之后"按需叠加，
  因此"回滚"只是停用配置，历史自然回到原样。
- 与网络接入无关：这里是仿真分支，不涉及真实下发（首页也已声明不下发真实配置）。
- 零开销：没有生效配置时 adjust_rows 直接返回原对象（热路径 filter_rows 不受影响）。
- 经验系数：与 plan_model.simulate 同源的线性经验系数，不是物理模型；
  文档口径见 docs/fault_definition.md 的"能力边界"。

存储：data/sim_config.json（data/ 已在 .gitignore 内，不入库）。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.getenv("NETTUNE_SIM_CONFIG") or (ROOT / "data" / "sim_config.json"))
_LOCK = threading.Lock()
_ACTIVE: dict | None = None          # 当前生效的配置版本（None = 无）
_VERSIONS: list[dict] = []           # 历史版本（最近 50 条）

# 经验系数（每单位参数的指标变化；与 plan_model 的估算同源，非物理模型）
# power  +1 dBm → RSRP +0.9 dB，PRB -1.5%
# tilt   -1°    → RSRP +0.5 dB（下倾角减小，边缘覆盖改善）
COEF = {
    "power": {"rsrp": 0.9, "prb_usage": -1.5, "throughput": 1.8},
    "tilt": {"rsrp": 0.5, "prb_usage": -0.8, "throughput": 1.1},
}
NUMERIC_KEYS = ("rsrp", "sinr", "throughput", "prb_usage", "packet_loss", "latency")


def _load() -> None:
    global _ACTIVE, _VERSIONS
    try:
        if not CONFIG_PATH.exists():
            return
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        _VERSIONS = [v for v in (data.get("versions") or []) if isinstance(v, dict)][-50:]
        active_id = data.get("active")
        _ACTIVE = next((v for v in _VERSIONS if v.get("id") == active_id and v.get("status") == "active"), None)
    except Exception:  # noqa: BLE001 - 配置损坏时应回落到"未生效"，不能拖垮服务
        _ACTIVE, _VERSIONS = None, []


def _save() -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"active": (_ACTIVE or {}).get("id"), "versions": _VERSIONS[-50:]}
        CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass    # 只读环境：内存里仍然生效，只是重启后丢失


_load()


def current() -> dict | None:
    """当前生效的配置版本（没有则 None）。"""
    return _ACTIVE


def versions() -> list[dict]:
    return list(reversed(_VERSIONS))


def apply(scenario: str, ap_ids: list[str], params: dict, effective_from: str,
          baseline: dict | None = None, note: str = "", plan_id: str | None = None) -> dict:
    """把一组参数应用为**仿真生效配置**。

    params 形如 {"tilt": 5, "power": 33}；baseline 是**当前工参**（{"tilt": 8, "power": 33}），
    偏移按 (params - baseline) 计算 —— 少了 baseline 就无法知道"改了多少"，会退化为零偏移。
    effective_from 之后的采样按经验系数叠加偏移；之前的行保持原样（历史不可改）。
    """
    global _ACTIVE
    if not ap_ids:
        raise ValueError("至少指定一个 AP")
    if not params:
        raise ValueError("至少指定一个参数")
    datetime.strptime(effective_from, "%Y-%m-%d %H:%M:%S")   # 格式校验，错了直接抛
    with _LOCK:
        version = {
            "id": f"SIMCFG-{uuid.uuid4().hex[:8]}",
            "scenario": scenario,
            "apIds": sorted({str(a).upper() for a in ap_ids}),
            "params": {k: float(v) for k, v in params.items() if k in COEF},
            "baseline": {k: float(v) for k, v in (baseline or {}).items() if k in COEF},
            "effectiveFrom": effective_from,
            "appliedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": note,
            "planId": plan_id,
            "status": "active",
        }
        if not version["params"]:
            raise ValueError("params 里没有可生效的参数（支持：tilt / power）")
        if not version["baseline"]:
            raise ValueError("缺少 baseline（当前工参），无法计算偏移量")
        if _ACTIVE:
            _ACTIVE["status"] = "superseded"
        _VERSIONS.append(version)
        _ACTIVE = version
        _save()
        return dict(version)


def rollback(version_id: str | None = None) -> dict:
    """停用配置（默认停用当前生效的），回到原始历史。"""
    global _ACTIVE
    with _LOCK:
        target = None
        if version_id:
            target = next((v for v in _VERSIONS if v.get("id") == version_id), None)
        else:
            target = _ACTIVE
        if target is None:
            raise ValueError("没有可回滚的配置版本")
        if target.get("status") == "active":
            target["status"] = "rolled_back"
            target["rolledBackAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if _ACTIVE and _ACTIVE.get("id") == target.get("id"):
            _ACTIVE = None
        _save()
        return dict(target)


def _offsets(active: dict, ap_id: str) -> dict[str, float]:
    """按参数相对基准的差值算出各指标的偏移量。"""
    base = active.get("baseline") or {}
    out: dict[str, float] = {}
    for key, target in (active.get("params") or {}).items():
        delta = float(target) - float(base.get(key, target))
        if not delta:
            continue
        for metric, coef in COEF.get(key, {}).items():
            out[metric] = out.get(metric, 0.0) + coef * delta
    return out


def adjust_rows(rows: list[dict]) -> list[dict]:
    """生效后的行叠加偏移。无生效配置时**原样返回同一个对象**（零拷贝、零开销）。"""
    active = _ACTIVE
    if not active or not rows:
        return rows
    ap_ids = set(active.get("apIds") or [])
    effective = active.get("effectiveFrom") or ""
    touched = False
    out: list[dict] = []
    for row in rows:
        if row.get("ap_id") in ap_ids and (row.get("timestamp") or "") >= effective:
            offs = _offsets(active, row.get("ap_id") or "")
            if offs:
                new_row = dict(row)
                changed = False
                for metric, delta in offs.items():
                    # 注意：filter_rows 出来的指标是**字符串**（CSV 解析结果），
                    # 直接 isinstance(float) 判不过会导致"配置生效了但数据没变"（实测踩到）。
                    if metric not in new_row:
                        continue
                    try:
                        base_value = float(new_row[metric])
                    except (TypeError, ValueError):
                        continue
                    new_row[metric] = round(base_value + delta, 2)
                    changed = True
                if changed:
                    out.append(new_row)
                    touched = True
                    continue
        out.append(row)
    return out if touched else rows


def describe() -> dict:
    """给接口用的摘要（不暴露内部结构）。"""
    active = _ACTIVE
    if not active:
        return {"active": None, "versions": len(_VERSIONS)}
    return {
        "active": {
            "id": active["id"], "scenario": active["scenario"], "apIds": active["apIds"],
            "params": active["params"], "effectiveFrom": active["effectiveFrom"],
            "appliedAt": active["appliedAt"], "note": active.get("note", ""),
            "planId": active.get("planId"),
        },
        "versions": len(_VERSIONS),
    }
