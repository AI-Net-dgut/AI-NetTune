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

def get_active_fault(timestamp_str: str):
    """按时间戳查命中的故障注入条目；未命中返回 (None, None, None)"""
    for start, end, ap, ftype, strength in FAULT_SCHEDULE:
        if start <= timestamp_str <= end:
            return ap, ftype, strength
    return None, None, None

# ==================================================
# 3. 主生成逻辑
# ==================================================
def generate_dataset() -> pd.DataFrame:
    records = []
    current_time = datetime.strptime(START_TIME, "%Y-%m-%d %H:%M:%S")
    total_slots = TOTAL_DAYS * 24 * 60 // INTERVAL_MINUTES

    for _ in range(total_slots):
        timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
        fault_ap, fault_type, fault_strength = get_active_fault(timestamp_str)

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

            # 故障注入：弱覆盖时段，对该AP信号额外衰减（制造覆盖空洞）
            if fault_type == "weak_coverage":
                ap_rsrp_map[fault_ap] -= fault_strength

            # 选择信号最优的AP作为服务AP
            serving_ap = max(ap_rsrp_map, key=ap_rsrp_map.get)
            serving_rsrp = ap_rsrp_map[serving_ap]

            # 故障注入：故障时段强制UE接入故障AP（模拟故障区域内用户只能连该AP）
            # —— 否则UE会切换到其他正常AP，故障AP上采样过少，研判占比无法超过阈值
            if fault_ap is not None:
                serving_ap = fault_ap
                serving_rsrp = ap_rsrp_map[fault_ap]

            # 计算干扰功率（所有非服务AP + 底噪）
            interference_mw = dbm_to_mw(NOISE_FLOOR_DBM)
            for ap_id, rsrp in ap_rsrp_map.items():
                if ap_id != serving_ap:
                    interference_mw += dbm_to_mw(rsrp)
            sinr_dbm = mw_to_dbm(dbm_to_mw(serving_rsrp) / interference_mw)
            sinr_dbm = clamp(sinr_dbm, -10.0, 30.0)   # v2.3: SINR 限幅，修复异常峰值

            # 计算吞吐量（香农公式简化）
            sinr_linear = dbm_to_mw(sinr_dbm)
            throughput = min(MAX_USER_THROUGHPUT, 20.0 * np.log2(1 + sinr_linear))
            throughput = round(throughput, 2)

            # 计算时延：信号越差、负载越高，时延越大
            load_factor = throughput / MAX_USER_THROUGHPUT
            latency = BASE_LATENCY_MS + max(0, (-serving_rsrp - 80) * 0.25) + load_factor * 8
            latency = round(clamp(latency, 8, 80), 2)

            # 计算丢包率：信号越差、负载越高，丢包越高
            packet_loss = BASE_PACKET_LOSS + max(0, (-serving_rsrp - 90) * 0.15) + load_factor * 2
            packet_loss = round(clamp(packet_loss, 0.1, 22.0), 2)

            # 计算PRB资源占用率
            prb_usage = 40.0 + load_factor * 55.0 + random.uniform(-3, 3)
            prb_usage = round(clamp(prb_usage, 10.0, 98.0), 2)

            # 故障注入：拥塞时段，抬升目标AP的PRB与丢包（制造容量拥塞）
            if fault_type == "congestion" and serving_ap == fault_ap:
                prb_usage = round(clamp(fault_strength + random.uniform(-2, 3), 85.0, 98.0), 2)
                packet_loss = round(clamp(packet_loss + 3.0, 5.0, 12.0), 2)

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
    if events_df is not None:
        for ts, ap, st in events_df[["timestamp", "ap_id", "network_status"]].itertuples(index=False):
            if st != "normal":
                status_map.setdefault((ts, ap), set()).add(st)
        print(f"[v2.7] 已加载网络事件表：异常 (时间,AP) 键 {len(status_map)} 个")

    records = []
    for i in range(total_slots):
        ts_str = (start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
        fault_ap, fault_type, fault_strength = get_active_fault(ts_str)
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
            # 数值联动（两种状态源共用同一口径）
            if weak_mask.any():
                # 弱覆盖：功率整体压深至全段 < -95 dBm（对齐后端弱覆盖高亮阈值），占用 10-30%
                power_row[weak_mask] -= SPECTRUM_DROP_K + 33.0
                occ_row[weak_mask] = np.random.uniform(*OCC_WEAK_RANGE, int(weak_mask.sum()))
            if cong_mask.any():
                # 拥塞：占用 85-95%（中心加权），仅对拥塞段生效
                occ_row[cong_mask] = np.random.uniform(*OCC_CONGEST_RANGE, int(cong_mask.sum()))
                occ_row[cong_mask] -= np.abs(band_centers[cong_mask] - SPECTRUM_CENTER_MHZ) / 500.0 * 15.0
                occ_row[cong_mask] = np.clip(occ_row[cong_mask], 85.0, 95.0)
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
    """命令行参数：--days 生成天数(3/7/30)，--output 自定义输出路径，--events 网络事件表对齐"""
    parser = argparse.ArgumentParser(description="AI-NetTune 仿真数据生成器 v2.6")
    parser.add_argument("--days", type=int, default=3, choices=[3, 7, 30],
                        help="生成总天数（3/7/30），默认3")
    parser.add_argument("--output", type=str, default=None,
                        help="自定义输出路径，默认按天数自动命名")
    parser.add_argument("--events", type=str, default=None,
                        help="网络事件表CSV（如 data/network_data_7d_events.csv）：频谱状态逐分钟对齐该表")
    return parser.parse_args()


def main():
    args = parse_args()
    global TOTAL_DAYS, OUTPUT_PATH, FAULT_SCHEDULE, SPECTRUM_OUTPUT_PATH
    TOTAL_DAYS = args.days
    if args.output:
        OUTPUT_PATH = args.output
    else:
        suffix = {3: "", 7: "_7d", 30: "_30d"}.get(args.days, "")
        OUTPUT_PATH = f"data/network_data{suffix}.csv"
        SPECTRUM_OUTPUT_PATH = f"data/spectrum{suffix}.csv"
    FAULT_SCHEDULE = build_fault_schedule(TOTAL_DAYS)  # 按天数延伸故障时间表
    print(f"[v2.6] 生成 {TOTAL_DAYS} 天数据，故障注入 {len(FAULT_SCHEDULE)} 条 → {OUTPUT_PATH}")
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
