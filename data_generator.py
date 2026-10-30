"""
仿真数据生成器 v2.6（唐苗数据扩展版，基于程思源 v2.3）
项目：AI-NetTune 多模态无线网络智能监测平台
设计思路：
- 固定AP点位 + UE随机位置，模拟用户移动
- 对数距离路径损耗模型 + 阴影衰落，生成RSRP
- 多AP同频干扰叠加底噪，计算SINR
- 香农公式简化计算吞吐量，映射时延、丢包、资源占用
- UE自动选择信号最优AP接入，符合真实通信机制
- 输出完整时序CSV，支持拓扑、热力图、故障研判
v2.3 修复（相对 v2.2）：
1. SCENE_NAME 由英文 campus 改为中文 校园 —— 与 main.py VALID_SCENARIOS 一致
2. START_TIME 改回 2026-01-01 —— 与 main.py 默认窗口一致
3. 新增 FAULT_SCHEDULE 故障注入 —— 保证演示时有弱覆盖/拥塞异常可报
4. SINR 限幅至 [-10, 30] —— 修复 v2.2 SINR 峰值 67.84 的异常
v2.4 扩展（唐苗，支持 7/30 天长周期数据集）：
1. TOTAL_DAYS 命令行参数化：--days 3/7/30，输出文件自动命名 network_data[_7d/_30d].csv
2. 故障注入按周规律延伸：第4天起每2天一组故障（弱覆盖/拥塞交替、AP轮换），
   保证全周期每天均有异常样本，异常占比与3天版（约8%）持平，供 Prophet/LSTM 训练
3. 校验逻辑增强：新增按天故障分布统计，确认每天均有异常样本
v2.5 扩展（唐苗，频谱维度，配合前端预测预警 v2 契约）：
1. 新增频谱数据生成：输出 data/spectrum[_7d/_30d].csv，
   字段 timestamp/observation_id/band_id/lower_mhz/upper_mhz/power_dbm/occupancy_pct/network_status/scenario
2. 频段网格固定为 5G n78（3300-3800 MHz），10 MHz 一段共 50 段，升序不重叠（≤128 上限）
3. 口径（已与组长确认，需同步 docs/api.md）：
   - power_dbm：观测点（AP）在该频段的接收功率 dBm，由中心 3550 MHz 高斯包络 + 帧内随机波动生成，
     非功率谱密度；
   - occupancy_pct：该频段在 1 分钟统计帧内被占用的时间比例（%），占用判定阈值为接收功率
     ≥ -95 dBm 视为忙；分母为帧内采样次数（1 分钟 = 60 次 1s 采样，生成时以概率直接抽样）；
   - 正常帧：占用 20-55%（日内波动）；拥塞故障帧：85-95%；弱覆盖故障帧：10-30%；
   - 背景干扰：约 2% 帧随机频段功率突降/占用突升，network_status 仍为 normal（模拟未标注干扰）；
   - 频谱帧同样遵守防未来口径：数据文件只含真实生成的历史序列，接口侧预测只用 baseTime 以前。
4. 与 network_data 同时间轴（1 分钟一帧 × 3 观测点 × 50 频段 = 150 行/帧）
v2.7 扩展（唐苗，全量事件对齐 + 功率阈值对齐，修复前端《预测预警问题状态与责任分工》数据口径）：
1. --events 模式下，频谱 network_status 逐分钟逐 AP 全量取自网络 events 表：
   - 聚合规则：同一 (时间, AP) 下任一 UE 标注异常即 AP 异常；多 UE 多类型全部保留
     （含 EV 事件与网络表背景异常标签），消除 v2.6 只取"首个非 normal"导致的类型缺失
     （前端复算 86 组"网络表有、频谱表无"差异）。
   - 同一 (时间, AP) 同时存在 weak_coverage + congestion 时，频段级混合标注：
     前 25 段 congestion、后 25 段 weak_coverage（50 段按序切分），保证
     (分钟+AP+异常类型) 去重口径下两表完全一致（差异=0）。
2. 弱覆盖功率对齐后端高亮阈值：weak 帧功率在正常包络基础上再降
   SPECTRUM_DROP_K+33 dB（中心段约 -101 dBm、边缘段约 -113 dBm，全段 < -95 dBm），
   修复"功率最低 -92.05、选不到弱覆盖高亮频段"问题。
3. events 模式下不再独立注入背景干扰（网络表背景异常已通过对齐表达），
   避免与网络表随机标签错位；v2.5 非 events 模式（3/30 天版）行为保持不变。
未传 --events 时保持 v2.5 的 FAULT_SCHEDULE 注入（3/30 天版）。
扩展说明：
- 核心字段15个，保持接口契约不变
- 末尾追加3个辅助字段，用于故障原因深度分析
- DeepMIMO仅用于离线频谱预测验证，不接入主链路
"""

import argparse
from pathlib import Path
import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta

# ==================================================
# 1. 全局配置参数（所有可调参数集中在此）
# ==================================================
# ---------- 时间配置 ----------
START_TIME = "2026-01-01 00:00:00"
TOTAL_DAYS = 3                  # 默认生成总天数（命令行 --days 可覆盖：3/7/30）
INTERVAL_MINUTES = 1            # 采样间隔（分钟）

# ---------- 基站AP配置 ----------
AP_INFO = {
    "AP-01": {"ap_x": 120.0, "ap_y": 220.0},
    "AP-02": {"ap_x": 380.0, "ap_y": 160.0},
    "AP-03": {"ap_x": 260.0, "ap_y": 350.0}
}
SCENE_NAME = "校园"             # 业务场景标识（与 main.py VALID_SCENARIOS 一致）

