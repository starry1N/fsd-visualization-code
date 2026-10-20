# 验证记录

日期：2026-10-10（Asia/Shanghai）。目标：独立 FSD 可视化代码包 0.1.0。

## 已验证

- ROS 2 Humble 本机实际 colcon 构建成功：`fsd_rviz_plugins` 与 `fsd_visualization`，两个包均完成。
- `bash check.sh`：**4 passed in 3.25s**。使用 DDS domain 179 和合成消息，检查配置生成、真实 ROS 消息转换、源 frame/stamp 保留、5 m/s 平面速度、Odometry 模式、非有限速度、时钟暂停时的速度过期、空帧 DELETEALL、航点 frame 冲突、定位时间回退与位置跳变清理。
- 通过安装后的 `start.sh mode:=bridge rviz:=false` 启动辅助节点，接收到状态输出，退出干净。
- 检查辅助节点发布的话题仅为 `/fsd_visualization/*` 和 ROS 系统话题，无控制/发车/急停输出。
- 图形检查 `bash check-gui.sh --mode native`：PASS。实际打开 RViz，提供合成 RGB 图像、7 个原生 MarkerArray 和定位速度，运行 10 秒，最终 RViz 订阅的相关话题均为 Best Effort，正常退出。
- 图形检查 `bash check-gui.sh --mode bridge`：PASS。实际打开 RViz，提供合成图像、基础锥桶、航点、定位和速度，桥接图层订阅正常，运行 10 秒后正常退出。
- GUI 检查使用独立 DDS domain 180、仅本机通信；仅测试工具发布合成静态 TF，正常辅助节点不发布 TF。
- Python/Bash 语法、XML/YAML 解析及 README 本地链接检查通过；运行源码无 CARLA Python import、无 `/sim/` 输入话题。

## 发现并修正

最初使用标准 Humble Image 插件时，收到合成图像后退出 RViz 出现 mutex 断言、exit code -6。独立包迁入 SafeImage 插件，先断开并清空订阅回调连接，再释放订阅；修正后上述两种模式均干净退出。没有修改系统 ROS 库或算法工作区。

RViz 加载配置时先建立默认 Reliable 订阅，再切换为配置的 Best Effort，可能出现短暂 QoS 警告。GUI 检查通过 ROS graph 检查最终订阅策略，而不是把启动瞬间的警告当作最终状态。

辅助节点关闭时也处理终端 SIGINT 与 launch 转发的重复信号，避免在资源清理期间再次抛 KeyboardInterrupt。

## 验证环境与范围

构建及接口验证使用本机已有 ROS Humble 和 WUTA 消息安装空间；没有启动 CARLA、FSD 算法、传感器驱动或驾驶控制。没有改变现有算法配置。

本次未对真实 FSD 输入做现场接入、屏幕像素检查、感知精度或驾驶验收。两次 GUI 检查仅证明短时进程运行、实际订阅策略及退出流程；不证明长时稳定、另一台机器的显卡性能或端到端实时性。速度显示是定位输出，不是独立真值。

## 复现

加载自己的 FSD 消息环境后，在源码包根目录执行：

```bash
bash build.sh
bash check.sh
bash check-gui.sh --mode native
bash check-gui.sh --mode bridge
```

最后两条需要图形桌面，并自动打开/关闭 RViz。自动测试 domain 不应与实车或算法 domain 重合。原始图形检查日志副本在 `validation/gui-native.log`、`validation/gui-bridge.log`；重新运行的日志位于 `log/`。

## 2026-10-14：固定诊断面板与融合坐标表

本次沿`start_full_fsd.sh`实际bridge入口扩展已有显示桥和RViz插件包；修正ConeMap订阅类型及默认华测速度源，新增只读快照、固定面板、原始/车体坐标切换和sensor-only未启用标识。没有修改算法、控制器、CAN或任务机逻辑，没有启动实车硬件。

