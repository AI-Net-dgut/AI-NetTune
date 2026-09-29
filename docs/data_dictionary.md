# AI‑NetTune 项目数据字典
CSV时序监测数据表


| 列名 | 含义 | 单位 | 数据类型 | 取值/枚举 | 说明 |
| ---- | ---- | ---- | ---- | ---- | ---- |
| timestamp | 采样时间 | yyyy‑MM‑dd HH:mm:ss | datetime | 2026‑01‑01 00:00:00 ~ 2026‑01‑02 00:00:00 | 指标采样时间戳 |
| ap_id | 接入点编号 | - | str | AP‑01,AP‑02,AP‑03 | 无线接入点ID |
| user_id | 用户编号 | - | str | UE‑001 ~ UE‑010 | 终端用户ID |
| rsrp | 参考信号接收功率 | dBm | float | -140 ~ -60 | 无线信号强度，数值越大信号越好 |
| sinr | 信干噪比 | dB | float | -10 ~ 30 | 信号质量，越高越好 |
| throughput | 吞吐量 | Mbps | float | 0 ~ 1000 | 用户下行速率 |
| latency | 时延 | ms | float | 1 ~ 500 | 端到端网络延迟 |
| packet_loss | 丢包率 | % | float | 0 ~ 100 | 数据包丢失百分比 |
| prb_usage | PRB利用率 | % | float | 0 ~ 100 | 无线资源块占用率 |
| scenario | 场景标签 | - | str | 校园、商场、体育馆 | 物理部署地点场景 |
| network_status | 网络状态 | - | str | normal、congestion、weak_coverage | 网络运行健康状态 |
