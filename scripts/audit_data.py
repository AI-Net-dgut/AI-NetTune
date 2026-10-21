#!/usr/bin/env python
"""数据对账：把交接文档里"声称"的结论与 CSV 实际情况逐条核对。

背景：`AI-NetTune预测预警数据交接说明-v2.7` 里的验证表有几条与仓库现状不符
（两表异常组差异、渐变征兆、背景异常）。重新生成数据前后都应该跑一遍这个脚本，
用同一把尺子看结论有没有变化。

用法：
    python scripts/audit_data.py            # 默认只核对 7 天网络表 + 频谱表
    python scripts/audit_data.py --full     # 额外核对 30 天表前 7 天
    python scripts/audit_data.py --no-spectrum   # 跳过 108MB 频谱表（更快）

退出码：0 = 与文档一致；1 = 存在不一致（明细会打印）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_layout import DATA_DIR  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ISSUES: list[str] = []
OKS: list[str] = []


def ok(msg):
    OKS.append(msg)
    print(f"  [一致] {msg}")


def bad(msg):
    ISSUES.append(msg)
    print(f"  [不符] {msg}")


def note(msg):
    print(f"  [信息] {msg}")


def load_network(name):
    import pandas as pd
    p = DATA_DIR / name
    if not p.is_file():
        return None
    df = pd.read_csv(p, encoding="utf-8-sig")
    df["ts"] = pd.to_datetime(df["timestamp"], format="%Y-%m-%d %H:%M:%S")
    return df


def groups(df, ts_col="ts", ap_col="ap_id"):
    """按 (分钟, AP, 异常类型) 去重计数。"""
    sub = df[df["network_status"] != "normal"]
    if sub.empty:
        return set()
    keys = sub[ts_col].dt.floor("min").astype(str) + "|" + sub[ap_col].astype(str) + "|" + sub["network_status"]
    return set(keys)


def main() -> int:
    ap = argparse.ArgumentParser(description="AI-NetTune 数据对账")
    ap.add_argument("--full", action="store_true", help="同时核对 30 天表前 7 天")
    ap.add_argument("--no-spectrum", action="store_true", help="跳过频谱表")
    args = ap.parse_args()

    print("=" * 78)
    print("AI-NetTune 数据对账")
    print(f"数据目录：{DATA_DIR}")
    print("=" * 78)

    # ---------- 1. 网络表 ----------
    print("\n[1] 网络表")
    net = load_network("network_data_7d.csv")
    if net is None:
        net = load_network("network_data_7d(1).csv")
    if net is None:
        bad("未找到 7 天网络表（可用 scripts/prepare_data.py 规范化文件名）")
    note(f"行数 {len(net):,}，时间 {net['ts'].min()} ~ {net['ts'].max()}")
    counts = net["network_status"].value_counts().to_dict()
    note(f"状态分布 {counts}")
    weak = net[net["network_status"] == "weak_coverage"]["rsrp"]
    normal = net[net["network_status"] == "normal"]["rsrp"]
    if len(weak) and weak.max() <= -110:
        ok(f"weak_coverage 行 RSRP 最大 {weak.max():.2f} ≤ −110")
    elif len(weak):
        bad(f"weak_coverage 行存在 RSRP > −110（最大 {weak.max():.2f}）")
    if len(normal) and normal.min() > -110:
        ok(f"normal 行 RSRP 最小 {normal.min():.2f} > −110")
    elif len(normal):
        bad(f"normal 行存在 RSRP ≤ −110（最小 {normal.min():.2f}）")

    # ---------- 2. 渐变征兆 ----------
    print("\n[2] 渐变征兆（提前预警的前提）")
    if net is None:
        print("  跳过")
    else:
        # 分类：normal→weak 的切点，看它**紧邻的连续 normal 段**里 RSRP 是否已经在下滑。
        # 关键：只看紧邻的 normal 段。若之前刚出现过弱覆盖（闪烁型），段会很短，直接排除，
        # 否则会把"反复出入弱覆盖"误判成"渐变"。
        ramps, steps = [], []
        for apid, g in net.groupby("ap_id"):
            g = g.sort_values("ts")
            status = g["network_status"].to_numpy()
            rsrp = g["rsrp"].to_numpy()
            ts = g["ts"].to_numpy()
            for i in range(1, len(status)):
                if not (status[i - 1] == "normal" and status[i] == "weak_coverage"):
                    continue
                # 往前找连续 normal 段的起点
                j = i
                while j > 0 and status[j - 1] == "normal" and i - j < 25:
                    j -= 1
                seg_len = i - j
                if seg_len < 8:          # 段太短：多半是闪烁，不是渐变
                    continue
                pre = rsrp[j:i]
                below = int((pre < -105).sum())
                drop = float(pre.mean() - rsrp[i])
                item = (apid, str(ts[i])[:19], seg_len, float(pre.mean()), float(rsrp[i]), drop, below)
                (ramps if below >= 3 else steps).append(item)

        def show(item):
            apid, when, seg, pre_mean, now, drop, below = item
            return (f"{apid} {when}：切入前连续 {seg} 分钟 normal，均值 {pre_mean:.1f} → {now:.1f} dBm"
                    f"（落差 {drop:.1f}），其中 < −105 的样本 {below}/{seg}")

        note(f"normal→weak 切点分类：渐变型 {len(ramps)} 个，阶跃型 {len(steps)} 个"
             f"（已排除段长 < 8 分钟的闪烁型）")
        for item in ramps[:3]:
            ok(f"渐变型 —— {show(item)}")
        for item in steps[:5]:
            bad(f"阶跃型 —— {show(item)}")
        if steps and not ramps:
            bad("全部切点都是阶跃：事件前没有「逐渐变差」，因此提前 N 分钟预警无法触发")
        elif steps and ramps:
            note("既存在渐变型也存在阶跃型：需确认演示所用的固定事件属于哪一类"
                 "（FAULT_SCHEDULE 注入的 10:00 / 14:00 / 20:00 等整点为阶跃型）")

    # ---------- 3. 两表对账 ----------
    if args.no_spectrum:
        print("\n[3] 两表对账：已跳过（--no-spectrum）")
    else:
        print("\n[3] 两表异常组对账（按 分钟+AP+异常类型 去重）")
        import pandas as pd
        spec_path = DATA_DIR / "spectrum_7d.csv"
        if not spec_path.is_file():
            bad("未找到 data/spectrum_7d.csv")
        else:
            spec = pd.read_csv(spec_path, encoding="utf-8-sig",
                               usecols=["timestamp", "observation_id", "band_id", "network_status"])
            spec["ts"] = pd.to_datetime(spec["timestamp"], format="%Y-%m-%d %H:%M:%S")
            spec_groups = groups(spec, "ts", "observation_id")
            note(f"频谱 {len(spec):,} 行，异常组 {len(spec_groups):,}")

            if net is not None:
                net_groups = groups(net)
                note(f"7 天网络表异常组 {len(net_groups):,}")
                only_net = net_groups - spec_groups
                only_spec = spec_groups - net_groups
                if not only_net and not only_spec:
                    ok("两表异常组完全一致（差异 0/0）")
                else:
                    bad(f"两表不一致：网络独有 {len(only_net)} 组，频谱独有 {len(only_spec)} 组"
                        f"（交接文档声称 0/0）")
                    for sample in list(only_net)[:3]:
                        note(f"  仅网络表有: {sample}")
                    for sample in list(only_spec)[:3]:
                        note(f"  仅频谱表有: {sample}")

            if args.full:
                big = load_network("network_data_30d.csv") or load_network("network_data_30d(1).csv")
                if big is not None:
                    head = big[big["ts"] <= spec["ts"].max()]
                    head_groups = groups(head)
                    only_net = head_groups - spec_groups
                    only_spec = spec_groups - head_groups
                    if not only_net and not only_spec:
                        ok(f"30 天表前 7 天与频谱一致（{len(head_groups):,} 组）")
                    else:
                        bad(f"30 天表前 7 天与频谱不一致：网络独有 {len(only_net)}，"
                            f"频谱独有 {len(only_spec)}（共 {len(head_groups):,} 组）")

    # ---------- 4. 频谱结构 ----------
    if not args.no_spectrum and (DATA_DIR / "spectrum_7d.csv").is_file():
        print("\n[4] 频谱结构")
        import pandas as pd
        spec = pd.read_csv(DATA_DIR / "spectrum_7d.csv", encoding="utf-8-sig",
                           usecols=["timestamp", "observation_id", "band_id",
                                    "lower_mhz", "upper_mhz", "power_dbm", "occupancy_pct"])
        need9 = {"timestamp", "observation_id", "band_id", "lower_mhz", "upper_mhz",
                 "power_dbm", "occupancy_pct", "network_status", "scenario"}
        head = set(pd.read_csv(DATA_DIR / "spectrum_7d.csv", encoding="utf-8-sig", nrows=0).columns)
        if need9 <= head:
            ok("9 字段齐全")
        else:
            bad(f"缺少字段 {sorted(need9 - head)}")
        note(f"行数 {len(spec):,}，AP {spec['observation_id'].nunique()}，频段 {spec['band_id'].nunique()}")
        lo, hi = spec["power_dbm"].min(), spec["power_dbm"].max()
        note(f"power 范围 {lo:.1f} ~ {hi:.1f} dBm")
        occ_bad = int(((spec["occupancy_pct"] < 0) | (spec["occupancy_pct"] > 100)).sum())
        if occ_bad:
            bad(f"占用率越界 {occ_bad} 行")
        else:
            ok("占用率全部在 0–100%")
        # 占用率与功率的语义一致性（文档定义：占用率 = 帧内 power≥−95 的时间比）
        busy = spec["power_dbm"] >= -95
        note(f"power ≥ −95 的行占比 {busy.mean() * 100:.1f}%，"
             f"其占用率均值 {spec.loc[busy, 'occupancy_pct'].mean():.1f}%"
             if busy.any() else "没有 power ≥ −95 的行")
        if busy.mean() > 0.5 and spec.loc[busy, "occupancy_pct"].mean() < 80:
            bad("占用率与功率语义不一致：大多数帧功率都 ≥ −95 dBm，按文档定义占用率应接近 100%，"
                "实测却只有 20–55%（审查报告 D-5）")

    # ---------- 汇总 ----------
    print("\n" + "=" * 78)
    print(f"结论：一致 {len(OKS)} 项，不符 {len(ISSUES)} 项")
    for item in ISSUES:
        print(f"  - {item}")
    print("=" * 78)
    return 0 if not ISSUES else 1


if __name__ == "__main__":
    raise SystemExit(main())