# ---------- 信道模型参数 ----------
PATH_LOSS_EXP = 3.2            # 路径损耗指数（城区校园典型值3.0~3.5）
PR0_DBM = -28.0                # 1米处参考接收功率 dBm
SHADOW_STD = 3.5               # 阴影衰落标准差 dB
NOISE_FLOOR_DBM = -105.0       # 系统底噪 dBm

# ---------- 业务性能参数 ----------
MAX_USER_THROUGHPUT = 150.0    # 单用户峰值吞吐量 Mbps
BASE_LATENCY_MS = 12.0         # 最小时延 ms
BASE_PACKET_LOSS = 0.5         # 基础丢包率 %

# ---------- 故障判定阈值（与接口契约一致，禁止修改） ----------
RSRP_WEAK_THRESHOLD = -110     # 弱覆盖阈值 dBm
PRB_CONGESTION_THRESHOLD = 85  # 拥塞PRB阈值 %
LOSS_CONGESTION_THRESHOLD = 5  # 拥塞丢包阈值 %

# ---------- 频谱维度配置（v2.5，口径与组长确认：n78 固定网格 / ≤128 段） ----------
SPECTRUM_BAND_START_MHZ = 3300     # n78 起始频率 MHz
SPECTRUM_BAND_END_MHZ = 3800       # n78 结束频率 MHz
SPECTRUM_BAND_WIDTH_MHZ = 10       # 每段带宽 MHz → 50 段（3300-3800），升序不重叠
SPECTRUM_CENTER_MHZ = 3550.0       # 频谱包络中心频率 MHz
SPECTRUM_DROP_K = 8.0              # 包络衰减系数：drop(dB)=((f-center)/200)^2*K（边缘落差约12dB）
SPECTRUM_POWER_BASE_DBM = -60.0    # 包络中心接收功率 dBm（观测点级）
SPECTRUM_POWER_FLUCT_DB = 1.5      # 正常帧功率随机波动 dB
POWER_BUSY_THRESHOLD_DBM = -95.0   # 占用判定阈值：接收功率≥此值视为频段忙
OCC_NORMAL_RANGE = (20.0, 55.0)    # 正常帧占用率区间 %
OCC_CONGEST_RANGE = (85.0, 95.0)   # 拥塞故障帧占用率区间 %
OCC_WEAK_RANGE = (10.0, 30.0)      # 弱覆盖故障帧占用率区间 %
SPECTRUM_NOISE_RATIO = 0.02        # 背景干扰帧比例（模拟未标注干扰，status 保持 normal）
SPECTRUM_NOISE_BANDS = (3, 8)      # 背景干扰波及的连续频段数
SPECTRUM_POWER_DROP_DB = 8.0       # 背景干扰功率突降 dB
SPECTRUM_OCC_RISE = 30.0           # 背景干扰占用率突升百分点（相对正常上限）

# ---------- 故障注入配置（演示用：固定时段制造可复现故障） ----------
# 每条: (起始时间, 结束时间, 目标AP, 故障类型, 强度)
#   weak_coverage: 强度 = 对该AP信号施加的额外衰减 dB（使其低于 -110）
#   congestion:    强度 = 目标PRB利用率 %（强制 85~强度 区间，并抬升丢包）
FAULT_SCHEDULE = [
    # 第1天：AP-02 弱覆盖 2小时（10:00-12:00）→ 与申报书演示口径一致
    ("2026-01-01 10:00:00", "2026-01-01 12:00:00", "AP-02", "weak_coverage", 15.0),
    # 第1天：AP-01 拥塞 2小时（20:00-22:00）
    ("2026-01-01 20:00:00", "2026-01-01 22:00:00", "AP-01", "congestion", 92.0),
    # 第2天：AP-03 弱覆盖 2小时（14:00-16:00）
    ("2026-01-02 14:00:00", "2026-01-02 16:00:00", "AP-03", "weak_coverage", 15.0),
    # 第3天：AP-01 拥塞 2小时（09:00-11:00）
    ("2026-01-03 09:00:00", "2026-01-03 11:00:00", "AP-01", "congestion", 92.0),
]

# ---------- 渐变征兆（v2.8）----------
# 背景：交接文档 v2.7 声称"事件前后存在渐入/渐出期（15–25 分钟）"，
#       但实测全部是单分钟阶跃（事件前 25 分钟 RSRP < −105 的样本数恒为 0），
#       导致"提前 N 分钟预警"在数据上不可能触发。
# 做法：事件强度按时间做线性渐入/渐出，起止时刻不变，只改变事件前后的数值曲线。
RAMP_MINUTES = 20        # 渐入/渐出各持续多少分钟（落在文档声称的 15–25 区间）

# ---------- 故障时间表扩展（v2.4） ----------
def build_fault_schedule(total_days: int) -> list:
    """按总天数构建完整故障注入时间表。
    前3天保留 FAULT_SCHEDULE 原有4条（与申报书演示口径一致）；
    第4天起每2天安排一组故障（弱覆盖/拥塞交替、AP轮换），
    保证7/30天全周期每天均有异常样本，异常占比与3天版（约8%）持平。
    """
    schedule = list(FAULT_SCHEDULE)
    start = datetime.strptime(START_TIME, "%Y-%m-%d %H:%M:%S")
    aps = ["AP-02", "AP-03", "AP-01"]  # 轮换顺序，避开与原有条目完全重复
    idx = 0
    for day in range(4, total_days + 1):
        date_str = (start + timedelta(days=day - 1)).strftime("%Y-%m-%d")
        if day % 2 == 0:
            # 偶数天：弱覆盖 10:00-12:00
            ap = aps[idx % 3]
            schedule.append((f"{date_str} 10:00:00", f"{date_str} 12:00:00", ap, "weak_coverage", 15.0))
        else:
            # 奇数天：拥塞 20:00-22:00
            ap = aps[(idx + 1) % 3]
            schedule.append((f"{date_str} 20:00:00", f"{date_str} 22:00:00", ap, "congestion", 92.0))
        idx += 1
    return schedule

