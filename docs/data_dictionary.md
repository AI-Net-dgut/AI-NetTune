# 数据字典 - 网络仿真实时序数据集

> 说明：仿真逻辑参考Cell-Free无蜂窝MIMO仿真思路，采用固定AP点位+随机移动UE，对数距离路径损耗模型生成信号，保留原有全部字段，新增坐标字段与扩展辅助字段，输出CSV时序数据集。

|字段名|数据类型|单位|说明|
| ---- | ---- | ---- | ---- |
|timestamp|string|-|时间戳，连续3天时序，用于时序可视化、历史态势预测|
|ap_id|string|-|接入点AP编号，如AP-01，固定AP设备|
|ap_x|float|米|AP设备平面X坐标，同一个AP固定不变|
|ap_y|float|米|AP设备平面Y坐标，同一个AP固定不变|
|user_id|string|-|用户终端UE编号|
|ue_x|float|米|用户终端平面X坐标，随时间变化模拟用户移动|
|ue_y|float|米|用户终端平面Y坐标，随时间变化模拟用户移动|
|rsrp|float|dBm|接收信号功率，基于距离路径损耗模型计算，距离越远信号越弱|
|sinr|float|dB|信干噪比，考虑多AP之间干扰计算得到|
|throughput|float|Mbps|吞吐量，基于香农容量公式，由SINR计算|
|latency|float|ms|网络时延，随PRB资源负载动态变化|
|packet_loss|float|%|丢包率，高负载场景下数值上升|
|prb_usage|float|%|无线PRB资源占用率，0~100|
|scenario|string|-|场景类型：校园 / 商场 / 体育馆|
|network_status|string|-|网络状态标签：normal(正常)、weak_coverage(弱覆盖，rsrp<-110)、congestion(拥塞，prb_usage>85且packet_loss>5)|
|retransmission_count|int|次|单位时间内数据重传次数，与丢包率正相关，用于故障原因辅助判断|
|traffic_demand|float|Mbps|用户侧业务需求吞吐量，始终≥实际吞吐量，用于区分低需求与网络受限|
|active_users|int|个|当前AP下的活跃用户总数，反映基站接入负载压力|
