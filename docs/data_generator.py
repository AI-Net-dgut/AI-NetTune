"""
仿真数据生成器 v2.1
参考Cell-Free与DeepMIMO射线追踪仿真通用思路：
- 固定AP点位，UE随机位置模拟用户移动
- 对数距离路径损耗模型 + 阴影衰落生成RSRP
- 多AP干扰计算SINR，香农公式计算吞吐量
- 输出时序CSV，新增平面坐标字段，用于拓扑、热力图可视化
【说明】主仿真器输出完整业务数据集；DeepMIMO数据集独立用于电磁频谱预测模型验证，不接入本主链路
故障标签规则：
normal         → 网络正常
weak_coverage  → 弱覆盖（rsrp < -110）
congestion     → 网络拥塞（prb_usage > 85 且 packet_loss > 5）
"""

import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta

# ====================== 配置参数 ======================
# 时间配置
START_TIME = "2026-10-01 00:00:00"
TOTAL_DAYS = 3  # 生成总天数
INTERVAL_MINUTES = 1  # 采样间隔

# 基站AP配置
AP_INFO = {
    "AP-01": {"ap_x": 120, "ap_y": 220},
    "AP-02": {"ap_x": 380, "ap_y": 160},
    "AP-03": {"ap_x": 260, "ap_y": 350}
}

# 通信模型参数
PATH_LOSS_EXP = 3.2    # 路径损耗指数
PR0 = -28.0            # 1米处参考接收功率 dBm
SHADOW_STD = 3.5       # 阴影衰落标准差 dB
NOISE_POWER = -105     # 底噪功率 dBm
MAX_THROUGHPUT = 150   # 单用户峰值吞吐量 Mbps

# 故障判定阈值
RSRP_WEAK_THRESHOLD = -110
PRB_CONGESTION_THRESHOLD = 85
LOSS_CONGESTION_THRESHOLD = 5

# UE配置
UE_COUNT_PER_TIME = 3  # 每个时刻同时在线用户数
MAP_SIZE_X = 500       # 场景横向范围 米
MAP_SIZE_Y = 450       # 场景纵向范围 米
SCENE_NAME = "campus"  # 业务场景

# ====================== 工具函数 ======================
def dbm_to_mw(dbm):
    """dBm转毫瓦"""
    return 10 ** (dbm / 10)

def mw_to_dbm(mw):
    """毫瓦转dBm"""
    return 10 * np.log10(mw)

# ====================== 主生成逻辑 ======================
if __name__ == "__main__":
    records = []
    current_time = datetime.strptime(START_TIME, "%Y-%m-%d %H:%M:%S")
    total_minutes = TOTAL_DAYS * 24 * 60

    for minute_idx in range(total_minutes):
        timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")

        # 生成该时刻所有UE的随机位置
        ue_list = []
        for ue_idx in range(UE_COUNT_PER_TIME):
            ue_id = f"UE-{ue_idx+1:02d}"
            ue_x = random.uniform(20, MAP_SIZE_X - 20)
            ue_y = random.uniform(20, MAP_SIZE_Y - 20)
            ue_list.append({"user_id": ue_id, "ue_x": ue_x, "ue_y": ue_y})

        # 逐个UE计算信号，选择最优AP接入
        for ue in ue_list:
            ap_signal_map = {}
            # 计算所有AP的接收功率
            for ap_id, ap_pos in AP_INFO.items():
                distance = np.sqrt((ue["ue_x"] - ap_pos["ap_x"])**2 + (ue["ue_y"] - ap_pos["ap_y"])**2)
                distance = max(distance, 1.0)  # 避免距离为0
                path_loss = PR0 - 10 * PATH_LOSS_EXP * np.log10(distance)
                shadow_fade = np.random.normal(0, SHADOW_STD)
                rsrp = path_loss + shadow_fade
                ap_signal_map[ap_id] = rsrp

            # 选择信号最好的AP作为服务AP
            best_ap = max(ap_signal_map, key=ap_signal_map.get)
            best_rsrp = ap_signal_map[best_ap]

            # 计算干扰功率（所有非服务AP的功率和）
            interference_mw = 0
            for ap_id, rsrp in ap_signal_map.items():
                if ap_id != best_ap:
                    interference_mw += dbm_to_mw(rsrp)
            interference_mw += dbm_to_mw(NOISE_POWER)
            sinr = mw_to_dbm(dbm_to_mw(best_rsrp) / interference_mw)

            # 计算吞吐量（香农公式简化）
            sinr_linear = dbm_to_mw(sinr)
            throughput = min(MAX_THROUGHPUT, 20 * np.log2(1 + sinr_linear))
            throughput = round(throughput, 2)

            # 计算时延、丢包、PRB占用
            # 信号越差，时延越高、丢包越高
            base_latency = 12
            latency = base_latency + max(0, (-best_rsrp - 80) * 0.25)
            latency = round(latency, 2)

            base_loss = 0.5
            packet_loss = base_loss + max(0, (-best_rsrp - 90) * 0.15)
            packet_loss = round(min(packet_loss, 22), 2)

            prb_usage = 40 + (throughput / MAX_THROUGHPUT) * 55 + random.uniform(-3, 3)
            prb_usage = round(max(10, min(prb_usage, 98)), 2)

            # 故障状态判定
            network_status = "normal"
            if best_rsrp < RSRP_WEAK_THRESHOLD:
                network_status = "weak_coverage"
            elif prb_usage > PRB_CONGESTION_THRESHOLD and packet_loss > LOSS_CONGESTION_THRESHOLD:
                network_status = "congestion"

            # ========== 原有15个核心字段 ==========
            record = {
                "timestamp": timestamp_str,
                "ap_id": best_ap,
                "ap_x": AP_INFO[best_ap]["ap_x"],
                "ap_y": AP_INFO[best_ap]["ap_y"],
                "user_id": ue["user_id"],
                "ue_x": round(ue["ue_x"], 2),
                "ue_y": round(ue["ue_y"], 2),
                "rsrp": round(best_rsrp, 2),
                "sinr": round(sinr, 2),
                "throughput": throughput,
                "latency": latency,
                "packet_loss": packet_loss,
                "prb_usage": prb_usage,
                "scenario": SCENE_NAME,
                "network_status": network_status,

                # ========== 新增3个辅助字段（追加在最后） ==========
                # 重传次数：与丢包率正相关，符合真实网络规律
                "retransmission_count": max(0, int(packet_loss * 0.35)),
                # 业务需求：比实际吞吐高10%~40%，用于区分「需求低」还是「网络差」
                "traffic_demand": round(throughput * random.uniform(1.1, 1.4), 2),
                # 该AP当前时刻活跃用户总数：反映接入负载压力
                "active_users": random.randint(3, 8)
            }

            records.append(record)

        current_time += timedelta(minutes=INTERVAL_MINUTES)

    # ====================== 输出保存 ======================
    df = pd.DataFrame(records)
    output_path = "data/network_data.csv"
    df.to_csv(output_path, index=False, encoding="utf-8-sig")

    # ====================== 控制台打印统计 ======================
    print("✅仿真数据生成完成！总记录数：", len(df))
    print("\n标签统计 network_status:")
    print(df["network_status"].value_counts())
    print("\n输出字段列表：")
    print(list(df.columns))