# ---------- UE配置 ----------
UE_COUNT_PER_SLOT = 3          # 每个时刻同时在线用户数
MAP_RANGE_X = (20, 480)        # 用户活动横向范围 米
MAP_RANGE_Y = (20, 430)        # 用户活动纵向范围 米

# ---------- 输出配置 ----------
OUTPUT_PATH = "data/network_data.csv"   # 默认输出；--days 7/30 时自动命名 _7d/_30d
SPECTRUM_OUTPUT_PATH = "data/spectrum.csv"  # v2.5 频谱输出；--days 7/30 时自动命名 _7d/_30d
OUTPUT_ENCODING = "utf-8-sig"
FAULT_EVENTS_OUTPUT = "data/fault_events.csv"   # D-2：故障事件清单（单一事实来源）


def write_fault_events(schedule: list) -> None:
    """把故障注入时间表落成 CSV，供前端案例起点、交接文档、评审复现统一引用。

    背景（审查报告 D-2）：交接文档与《团队分工》都提到"数据生成器同步输出
    fault_events.csv 故障事件清单"，但该文件从未被生成过，EV-001~004 只存在于
    文档正文里；前端 CASE_WINDOWS 的三个案例起点是手写时间戳，改数据就失效。

    列：event_id / scenario / ap_id / fault_type / start_time / end_time /
        ramp_minutes / strength / labeled_from_expect
    最后一个字段是提示：渐变期只有越过判定阈值才标异常，所以"事件开始"与
    "首次出现 weak_coverage 标签"并不在同一分钟。
    """
    import csv
    # 只登记**频谱数据覆盖范围内**的事件（独立审计 P2）：频谱表只有 7 天，
    # 而 30 天网络表有 31 个注入事件；若把 01-08 之后的事件也写进清单，
    # 前端案例按钮会多出 23 个"点开即数据不足"的入口，并与文档的"8 条"冲突。
    # 超过频谱覆盖的事件不再列入清单（网络表里仍然有故障注入，只是没有对照频谱）。
    spectrum_end = None
    try:
        import pandas as _pd  # noqa: PLC0415 - 仅用于读频谱末时刻
        _spec_path = Path(SPECTRUM_OUTPUT_PATH)
        if _spec_path.exists():
            head = _pd.read_csv(_spec_path, encoding=OUTPUT_ENCODING, usecols=["timestamp"])
            spectrum_end = str(head["timestamp"].max())
    except Exception:  # noqa: BLE001 - 读不到就不过滤，保持旧行为
        spectrum_end = None
    if spectrum_end:
        schedule = [row for row in schedule if row[0] <= spectrum_end]
    rows = []
    for idx, (start, end, ap, ftype, strength) in enumerate(schedule, start=1):
        rows.append({
            "event_id": f"EV-{idx:03d}",
            "scenario": SCENE_NAME,
            "ap_id": ap,
            "fault_type": ftype,
            "start_time": start,
            "end_time": end,
            "ramp_minutes": RAMP_MINUTES,
            "strength": strength,
            "labeled_from_expect": ("事件内 RSRP 跌破 -110 dBm 后" if ftype == "weak_coverage"
                                    else "事件内 PRB>85% 且丢包>5% 后"),
        })
    path = Path(FAULT_EVENTS_OUTPUT)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=OUTPUT_ENCODING, newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"[v2.8] 已输出故障事件清单：{FAULT_EVENTS_OUTPUT}（{len(rows)} 条）")

# ==================================================
# 2. 工具函数
# ==================================================
def dbm_to_mw(dbm: float) -> float:
    """dBm 转 毫瓦"""
    return 10 ** (dbm / 10.0)

def mw_to_dbm(mw: float) -> float:
    """毫瓦 转 dBm"""
    return 10.0 * np.log10(mw)

def clamp(value: float, min_val: float, max_val: float) -> float:
    """数值限幅"""
    return max(min_val, min(value, max_val))

def _ramp_factor(start: str, end: str, ts: str) -> float:
    """事件强度系数 ∈ [0,1]：窗口两端线性渐入/渐出，中段为 1。

    v2.8 新增。起止时刻完全不变，只让强度渐变，从而在事件前产生可预测的下降趋势。
    """
    fmt = "%Y-%m-%d %H:%M:%S"
    t0, t1, now = (datetime.strptime(x, fmt) for x in (start, end, ts))
    total = (t1 - t0).total_seconds() / 60.0
    if total <= 0:
        return 1.0
    ramp = min(RAMP_MINUTES, total / 3.0)      # 窗口很短时按 1/3 收缩，保证有平台段
    elapsed = (now - t0).total_seconds() / 60.0
    remaining = (t1 - now).total_seconds() / 60.0
    if elapsed < ramp:
        return max(0.0, elapsed / ramp)
    if remaining < ramp:
        return max(0.0, remaining / ramp)
    return 1.0


def get_active_fault(timestamp_str: str):
    """按时间戳查命中的故障注入条目。

    返回 (ap, type, strength, ramp)；未命中返回 (None, None, None, 0.0)。
    ramp 是渐变系数（v2.8）：调用方用它把强度按比例施加，避免阶跃。
    """
    for start, end, ap, ftype, strength in FAULT_SCHEDULE:
        if start <= timestamp_str <= end:
            return ap, ftype, strength, _ramp_factor(start, end, timestamp_str)
    return None, None, None, 0.0

