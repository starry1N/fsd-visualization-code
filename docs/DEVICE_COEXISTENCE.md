# E22 与相机、雷达共存检查

核对日期：2026-10-17，工控机 WUTA2026_FINAL，ROS 2 Humble。以下区分源码检查、在线观察和未完成验证。

## 设备与接口

| 设备 | 本机接口 | 实际职责 |
| --- | --- | --- |
| E22 / CH340 | `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` → `/dev/ttyUSB1`；USB 1-5、12 Mbit/s | 单独无线进程打开，UART 9600 8N1 |
| 华测 / Prolific | `/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_BKEDc143M08-if00-port0` → `/dev/ttyUSB0`；USB 1-9 | 惯导串口；无线程序不得改用该端口 |
| ZED 2i 图像 | USB 2-5、5000 Mbit/s、uvcvideo | 相机驱动读取图像，E22 不打开视频设备 |
| ZED HID | USB 1-6.2、12 Mbit/s，经 USB hub | 相机辅助接口；与 E22 同属 USB 2 根总线，但不是同一个端口 |
| RoboSense M1 | 根项目 enp8s0；UDP 6699 / 7788 | 雷达驱动收包，不使用 ttyUSB |

USB 路径来自 `lsusb -t`，雷达接口来自宿主部署配置及实际 `fusion_modules_hardware.launch.py` 选择的 `rsm1_hardware.yaml`。设备编号不是跨机器的通用配置。

该 CH340 没有唯一序列号。单个同型号适配器时 by-id 路径通常不随插入顺序变化；同时插入多个相同适配器可能重名。部署必须复核 `readlink -f` 与 USB 拓扑，不按 ttyUSB 编号猜设备。BRLTTY 曾误占 CH340，本机现已 mask `brltty.service` 和 `brltty-udev.service`；新机器需独立核对。

## 软件行为与在线观察

- `radio_link/fsd_radio/transport.py` 只打开配置的串口，关闭软硬件流控；没有 USB 重置、摄像头控制或雷达网络配置操作。
- `ros_sender.py` 订阅显示 Marker、真实位姿/速度与 TF；不订阅图像或原始点云，不启动传感器，不发布驾驶、GO 或车辆 TF。JSON 扩展仅进入专用扩展话题。
- 无线收发在线程中运行，显示缓存、队列与包长有上限。默认双向 UART 总预算 160 字节/秒；这不是整机 CPU 或内存的硬上限。
- 无线进程 PID 16403 的设备文件句柄仅包含 E22 ttyUSB1（另有终端及 ROS 共享内存），未打开 ttyUSB0、视频或 HID 设备。观察时约 1.9% CPU、65,232 kB RSS；这是无有效显示输入时的快照，不是满载测试。
- 早前约 8 秒状态采样中 TX 增加 68 字节/2包，RX 为0，有重试且没有对端会话。只能证明写入 UART，不能证明空口或 Windows 收到。

## 结论与验证边界

源码和当前设备映射未发现直接的串口冲突或对相机/雷达接口的改写。E22 流量远低于 USB 总线容量，ZED 图像也位于独立的 USB 3 总线；仍共享主机控制器、CPU、供电和电磁环境，不能保证任何接线和满载工况都完全无影响。

本轮未启动、停止或重置真实传感器，未进行无线开/关对照的相机帧率、雷达丢包或电磁兼容测试。内核日志可见 ZED HID 在22:30:52、22:36:26等时刻重置，以及华测协议进程在22:31:08、22:36:43等时刻段错误；没有证据将这些事件归因于 E22。后续应在已有安全台架会话中，同步记录无线开/关前后的图像与点云频率、驱动错误、USB日志及电源状态。仅看 ROS topic 名称存在不能证明有数据；本机域0可发现相关topic名称，但本次未将其视为驱动持续运行或帧率正常的证据。

## 复核命令

```bash
ls -l /dev/serial/by-id/
lsusb -t
fuser -v /dev/ttyUSB0 /dev/ttyUSB1
systemctl is-enabled brltty.service brltty-udev.service
journalctl -k -b --no-pager | rg 'ch341|ttyUSB|reset|segfault'
# 加载已有 ROS 环境后读取，不另开串口读者：
ros2 topic echo /fsd_radio/status --once
```

显示和无线均独立启动；先运行 `bash start.sh mode:=bridge`，再运行 `bash radio_link/start_linux.sh --mode bridge`。无线 `connected=true` 只表示本机串口已打开；应同时检查 RX、peer_age_s、重试/失败和输入图层状态，完成对端带ID的 Ping/Pong 后再宣称双向通信正常。
