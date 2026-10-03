"""
仿真数据生成器 v2.2
项目：AI-NetTune 多模态无线网络智能监测平台
设计思路：
- 固定AP点位 + UE随机位置，模拟用户移动
- 对数距离路径损耗模型 + 阴影衰落，生成RSRP
- 多AP同频干扰叠加底噪，计算SINR
- 香农公式简化计算吞吐量，映射时延、丢包、资源占用
- UE自动选择信号最优AP接入，符合真实通信机制
- 输出完整时序CSV，支持拓扑、热力图、故障研判
扩展说明：
- 核心字段15个，保持接口契约不变
- 末尾追加3个辅助字段，用于故障原因深度分析
- DeepMIMO仅用于离线频谱预测验证，不接入主链路
"""

import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta

# ==================================================
# 1. 全局配置参数（所有可调参数集中在此）
# ==================================================
# ---------- 时间配置 ----------
START_TIME = "2026-10-01 00:00:00"
TOTAL_DAYS = 3                  # 生成总天数
INTERVAL_MINUTES = 1            # 采样间隔（分钟）

# ---------- 基站AP配置 ----------
AP_INFO = {
    "AP-01": {"ap_x": 120.0, "ap_y": 220.0},
    "AP-02": {"ap_x": 380.0, "ap_y": 160.0},
    "AP-03": {"ap_x": 260.0, "ap_y": 350.0}
}
SCENE_NAME = "campus"          # 业务场景标识

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

# ---------- UE配置 ----------
UE_COUNT_PER_SLOT = 3          # 每个时刻同时在线用户数
MAP_RANGE_X = (20, 480)        # 用户活动横向范围 米
MAP_RANGE_Y = (20, 430)        # 用户活动纵向范围 米

# ---------- 输出配置 ----------
OUTPUT_PATH = "data/network_data.csv"
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

# ==================================================
# 3. 主生成逻辑
# ==================================================
def generate_dataset() -> pd.DataFrame:
    records = []
    current_time = datetime.strptime(START_TIME, "%Y-%m-%d %H:%M:%S")
    total_slots = TOTAL_DAYS * 24 * 60 // INTERVAL_MINUTES

    for _ in range(total_slots):
        timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")

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

            # 选择信号最优的AP作为服务AP
            serving_ap = max(ap_rsrp_map, key=ap_rsrp_map.get)
            serving_rsrp = ap_rsrp_map[serving_ap]

            # 计算干扰功率（所有非服务AP + 底噪）
            interference_mw = dbm_to_mw(NOISE_FLOOR_DBM)
            for ap_id, rsrp in ap_rsrp_map.items():
                if ap_id != serving_ap:
                    interference_mw += dbm_to_mw(rsrp)
            sinr_dbm = mw_to_dbm(dbm_to_mw(serving_rsrp) / interference_mw)

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

    # 导出CSV
    df.to_csv(OUTPUT_PATH, index=False, encoding=OUTPUT_ENCODING)
    print(f"\n文件已导出：{OUTPUT_PATH}")
    print(f"字段列表：{list(df.columns)}")
    print("=" * 50)

# ==================================================
# 5. 程序入口
# ==================================================
if __name__ == "__main__":
    dataset = generate_dataset()
    validate_and_export(dataset)
    
    # 如果不需要校验，把上面一行替换成下面这行即可
    # dataset.to_csv(OUTPUT_PATH, index=False, encoding=OUTPUT_ENCODING)
    # print("生成完成，文件已导出")