# ==================================================
# 3. 主生成逻辑
# ==================================================
def generate_dataset() -> pd.DataFrame:
    records = []
    current_time = datetime.strptime(START_TIME, "%Y-%m-%d %H:%M:%S")
    total_slots = TOTAL_DAYS * 24 * 60 // INTERVAL_MINUTES

    for _ in range(total_slots):
        timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
        fault_ap, fault_type, fault_strength, fault_ramp = get_active_fault(timestamp_str)

        # 生成当前时刻所有UE的随机位置
        ue_list = []
        for ue_idx in range(UE_COUNT_PER_SLOT):
            ue_list.append({
                "user_id": f"UE-{ue_idx+1:02d}",
                "ue_x": round(random.uniform(*MAP_RANGE_X), 2),
                "ue_y": round(random.uniform(*MAP_RANGE_Y), 2)
            })

        # 逐个UE计算信道与性能，选择最优AP接入
        for ue in ue_list:
            # 计算所有AP的接收功率
            ap_rsrp_map = {}
            for ap_id, ap_pos in AP_INFO.items():
                dist = np.sqrt(
                    (ue["ue_x"] - ap_pos["ap_x"]) ** 2 +
                    (ue["ue_y"] - ap_pos["ap_y"]) ** 2
                )
                dist = max(dist, 1.0)  # 避免距离为0
                path_loss = PR0_DBM - 10.0 * PATH_LOSS_EXP * np.log10(dist)
                shadow = np.random.normal(0, SHADOW_STD)
                ap_rsrp_map[ap_id] = path_loss + shadow

            # D-7 修正①：RSRP 物理上限。原实现近距离 UE 会算出 -22.77 dBm，
            # 而 5G RSRP 高于 -60 dBm 不合物理（data_dict 声明范围 -140~-60）。
            # 用 -60.5 往下抖动，避免一堆行死死贴在 -60 形成水平带。
            for _aid in ap_rsrp_map:
                if ap_rsrp_map[_aid] > -60.5:
                    ap_rsrp_map[_aid] = -60.5 - random.uniform(0.0, 2.5)

            # 故障注入：弱覆盖时段，对该AP信号额外衰减（制造覆盖空洞）
            # v2.8：衰减量按 ramp 渐入/渐出，事件前 20 分钟会出现"逐渐变差"的可预测征兆
            if fault_type == "weak_coverage":
                ap_rsrp_map[fault_ap] -= fault_strength * fault_ramp

            # 选择信号最优的AP作为服务AP
            serving_ap = max(ap_rsrp_map, key=ap_rsrp_map.get)
            serving_rsrp = ap_rsrp_map[serving_ap]

            # 故障注入：故障时段强制UE接入故障AP（模拟故障区域内用户只能连该AP）
            # —— 否则UE会切换到其他正常AP，故障AP上采样过少，研判占比无法超过阈值
            # v2.8：弱覆盖事件在渐入/渐出期做"位置过渡"，避免窗口第一分钟就因强制切换产生阶跃。
            #       注意必须先把未衰减电平还原再混合，否则 ramp 会被乘两次
            #       （serving = natural + (forced−natural)×ramp 里 forced 已含 −strength×ramp），
            #       实测会导致下降集中在最后几分钟、看起来仍像阶跃。
            if fault_ap is not None:
                forced_rsrp = ap_rsrp_map[fault_ap]
                if fault_type == "weak_coverage" and fault_ramp < 1.0:
                    unattenuated = forced_rsrp + fault_strength * fault_ramp
                    serving_rsrp = (serving_rsrp + (unattenuated - serving_rsrp) * fault_ramp
                                    - fault_strength * fault_ramp)
                elif fault_type == "weak_coverage":
                    serving_rsrp = forced_rsrp
                # D-7 修正②：拥塞是**负载**问题，不是覆盖问题。
                # 样本仍算到故障 AP 头上（保证该 AP 有足够采样），但信号质量保持
                # 用户实际所在位置的水平——否则拥塞行的 SINR 会被压到 -10 dB，
                # 看起来像覆盖故障，judge_ap 先判覆盖就会把拥塞误判成覆盖。
                serving_ap = fault_ap

            # 计算干扰功率（所有非服务AP + 底噪）
            interference_mw = dbm_to_mw(NOISE_FLOOR_DBM)
            for ap_id, rsrp in ap_rsrp_map.items():
                if ap_id != serving_ap:
                    interference_mw += dbm_to_mw(rsrp)
            sinr_dbm = mw_to_dbm(dbm_to_mw(serving_rsrp) / interference_mw)
            # D-7 修正③：限幅从 [-10, 30] 放宽到 [-25, 30]。
            # 原范围导致 7.31% 的行压在边界上（7,164 行恰好 -10.0、2,314 行恰好 30.0），
            # 弱覆盖行 95% 的 SINR 全等于 -10，信息被压平。
            sinr_dbm = clamp(sinr_dbm, -25.0, 30.0)

            # 计算吞吐量（香农公式简化）
            sinr_linear = dbm_to_mw(sinr_dbm)
            # D-7 修正④：封顶不再产生"永远 150"的水平带。
            # 原实现 5.28% 的行恰好等于 150.0；这里对触顶的行按 0.93~1.00 比例抖动，
            # 既保留"上限"语义，又不让图表出现一条死平线。
            throughput = 20.0 * np.log2(1 + sinr_linear)
            if throughput >= MAX_USER_THROUGHPUT:
                throughput = MAX_USER_THROUGHPUT * random.uniform(0.93, 0.995)
            throughput = round(throughput, 2)

            # 计算时延：信号越差、负载越高，时延越大
            load_factor = throughput / MAX_USER_THROUGHPUT
            latency = BASE_LATENCY_MS + max(0, (-serving_rsrp - 80) * 0.25) + load_factor * 8
            latency = round(clamp(latency, 8, 80), 2)

            # 计算丢包率：信号越差、负载越高，丢包越高
            packet_loss = BASE_PACKET_LOSS + max(0, (-serving_rsrp - 90) * 0.15) + load_factor * 2
            packet_loss = round(clamp(packet_loss, 0.1, 22.0), 2)

            # 计算PRB资源占用率
            # D-7 修正⑤：压低正常行的 PRB。原为 40 + load*55 + U(-3,3)，
            # 正常行 p95 达 93.63%，而 main.py 的拥塞阈值是 >90% →
            # 正常窗口里也有大量小时"按规则算拥塞"，造成系统性误报（B-7 的数据根因）。
            # 改为 22 + load*48 ± 3 → 约 19~73%，p95 远低于 85%。
            prb_usage = 22.0 + load_factor * 48.0 + random.uniform(-3, 3)
            prb_usage = round(clamp(prb_usage, 8.0, 78.0), 2)

            # 故障注入：拥塞时段，抬升目标AP的PRB与丢包（制造容量拥塞）
            # v2.8：按 ramp 从自然值过渡到目标值，渐入期 PRB 会逐步越过 85% 阈值
            if fault_type == "congestion" and serving_ap == fault_ap:
                target_prb = clamp(fault_strength + random.uniform(-2, 3), 85.0, 98.0)
                # 下限用 6.0 而不是 5.0：拥塞判定是 packet_loss > 5（严格大于），
                # 取 5.0 会让大量行恰好等于 5 而丢掉 congestion 标签（实测掉到原来的 1/3）
                target_loss = clamp(packet_loss + 3.0, 6.0, 12.0)
                prb_usage = round(prb_usage + (target_prb - prb_usage) * fault_ramp, 2)
                packet_loss = round(packet_loss + (target_loss - packet_loss) * fault_ramp, 2)

            # 故障状态判定
            network_status = "normal"
            if serving_rsrp < RSRP_WEAK_THRESHOLD:
                network_status = "weak_coverage"
            elif prb_usage > PRB_CONGESTION_THRESHOLD and packet_loss > LOSS_CONGESTION_THRESHOLD:
                network_status = "congestion"

            # ========== 扩展辅助字段计算 ==========
            # 重传次数：与丢包率、时延正相关
            retransmission = max(0, int(packet_loss * 0.3 + latency * 0.05))

            # 业务需求：信号越好，潜在需求越高；始终大于等于实际吞吐量
            demand_factor = 1.1 + (1.0 - load_factor) * 0.3 + random.uniform(0, 0.15)
            traffic_demand = round(throughput * demand_factor, 2)

            # 活跃用户数：与当前AP负载正相关
            active_users = int(3 + load_factor * 6 + random.randint(0, 2))

            # ========== 组装记录（字段顺序严格与数据词典一致） ==========
            record = {
                # 核心字段
                "timestamp": timestamp_str,
                "ap_id": serving_ap,
                "ap_x": AP_INFO[serving_ap]["ap_x"],
                "ap_y": AP_INFO[serving_ap]["ap_y"],
                "user_id": ue["user_id"],
                "ue_x": ue["ue_x"],
                "ue_y": ue["ue_y"],
                "rsrp": round(serving_rsrp, 2),
                "sinr": round(sinr_dbm, 2),
                "throughput": throughput,
                "latency": latency,
                "packet_loss": packet_loss,
                "prb_usage": prb_usage,
                "scenario": SCENE_NAME,
                "network_status": network_status,
                # 扩展辅助字段
                "retransmission_count": retransmission,
                "traffic_demand": traffic_demand,
                "active_users": active_users
            }
            records.append(record)

        current_time += timedelta(minutes=INTERVAL_MINUTES)

    return pd.DataFrame(records)