- 本机最小colcon构建：`fsd_rviz_plugins`及`fsd_visualization`通过。
- 隔离domain 179接口回归：6项通过。验证真实ConeMap五色接口、空结果、未收到/未启用、控制请求与缓存、重复接收旧采样时间仍判超时、无效JSON/坐标、TF缺失和零时间不使用最新TF、采样时刻插值（得到X=4而不是最新TF的X=5），以及纳秒采样时间字符串不丢精度。
- 在发布一次Transient Local全局ready/急停事件之后才创建显示桥，验证晚订阅能收到锁存值；急停事件超过1秒仍显示末次锁存值。显示桥所有发布者仍仅属于显示命名空间或ROS系统话题。
- 隔离domain 180实际RViz图形检查：native及bridge通过；新增面板实际订阅`/fsd_visualization/status`，最终QoS为Best Effort，短时运行和退出正常。已检查右侧面板与坐标表截图，任务阶段/赛项、融合/地图数量、路径速度、驱动请求、源坐标系和精确采样时间可见；车体模式加载通过。
- Python/XML/YAML/RViz解析、启动脚本Bash语法及`git diff --check`通过。

测试输入全部为隔离域合成数据（包含供显示消费的任务/控制消息），不启动控制或CAN消费者。GUI日志位于`log/gui-native.log`及`log/gui-bridge.log`；启动瞬间的既有默认Reliable→Best Effort切换警告仍可能出现，最终订阅策略检查通过。当前验证不证明实车感知精度、电机实际动作或长期运行性能。显示桥2秒断流提示仅是显示层监控，未替代CAN指令超时或控制器路径失效保护。

## 2026-10-17：独立仓库与本机环境修复

从主项目目录历史提取为独立私有仓库，保留原路径作为主项目子模块。包含已有诊断面板、显示桥、E22及其部署修复，构建产物、日志、虚拟环境不纳入版本管理。

本机最小colcon构建两个ROS包成功；隔离domain 179显示接口及安装启动测试6项通过。无线完整测试30项通过（18.24秒），包含domain 191的真实ROS订阅、TCP串口替身及双向扩展往返；未打开真实无线串口。启动器从干净环境可加载ROS/FSD并显示帮助，Bash语法及Git空白检查通过。

无线测试已改为尊重实际禁用的路径图层，外置配置和ROS测试显式启用各自需要的测试图层，不修改部署图层开关。pytest限制为<9以兼容Humble插件；Linux虚拟环境创建兼容缺少ensurepip但已安装系统pip的机器，并安装支持可编辑构建的setuptools。

[设备共存检查](docs/DEVICE_COEXISTENCE.md)记录实际串口/USB拓扑和在线资源快照。未执行真实相机/雷达与无线的开关对照测试，也未证实空口/Windows连通；现有UART发送和零接收不能作为端到端成功证据。本次未重跑GUI视觉验收，历史图形验证见上文。

## 2026-10-20：无线0.6.0实现记录（未执行验证）

调整115200 UART/62500 bit/s空速配置下的总预算至3000 B/s，缩短调度等待；新增CLI当前运行选择和PC隔离显示，去除历史运行扫描。保留必要日志完整原文、源时间、分片和落盘确认，普通GNSS状态文字由传感器快照替代。

按用户明确要求，本轮未执行自动化测试、语法/编译检查、WSL、串口通信或Foxglove现场验证，仅人工阅读修改代码和差异。既有测试的配置/选择指针夹具随契约调整，但没有运行；上文历史验证结果不能作为0.6.0通过依据。3000 B/s为应用调度预算，4 Hz/2秒为目标，不代表测得空口吞吐或实机验收结果。

## 2026-10-20：0.6.1会话恢复修复

现场读取证实无线持续接收，而Foxglove话题列表为空；刷新Foxglove后车速、状态和车辆箭头恢复。修复在clear_session后重新创建并公告SDK通道，同时重新绑定JSON缓存及重放几何，避免CLI重启后必须手动重连。按用户要求未运行自动化测试或Linux/WSL验证。现场恢复观察发生在源码修复前，不能作为修复后回归通过证据。

## 2026-10-20：0.6.2完整CLI日志（未执行验证）

将原有当前运行选择指向manager.events.jsonl，转发器接收完整journal及旧版必要journal文件名以兼容升级。既有转发测试夹具改为完整事件源，并将筛选边界用例中的无线期望改为全文。日志界面标题同步更新。按用户此前指令，本轮未运行测试、构建、WSL或实机验证。
