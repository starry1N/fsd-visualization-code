# 无线遥测验证记录

## 2026年10月19日 必要字段协议

- radio_link版本0.2.0，必要几何schema 2；固定样式、文字、namespace/marker ID和源端逐快照统计移出无线消息。
- `python -m pytest -q`：46 passed，1 skipped；ROS smoke因无rclpy跳过。包含必要二进制的真实SDK WebSocket订阅、PC本地展开、虚拟串口双向传输和旧JSON解码兼容性。
- 验证了全8种必要几何、源TF后的点坐标、包围框尺寸/姿态、箭头端点、源颜色索引、配置不匹配拒绝、畸形帧、空快照清除、有限解压与按原样本计算的UART成本。
- 同一组合成样本，计入分片和空ACK后：车辆280→104字节、24锥桶768→300字节、48点路径884→172字节、6包围框536→160字节、空地图336→84字节。不是空口实测。
- 原接收程序占用8765，独立演示改用SDK与控制接口的临时端口并正常退出，未抢占COM3或停止现场程序。
- 当前版本未执行Linux ROS/真实TF与双端无线验收；已有旧版接收链路的成功记录不能替代本版验收。调度间隔、160字节/秒预算和重试规则保持不变。

## 2026年10月17日 初版离线验证

日期：2026年10月17日。范围仅为新增 radio_link，未运行原有RViz/算法全栈测试。

已执行：

- Windows独立Python虚拟环境，可编辑安装项目及 `foxglove-sdk==0.29.2`、pyserial 3.5、PyYAML。
- `python -m pytest -q`：29 passed，1 skipped，约4.7秒。
- 跳过项：`test_ros_smoke.py`，本机无rclpy；该文件提供工控机可执行的隔离ROS domain 191和虚拟串口验收。
- 真实pyserial API经TCP串口替身进行双向场景/JSON分包传输，故意使用7字节TCP片段验证流式解析。
- 官方SDK本机WebSocket服务：实际连接握手、广告、订阅速度JSON、客户端Publish上行通过；全部10种受支持Marker转换完成SDK序列化。
- 丢ACK、最终一次重试成功、重复包、会话变化与序号回绕、有界重组与解压、双向160字节/秒节拍测试通过。
- `inspect`正确解析给定的两个显示配置，识别被排除的图像/原始点云、native/bridge映射和实车速度覆盖；个人topics YAML外置回归通过。
- `demo --duration 1.5`启动并停止本地SDK服务，未打开COM3。
- Python源码编译、Ruff F级静态检查、PowerShell脚本语法解析、Git Bash `bash -n`、`git diff --check`通过。

未执行：Linux ROS 2运行、真实TF与算法话题接入、COM3收发、双模块空口试验、实际已安装Foxglove的界面查看和长期稳定性测试。未启动FSD、惯导、CAN或车辆控制，也未改写无线模块参数。

后续应先按README完成带id的双向Ping/Pong，再逐层增加显示数据，并记录实际数据年龄、重试与往返延迟。当前结果不能证明无线实际吞吐、全部Linux驱动兼容性或全部图层的实时刷新率。

## 2026-10-17 Linux工控机补充验证

在本机系统Python3.10虚拟环境安装Linux/PC/测试依赖，加载已有ROS 2 Humble及FSD消息环境后，完整测试：**30 passed in 18.24s**。包含domain 191的ROS订阅、消息转换及双向扩展测试、TCP串口替身、Foxglove SDK WebSocket；不接触真实硬件。测试显式启用测试需要的可选路径图层，默认无线配置仍服从RViz图层开关。Humble测试插件需要pytest<9。

本机默认E22使用by-id路径，独立启动加载共享环境脚本；`--help`、Bash语法检查通过。真实无线进程曾发送UART数据但RX=0，没有完成双模块空口或Windows接收验证。串口与相机雷达的关系及尚未完成的对照测试见[设备共存检查](../docs/DEVICE_COEXISTENCE.md)。