# ==================================================
# 4. 数据校验与导出（不需要校验可直接删除整个函数）
# ==================================================
def validate_and_export(df: pd.DataFrame):
    print("=" * 50)
    print("仿真数据生成完成")
    print(f"总记录数：{len(df)}")
    print(f"字段数量：{len(df.columns)}")

    # 校验字段完整性
    expected_core = [
        "timestamp", "ap_id", "ap_x", "ap_y", "user_id",
        "ue_x", "ue_y", "rsrp", "sinr", "throughput",
        "latency", "packet_loss", "prb_usage", "scenario", "network_status"
    ]
    missing = [f for f in expected_core if f not in df.columns]
    if missing:
        print(f"缺失核心字段：{missing}")
    else:
        print("核心字段完整")

    # 校验故障标签规则
    weak_check = df[df["network_status"] == "weak_coverage"]["rsrp"].max()
    cong_check_rsrp = df[(df["network_status"] == "congestion") & (df["rsrp"] < RSRP_WEAK_THRESHOLD)]
    print("\n标签统计 network_status：")
    print(df["network_status"].value_counts().to_string())
    print(f"\n规则校验：弱覆盖最大RSRP = {weak_check} dBm（阈值 -110）")
    if len(cong_check_rsrp) == 0:
        print("拥塞标签与弱覆盖无重叠")

    # v2.4 新增：按天故障样本分布（确认全周期每天都有异常样本）
    df["_day"] = df["timestamp"].str[:10]
    daily_fault = df[df["network_status"] != "normal"].groupby("_day").size()
    no_fault_days = [d for d in df["_day"].unique() if d not in set(daily_fault.index)]
    print(f"\n按天故障分布：共{df['_day'].nunique()}天，无异常样本的天数：{len(no_fault_days)} {no_fault_days[:5]}")
    abnormal_ratio = (df["network_status"] != "normal").mean() * 100
    print(f"全周期异常样本占比：{abnormal_ratio:.1f}%")

    # 导出CSV（去掉临时统计列）
    df = df.drop(columns=["_day"])
    df.to_csv(OUTPUT_PATH, index=False, encoding=OUTPUT_ENCODING)
    print(f"\n文件已导出：{OUTPUT_PATH}")
    print(f"字段列表：{list(df.columns)}")
    print("=" * 50)

