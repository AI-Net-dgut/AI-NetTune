import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os

# 配置参数
start_time = datetime(2026, 1, 1, 0, 0, 0)
total_minutes = 24 * 60  # 24小时，每分钟一条记录
ap_list = ["AP-01", "AP-02", "AP-03"]
user_list = ["UE-001", "UE-002", "UE-003", "UE-004", "UE-005", "UE-006", "UE-007", "UE-008", "UE-009", "UE-010"]
scenario_list = ["校园", "商场", "体育馆"]

data_rows = []

for minute in range(total_minutes):
    current_time = start_time + timedelta(minutes=minute)
    for ap_id in ap_list:
        for user_id in np.random.choice(user_list, size=3):
            # 基础指标
            rsrp = np.random.uniform(-130, -70)
            sinr = np.random.uniform(-5, 25)
            throughput = np.random.uniform(0, 800)
            latency = np.random.uniform(2, 200)
            packet_loss = np.random.uniform(0, 8)
            prb_usage = np.random.uniform(10, 95)
            scenario = np.random.choice(scenario_list)

            # 故障标签判定
            if rsrp < -110:
                network_status = "weak_coverage"
            elif prb_usage > 85 and packet_loss > 5:
                network_status = "congestion"
            else:
                network_status = "normal"

            row = {
                "timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S"),
                "ap_id": ap_id,
                "user_id": user_id,
                "rsrp": round(rsrp, 2),
                "sinr": round(sinr, 2),
                "throughput": round(throughput, 2),
                "latency": round(latency, 2),
                "packet_loss": round(packet_loss, 2),
                "prb_usage": round(prb_usage, 2),
                "scenario": scenario,
                "network_status": network_status
            }
            data_rows.append(row)

# 生成DataFrame
df = pd.DataFrame(data_rows)

# 创建data文件夹
os.makedirs("data", exist_ok=True)
df.to_csv("data/network_data.csv", index=False, encoding="utf-8-sig")

print(f"✅ 仿真数据生成完成！共 {len(df)} 条记录，保存到 data/network_data.csv")
