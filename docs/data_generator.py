import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
import math

"""
仿真数据生成器 v2
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

# ========= 通信仿真参数 =========
TX_POWER = 23
PR0 = -30
PATH_LOSS_EXP = 3.5
SHADOW_SIGMA = 8
NOISE_POWER = -101
BANDWIDTH = 20  # MHz

# ========= 业务配置 =========
start_time = datetime(2026, 1, 1, 0, 0, 0)
total_days = 3
total_minutes = total_days * 24 * 60

# AP固定坐标(米)
ap_info = {
    "AP-01": {"ap_x": 120, "ap_y": 220},
    "AP-02": {"ap_x": 380, "ap_y": 160},
    "AP-03": {"ap_x": 260, "ap_y": 350}
}
ap_list = list(ap_info.keys())
user_list = [f"UE-{i:03d}" for i in range(1, 11)]
scenario_list = ["校园", "商场", "体育馆"]

data_rows = []


def calculate_rsrp(distance: float):
    """对数距离路径损耗模型，返回RSRP(dBm)"""
    d = max(distance, 1.0)
    rsrp = PR0 - 10 * PATH_LOSS_EXP * math.log10(d) + np.random.normal(0, SHADOW_SIGMA)
    return np.clip(rsrp, -130, -70)


for minute in range(total_minutes):
    current_time = start_time + timedelta(minutes=minute)
    for ap_id in ap_list:
        ap_x_fixed = ap_info[ap_id]["ap_x"]
        ap_y_fixed = ap_info[ap_id]["ap_y"]
        # 每个AP，每个时刻采样3个用户
        for user_id in np.random.choice(user_list, size=3):
            ue_x = np.random.uniform(50, 450)
            ue_y = np.random.uniform(50, 450)

            # 计算全部AP到此UE的RSRP，选择信号最优AP作为服务AP
            ap_signal = {}
            for ap_name in ap_list:
                ox = ap_info[ap_name]["ap_x"]
                oy = ap_info[ap_name]["ap_y"]
                dist = math.hypot(ue_x - ox, ue_y - oy)
                ap_signal[ap_name] = calculate_rsrp(dist)

            best_ap = max(ap_signal, key=ap_signal.get)
            if best_ap != ap_id:
                continue  # UE不附着当前AP，则跳过这条样本

            rsrp = ap_signal[best_ap]

            # SINR = 信号/(干扰+噪声)
            sig_lin = 10 ** (rsrp / 10)
            inter_lin = 0.0
            for ap_n in ap_list:
                if ap_n != best_ap:
                    inter_lin += 10 ** (ap_signal[ap_n] / 10)
            noise_lin = 10 ** (NOISE_POWER / 10)
            sinr_lin = sig_lin / (inter_lin + noise_lin)
            sinr_db = 10 * math.log10(sinr_lin)
            sinr_db = np.clip(sinr_db, -5, 25)

            # 香农吞吐量
            thr = BANDWIDTH * math.log2(1 + sinr_lin)
            thr = np.clip(thr, 0, 800)

            # 负载相关指标
            prb_usage = np.random.uniform(10, 95)
            if prb_usage > 80:
                packet_loss = np.random.uniform(2, 8)
                latency = np.random.uniform(50, 200)
            else:
                packet_loss = np.random.uniform(0, 3)
                latency = np.random.uniform(2, 50)

            scenario = np.random.choice(scenario_list)

            # 故障判定（旧规则不变）
            if rsrp < -110:
                network_status = "weak_coverage"
            elif prb_usage > 85 and packet_loss > 5:
                network_status = "congestion"
            else:
                network_status = "normal"

            row = {
                "timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S"),
                "ap_id": ap_id,
                "ap_x": ap_x_fixed,
                "ap_y": ap_y_fixed,
                "user_id": user_id,
                "ue_x": round(ue_x, 2),
                "ue_y": round(ue_y, 2),
                "rsrp": round(rsrp, 2),
                "sinr": round(sinr_db, 2),
                "throughput": round(thr, 2),
                "latency": round(latency, 2),
                "packet_loss": round(packet_loss, 2),
                "prb_usage": round(prb_usage, 2),
                "scenario": scenario,
                "network_status": network_status
            }
            data_rows.append(row)


df = pd.DataFrame(data_rows)
os.makedirs("data", exist_ok=True)
df.to_csv("data/network_data.csv", index=False, encoding="utf-8-sig")

print(f"✅仿真数据生成完成！总记录数：{len(df)}")
print("\n标签统计 network_status：")
print(df["network_status"].value_counts())
print("\n输出字段列表：")
print(df.columns.tolist())
