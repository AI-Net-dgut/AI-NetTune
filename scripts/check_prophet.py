"""Prophet 运行环境自检（部署/演示前一键确认接口 10 是否真能走 Prophet）。

背景：接口 10 依赖 cmdstanpy，每次拟合约在临时目录写编译产物；临时目录不可写或受限时
预热必然超时，随后**进程级永久降级**为移动平均，只能重启恢复。文档原文只写
"装了才用，否则移动平均 baseline"，看不出原因 —— 本脚本把它变成一行命令的判断。

用法：
    python scripts/check_prophet.py                 # 用默认超时（NETTUNE_PROPHET_TIMEOUT 或 20s）
    python scripts/check_prophet.py --timeout 180   # 慢机器放宽
    python scripts/check_prophet.py --json          # 机器可读输出

退出码：
    0  Prophet 可用（method=prophet）
    1  已安装但本次降级（附原因；请检查临时目录可写性后重启服务）
    2  未安装 forecast 依赖（pip install prophet）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _temp_writable() -> tuple[bool, str]:
    try:
        with tempfile.NamedTemporaryFile(prefix="nettune-probe-", suffix=".tmp", delete=True) as fh:
            fh.write(b"ok")
            path = fh.name
        return True, path
    except Exception as exc:  # noqa: BLE001 - 只用于自检输出
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Prophet 环境自检")
    ap.add_argument("--timeout", type=float, default=None, help="预热超时（秒），默认读 NETTUNE_PROPHET_TIMEOUT")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    timeout = args.timeout if args.timeout is not None else float(os.getenv("NETTUNE_PROPHET_TIMEOUT", "20"))
    # --timeout 必须同时生效到**拟合超时**，否则只是"多等一会儿"，
    # 拟合仍按 20 秒放弃（独立审计 P3：--timeout 120 仍报"超过 20 秒"）。
    try:
        import forecast_model as _fm_timeout  # noqa: PLC0415
        _fm_timeout.PROPHET_TIMEOUT_SEC = float(timeout)
    except Exception:  # noqa: BLE001
        pass
    report: dict = {"python": sys.version.split()[0], "timeout_sec": timeout}

    try:
        import forecast_model as fm  # noqa: PLC0415 - 延迟导入，便于先报依赖问题
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 无法导入 forecast_model：{exc}")
        return 2

    report["has_prophet"] = bool(getattr(fm, "_HAS_PROPHET", False))
    try:
        import cmdstanpy  # noqa: PLC0415
        report["cmdstanpy"] = getattr(cmdstanpy, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        report["cmdstanpy"] = None

    ok_temp, temp_detail = _temp_writable()
    report["temp_dir_writable"] = ok_temp
    report["temp_dir"] = temp_detail

    if not report["has_prophet"]:
        print("✗ 未安装 prophet（pip install prophet）")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    started = time.time()
    try:
        fm.warm_up()   # 后台线程启动预热，立即返回
        # 必须等预热**真的结束**（_warm_state.done）再判，否则只会看到"预热中"，
        # 把"还在跑"误报成"已降级"（本脚本第一版就踩了这个坑）。
        deadline = started + timeout + 5
        while time.time() < deadline:
            state = dict(getattr(fm, "_warm_state", {}) or {})
            if state.get("done") or state.get("ok") is not None:
                break
            time.sleep(0.2)
    except Exception as exc:  # noqa: BLE001
        report["warm_up_error"] = f"{type(exc).__name__}: {exc}"
    report["warm_up_sec"] = round(time.time() - started, 2)
    report["warm_state"] = dict(getattr(fm, "_warm_state", {}) or {})

    # 用一条合成序列做真实拟合，确认 method 到底是 prophet 还是 moving_average
    # （prophet_series 的签名是 (bins, vals, horizon)，bins 必须是时间戳字符串）
    from datetime import datetime, timedelta  # noqa: PLC0415

    base = datetime(2026, 1, 1)
    bins = [(base + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S") for i in range(720)]
    vals = [(-90.0 - 0.02 * i) for i in range(720)]
    t1 = time.time()
    try:
        result = fm.prophet_series(bins, vals, 24) or {}
    except Exception as exc:  # noqa: BLE001
        result = {"method": "error", "degrade_reason": f"{type(exc).__name__}: {exc}"}
    report["sample_method"] = result.get("method")
    report["sample_sec"] = round(time.time() - t1, 2)
    report["degrade_reason"] = result.get("degrade_reason") or ""
    report["sample_mae"] = result.get("mae")
    report["sample_predicted_len"] = len(result.get("predicted") or [])

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Python              : {report['python']}")
        print(f"prophet 已安装      : {report['has_prophet']}（cmdstanpy {report['cmdstanpy']}）")
        print(f"临时目录可写        : {ok_temp}（{temp_detail}）")
        print(f"预热耗时            : {report['warm_up_sec']}s（上限 {timeout}s，状态 {report['warm_state']}）")
        print(f"单序列实测 method   : {report['sample_method']}（{report['sample_sec']}s，"
              f"predicted={report['sample_predicted_len']} 点，MAE={report['sample_mae']}）")
        if report["degrade_reason"]:
            print(f"降级原因            : {report['degrade_reason']}")

    if report["sample_method"] == "prophet":
        print("\n✓ 接口 10 可以走 Prophet。")
        return 0
    print("\n✗ 接口 10 当前会降级为移动平均。")
    if not ok_temp:
        print("  临时目录不可写 —— 这是 cmdstanpy 的硬前提，请给运行用户可写的 TMP/TEMP。")
    print("  预热失败是进程级永久标记：修好环境后**重启服务**再跑本脚本确认。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