# ==================================================
# 4.5 频谱数据生成（v2.5：n78 固定网格 + 功率包络 + 占用率，与故障注入联动）
# ==================================================
def build_spectrum_bands() -> list:
    """n78 固定频段网格：3300-3800 MHz，10 MHz/段，共 50 段，升序不重叠（≤128 上限）"""
    bands = []
    lo = SPECTRUM_BAND_START_MHZ
    idx = 0
    while lo + SPECTRUM_BAND_WIDTH_MHZ <= SPECTRUM_BAND_END_MHZ:
        hi = lo + SPECTRUM_BAND_WIDTH_MHZ
        bands.append({"band_id": f"band-{idx}", "lower_mhz": lo, "upper_mhz": hi})
        lo = hi
        idx += 1
    return bands


def _spectrum_envelope(band_centers: np.ndarray) -> np.ndarray:
    """高斯包络：中心 3550 MHz 最高，向两侧按 ((f-3550)/200)^2*K dB 衰减（边缘落差约12dB）"""
    delta = (band_centers - SPECTRUM_CENTER_MHZ) / 200.0
    return SPECTRUM_POWER_BASE_DBM - (delta ** 2) * SPECTRUM_DROP_K


def generate_spectrum_dataset(total_days: int, fault_schedule: list, events_df=None) -> pd.DataFrame:
    """逐帧生成频谱数据：每帧 3 观测点(AP) × 50 频段 = 150 行。
    与 network_data 同时间轴（1 分钟/帧）。
    v2.6：传入 events_df（网络 events 表）时，逐分钟逐 AP 的 network_status 直接取自
    该表（含 EV 事件与背景异常标签），数值按状态联动 → 与网络表事件完全一致；
    未传入时沿用 v2.5 的 FAULT_SCHEDULE 注入（3/30 天版）。
    口径：power_dbm = 观测点在频段内的接收功率（dBm，非功率谱密度）；
          occupancy_pct = 频段在 1 分钟帧内被占用时间比例（占用判定 ≥ POWER_BUSY_THRESHOLD_DBM）。
    """
    bands = build_spectrum_bands()
    n_bands = len(bands)
    band_centers = np.array([(b["lower_mhz"] + b["upper_mhz"]) / 2.0 for b in bands])
    envelope = _spectrum_envelope(band_centers)
    aps = sorted(AP_INFO.keys())  # AP-01 / AP-02 / AP-03
    n_aps = len(aps)
    total_slots = total_days * 24 * 60  # 1 分钟一帧
    start = datetime.strptime(START_TIME, "%Y-%m-%d %H:%M:%S")

    # v2.7：预构建 (timestamp, ap_id) → 异常类型集合（聚合同一 AP 下所有 UE 的标注，
    # 含 EV 事件与背景异常；多 UE 多类型全部保留，用于频段级混合标注）
    status_map = {}
    weak_sev, cong_sev = {}, {}
    if events_df is not None:
        # 第一遍：标签（v2.7 口径，聚合同一 AP 下所有 UE 的标注）
        for ts, ap, st in events_df[["timestamp", "ap_id", "network_status"]].itertuples(index=False):
            if st != "normal":
                status_map.setdefault((ts, ap), set()).add(st)

        # 第二遍：严重度（v2.8）。让频谱功率随真实恶化程度连续变化，
        # 否则频谱只有"正常/异常"两档，事件前的渐变在频谱里完全看不到，
        # 基于频谱的提前预警也就不可能触发。
        #
        # 关键约束：**未被标注为异常的帧必须留在阈值以上**（弱覆盖 −95 dBm / 拥塞 85%），
        # 否则会出现"标签说正常、频谱却越阈"的误报（实测在正常时段触发过）。
        # 于是未标注帧的严重度封顶（weak 0.80 / congestion 0.75）：
        # 表现为"正在逐渐恶化但尚未越阈"，预测把这些趋势外推后才越过阈值 →
        # 这正是"提前预警"，而不是"已发生"。
        for ts, ap, rsrp, prb in events_df[
                ["timestamp", "ap_id", "rsrp", "prb_usage"]].itertuples(index=False):
            key = (ts, ap)
            types = status_map.get(key, set())
            sev_w = clamp((-float(rsrp) - 95.0) / 17.6, 0.0, 1.0)
            if "weak_coverage" not in types:
                sev_w = min(sev_w, 0.80)
            sev_c = clamp((float(prb) - 60.0) / 25.0, 0.0, 1.0)
            if "congestion" not in types:
                sev_c = min(sev_c, 0.75)
            if sev_w > weak_sev.get(key, 0.0):
                weak_sev[key] = sev_w
            if sev_c > cong_sev.get(key, 0.0):
                cong_sev[key] = sev_c
        print(f"[v2.8] 已加载网络事件表：异常 (时间,AP) 键 {len(status_map)} 个，"
              f"严重度键 weak={len(weak_sev)} / congestion={len(cong_sev)}")

    records = []
    for i in range(total_slots):
        ts_str = (start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
        fault_ap, fault_type, fault_strength, _fault_ramp = get_active_fault(ts_str)
        # 背景干扰仅 v2.5（非 events 模式）保留：约 2% 帧随机 AP × 连续频段
        noise_ap_idx = None
        noise_start = noise_len = None
        if events_df is None and np.random.rand() < SPECTRUM_NOISE_RATIO:
            noise_ap_idx = np.random.randint(0, n_aps)
            noise_start = np.random.randint(0, n_bands - SPECTRUM_NOISE_BANDS[1])
            noise_len = np.random.randint(*SPECTRUM_NOISE_BANDS)
        for ap_idx, ap_id in enumerate(aps):
            # v2.7：状态源二选一 —— events 表（精确对齐）或 FAULT_SCHEDULE（默认）
            if events_df is not None:
                type_set = status_map.get((ts_str, ap_id), set())
            else:
                type_set = set()
                if fault_ap == ap_id:
                    type_set.add(fault_type)
            # 频段级状态数组：混合异常按段序切分（拥塞在前、弱覆盖在后；单类型则全段一致）
            statuses = ["normal"] * n_bands
            if "congestion" in type_set and "weak_coverage" in type_set:
                half = n_bands // 2
                statuses[:half] = ["congestion"] * half
                statuses[half:] = ["weak_coverage"] * (n_bands - half)
            elif "congestion" in type_set:
                statuses = ["congestion"] * n_bands
            elif "weak_coverage" in type_set:
                statuses = ["weak_coverage"] * n_bands
            power_row = envelope + np.random.normal(0, SPECTRUM_POWER_FLUCT_DB, n_bands)
            occ_row = np.random.uniform(*OCC_NORMAL_RANGE, n_bands)
            weak_mask = np.array([s == "weak_coverage" for s in statuses])
            cong_mask = np.array([s == "congestion" for s in statuses])
            normal_mask = ~(weak_mask | cong_mask)
            # 数值联动：按"严重度"连续施加（v2.8）
            #   events 模式用网络表算出的严重度（渐变期 0~1 连续），
            #   非 events 模式直接 0/1（保持 v2.5 行为）。
            if events_df is not None:
                # 严重度取 3 分钟滑动平均：单分钟取"最差 UE"会被阴影衰落（σ≈8–10 dB）主导，
                # 频谱功率逐分钟剧烈抖动（实测 -59.6/-71.9/-59.7/-89.2…），
                # 预测模型看不出趋势，提前预警也就失效了。
                def _smoothed(registry, ts: str, ap: str) -> float:
                    vals = []
                    for back in range(3):
                        key = (datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                               - timedelta(minutes=back)).strftime("%Y-%m-%d %H:%M:%S")
                        v = registry.get((key, ap))
                        if v is not None:
                            vals.append(v)
                    return sum(vals) / len(vals) if vals else 0.0

                sev_w = _smoothed(weak_sev, ts_str, ap_id)
                sev_c = _smoothed(cong_sev, ts_str, ap_id)
            else:
                sev_w = 1.0 if (fault_type == "weak_coverage" and fault_ap == ap_id) else 0.0
                sev_c = 1.0 if (fault_type == "congestion" and fault_ap == ap_id) else 0.0

            if sev_w > 0:
                # 弱覆盖：功率按严重度压深。严重度=1 时等效原来的 −41 dB（全段 < −95 dBm）；
                # 严重度 0~1 之间就是事件前的"渐变征兆"（此时标签仍可能为 normal）。
                power_row -= sev_w * (SPECTRUM_DROP_K + 33.0)
                weak_draw = np.random.uniform(*OCC_WEAK_RANGE, n_bands)
                occ_row = occ_row * (1.0 - sev_w) + weak_draw * sev_w
            if sev_c > 0:
                cong_draw = np.random.uniform(*OCC_CONGEST_RANGE, n_bands)
                cong_draw = cong_draw - np.abs(band_centers - SPECTRUM_CENTER_MHZ) / 500.0 * 15.0
                cong_draw = np.clip(cong_draw, 85.0, 95.0)
                occ_row = occ_row * (1.0 - sev_c) + cong_draw * sev_c
            # v2.5 背景干扰（仅非 events 模式）：只叠加在 normal 段，避免双重异常
            if noise_ap_idx == ap_idx and noise_len is not None and normal_mask.any():
                power_row[noise_start:noise_start + noise_len] -= SPECTRUM_POWER_DROP_DB
                occ_row[noise_start:noise_start + noise_len] += SPECTRUM_OCC_RISE
                occ_row = np.clip(occ_row, 0.0, 100.0)
            for b_idx, b in enumerate(bands):
                records.append({
                    "timestamp": ts_str,
                    "observation_id": ap_id,
                    "band_id": b["band_id"],
                    "lower_mhz": b["lower_mhz"],
                    "upper_mhz": b["upper_mhz"],
                    "power_dbm": round(float(power_row[b_idx]), 2),
                    "occupancy_pct": round(float(occ_row[b_idx]), 2),
                    "network_status": statuses[b_idx],
                    "scenario": SCENE_NAME,
                })
    return pd.DataFrame(records)


def validate_and_export_spectrum(df: pd.DataFrame):
    print("=" * 50)
    print("频谱数据生成完成（v2.5）")
    print(f"总记录数：{len(df)}")
    print(f"字段数量：{len(df.columns)}")
    # 频段网格校验：升序不重叠
    bands = df[["band_id", "lower_mhz", "upper_mhz"]].drop_duplicates().sort_values("lower_mhz")
    overlap = (bands["lower_mhz"].values[1:] < bands["upper_mhz"].values[:-1]).any()
    print(f"频段数：{len(bands)}（上限 128）；升序不重叠：{not overlap}")
    # 取值范围校验
    print(f"power_dbm 范围：[{df['power_dbm'].min()}, {df['power_dbm'].max()}] dBm")
    print(f"occupancy_pct 范围：[{df['occupancy_pct'].min()}, {df['occupancy_pct'].max()}] %")
    bad_occ = ((df["occupancy_pct"] < 0) | (df["occupancy_pct"] > 100)).sum()
    print(f"占用率越界记录数：{bad_occ}")
    # 帧级状态分布
    n_frames = df["timestamp"].nunique()
    status_count = df.drop_duplicates(["timestamp", "observation_id"])["network_status"].value_counts().to_dict()
    print(f"观测帧数：{n_frames}；帧状态分布（按观测点帧）：{status_count}")
    df.to_csv(SPECTRUM_OUTPUT_PATH, index=False, encoding=OUTPUT_ENCODING)
    print(f"文件已导出：{SPECTRUM_OUTPUT_PATH}")
    print("=" * 50)

# ==================================================
# 5. 程序入口
# ==================================================
def parse_args():
    """命令行参数：--days 生成天数(3/7/30)，--output 自定义输出路径，--events 网络事件表对齐，--seed 随机种子"""
    parser = argparse.ArgumentParser(description="AI-NetTune 仿真数据生成器 v2.8")
    parser.add_argument("--days", type=int, default=3, choices=[3, 7, 30],
                        help="生成总天数（3/7/30），默认3")
    parser.add_argument("--output", type=str, default=None,
                        help="自定义输出路径，默认按天数自动命名")
    parser.add_argument("--events", type=str, default=None,
                        help="网络事件表CSV（如 data/network_data_7d_events.csv）：频谱状态逐分钟对齐该表")
    parser.add_argument("--seed", type=int, default=None,
                        help="随机种子。给定后同一 seed + 同一 --days 可逐字节复现；"
                             "同 seed 的 7 天版与 30 天版前 7 天完全一致")
    return parser.parse_args()


def main():
    args = parse_args()
    global TOTAL_DAYS, OUTPUT_PATH, FAULT_SCHEDULE, SPECTRUM_OUTPUT_PATH
    TOTAL_DAYS = args.days
    if args.seed is not None:
        # 两套随机源都要播种：位置/波动用 random，阴影衰落用 np.random
        random.seed(args.seed)
        np.random.seed(args.seed)
        print(f"[v2.8] 随机种子 = {args.seed}（同 seed 可复现）")
    else:
        print("[v2.8] 未指定 --seed：本次数据不可复现（与历史行为一致）")
    if args.output:
        OUTPUT_PATH = args.output
        # 旧行为：--output 只改网络表，频谱仍写默认名；这是隐性坑，这里显式提示
        print(f"[v2.8] 注意：--output 仅影响网络表；频谱仍写入 {SPECTRUM_OUTPUT_PATH}")
    else:
        suffix = {3: "", 7: "_7d", 30: "_30d"}.get(args.days, "")
        OUTPUT_PATH = f"data/network_data{suffix}.csv"
        SPECTRUM_OUTPUT_PATH = f"data/spectrum{suffix}.csv"
    # 事件清单的过滤基准必须是**实际存在的频谱文件**：--days 30 时
    # SPECTRUM_OUTPUT_PATH 指向 spectrum_30d.csv（通常不存在），若照它过滤就会
    # 回退成 31 条并覆盖 fault_events.csv（独立审计 P3）。这里优先用 7 天频谱。
    if not Path(SPECTRUM_OUTPUT_PATH).exists() and Path("data/spectrum_7d.csv").exists():
        SPECTRUM_OUTPUT_PATH = "data/spectrum_7d.csv"
        print(f"[v2.8] 频谱输出不存在，事件清单按 {SPECTRUM_OUTPUT_PATH} 覆盖范围过滤")
    FAULT_SCHEDULE = build_fault_schedule(TOTAL_DAYS)  # 按天数延伸故障时间表
    print(f"[v2.8] 生成 {TOTAL_DAYS} 天数据，故障注入 {len(FAULT_SCHEDULE)} 条，"
          f"渐变 {RAMP_MINUTES} 分钟 → {OUTPUT_PATH}")
    # D-2：把故障时间表落成 CSV，作为"事件"的单一事实来源。
    # 此前前端 CASE_WINDOWS 手写案例起点时间戳，交接文档提到的 fault_events.csv
    # 从未生成过（EV-001~004 只活在文档里），改数据就会失效。
    write_fault_events(FAULT_SCHEDULE)
    dataset = generate_dataset()
    validate_and_export(dataset)

    # v2.5/v2.6：同步生成频谱数据（n78 网格；--events 时与网络事件表精确对齐，供预测预警 v2 契约）
    events_df = None
    if args.events:
        events_df = pd.read_csv(args.events)
        print(f"[v2.6] 频谱状态源 = 网络事件表 {args.events}（{len(events_df)} 行）")
    spectrum = generate_spectrum_dataset(TOTAL_DAYS, FAULT_SCHEDULE, events_df=events_df)
    validate_and_export_spectrum(spectrum)

    # 如果不需要校验，把上面 validate_and_export 一行替换成下面这行即可
    # dataset.to_csv(OUTPUT_PATH, index=False, encoding=OUTPUT_ENCODING)
    # print("生成完成，文件已导出")


if __name__ == "__main__":
    main()
