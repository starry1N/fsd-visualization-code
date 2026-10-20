# FSD 独立可视化代码包

ROS 2 Humble + RViz 的独立工作区，适用于已运行的 WUTA-FSD 算法、实车传感器或 ROS bag。启动时只打开 RViz 和显示辅助节点，不启动算法、传感器驱动或仿真器，不发布驾驶、发车、急停、任务或 TF 消息。

主包名：`fsd_visualization`，另含 `fsd_rviz_plugins` 图像显示插件和固定诊断面板。版本：0.1.0。依赖 ROS 2、RViz、PyYAML，以及 WUTA 的消息定义（兼容桥接模式和接口测试需要 `wuta_msgs` / `autoware_msgs`）。源码不包含模型、FSD 算法源码或机器专用的安装环境。

2026年10月17日新增可选[ E22无线遥测与Foxglove组件](radio_link/README.md)：Linux按既有显示话题量化抽样，Windows从COM3接收并经Foxglove官方SDK显示；双向JSON扩展独立于驾驶话题。无线组件独立安装/启动，原有build.sh/start.sh行为不变，Linux无线串口在`radio_link/radio.yaml`配置，启动参数可覆盖。


2026年10月19日无线组件更新至0.2.0，采用schema 2精简二进制协议：仅传输必要几何、车辆状态及时间信息，固定显示样式在接收端本地配置。升级时先更新并重启Windows接收端，再更新Linux发送端；新版接收端兼容旧格式。刷新频率和160 UART字节/秒总预算保持不变；字节数对比、升级说明及尚未完成实机验证的范围见[无线组件说明](radio_link/README.md)与[验证记录](radio_link/VALIDATION.md)。

2026年10月19日源时间升级：无线组件0.3.0/schema 3保留定位、速度和各Marker独立纳秒源时间，PC缓冲排序后以源时间发布Foxglove消息；速度改用`/radio/speed.speed_m_s`。先升级Windows接收端，再在Linux拉取并重启发送端；布局与源时间缺失的处理见[无线说明](radio_link/README.md)。

无线组件0.5.0转发CLI必要日志（错误/警告及诊断上下文、任务变化和启动/退出汇总）、CLI传感器状态与车辆摘要，具备PC落盘确认和日志断点续传；Linux原日志保留全文，需同步更新主项目的CLI子模块。布局新增“CLI 日志与传感器”，筛选规则、部署及带宽分配见[无线说明](radio_link/README.md)。

2026年10月20日无线0.5.1适配新`wuta_logs/`日志布局，兼容旧目录；CLI切回会话或pending归档移动后，按stream ID重定位并继续磁盘确认与断点续传。配套CLI可选择算法、外部驱动、华测和CAN分类日志，并单列感知驱动进程检查。

## 独立仓库与主项目引用

独立仓库：[starry1N/fsd-visualization-code](https://github.com/starry1N/fsd-visualization-code)（私有，克隆者需访问权限）。范围包含两个ROS包、显示配置与测试，以及可选E22/Foxglove组件；FSD消息包仍由原算法安装空间提供。

```bash
git clone https://github.com/starry1N/fsd-visualization-code.git
```

在 WUTA2026_FINAL 中以 `fsd-visualization-code/` 子模块挂载，路径及根启动入口保持兼容。主项目新克隆或更新后执行 `git submodule update --init --recursive`。独立使用时设置实际 `FSD_SETUP`；主项目内可复用相邻的已有FSD安装环境。修改在本仓库提交并推送，再回到主项目提交子模块指针；不要提交 build/install/log、无线虚拟环境或现场日志。

设备隔离、串口固定命名、BRLTTY及本机检查结论见[共存检查](docs/DEVICE_COEXISTENCE.md)。本机E22尚未收到对端响应，不能宣称无线已双向连通；串口`connected`仅表示打开成功。RViz与无线分别运行：`bash start.sh mode:=bridge`、`bash radio_link/start_linux.sh --mode bridge`。

## 1. 解压、构建与打开 RViz

将交付的压缩包解压到任意目录，进入 `fsd-visualization-code`。需要已有 ROS 2 Humble；FSD 自定义消息由你现有的 FSD 安装空间提供。

```bash
cd /path/to/fsd-visualization-code
export FSD_SETUP=/path/to/fsd_ws/install/setup.bash
export ROS_DOMAIN_ID=42  # 改为正在运行的算法所用 domain；脚本不固定为 42
bash build.sh
bash start.sh          # 默认自动打开 RViz
```

这里的 `/path/to/...` 都需要换成实际路径。若终端已 source 所需算法环境，可以不设置 `FSD_SETUP`。未设置且当前缺少消息包时，脚本尝试加载相邻 `../WUTA-FSD/ros2_ws/install/setup.bash`；本仓库内可直接运行 `bash start.sh`。复制到其他目录时仍需已有消息环境或显式设置 `FSD_SETUP`。脚本只加载已有安装空间，不编译 FSD、不启动任何算法；打开 RViz 前检查必需消息，缺失时输出环境配置提示。`build.sh` 只构建本代码包内的两个包。也可将 `src/` 下两个包一起放进你自己的 ROS 工作区后构建。

FSD 算法需通过原有入口单独运行，并提供有效 TF。初次连接没有图层时，先检查 topic、消息类型和 TF；可视化不会自动发车，也不会创建测试锥桶。

### 模式 A：直接查看算法原生显示输出

```bash
bash start.sh mode:=native use_sim_time:=false  # 自动打开 RViz，实车/墙钟
```

适合当前算法已经发布 `*_viz`、相机标注图像的情况。默认 `native`，不会重绘锥桶/航点；显示桥仍订阅真实算法结果供诊断面板与坐标表使用，需要FSD消息环境。

### 模式 B：算法没有 `_viz` 时使用桥接显示

```bash
bash start.sh mode:=bridge use_sim_time:=false  # 自动打开 RViz，转换算法输出
```

桥接读取 `ConeArray`、`ConeMap`、`Lane`、`PoseStamped`，产生独立 `/fsd_visualization/*` 图层。不会把桥接输出送回算法；没有来源信息时不生成 camera_guided 等来源标签，也不生成引导簇或控制预瞄点。

此模式仍直接显示已有的相机标注图像、原始雷达、引导簇和控制目标；对应算法未发布时可在配置中关闭。桥接轨迹来自定位估计，采用距离抽样、有界缓存和跳变重置，未经原算法轨迹平滑，和原生轨迹可能存在差异。

### ROS bag / 使用仿真时钟的数据源

先启动显示，再在另一个已加载相同消息环境、使用相同 domain 的终端回放：

```bash
bash start.sh mode:=bridge use_sim_time:=true  # 自动打开 RViz，等待 /clock
```

另一个终端：

```bash
ros2 bag play /path/to/bag --clock
```

Bag 应包含所需输入和 `/tf`、`/tf_static`；回放暂停时 ROS 时间也会暂停。速度过期检查使用墙钟定时器，仍会显示 STALE。定位时间戳或 ROS 时钟回退时清理轨迹，避免连接两轮回放。若 bag 含车辆控制话题，应在独立 domain 回放，确保没有实车执行节点接收这些记录。

### 速度源改用 Odometry

```bash
bash start.sh mode:=native velocity_type:=odometry use_sim_time:=false  # 自动打开 RViz
```

默认速度源是 `/chcnav/velocity` 的 `geometry_msgs/msg/TwistStamped`；odometry 模式改为配置的 `nav_msgs/msg/Odometry`。显示 `hypot(vx, vy)`，单位 m/s 和 km/h，是输入速度的平面大小，不保留倒车符号，不等于独立真值或指令速度。

### 停止

在启动终端 Ctrl+C，或关闭该次 RViz 窗口，launch 会同时停止显示辅助节点。FSD 算法与驱动由它们原来的入口管理，继续运行。默认使用机器现有的图形设备；需要本机 NVIDIA PRIME 时可选：

```bash
FSD_VIS_NVIDIA=1 bash start.sh mode:=native  # 自动打开 RViz，请求 NVIDIA
```

## 2. 修改 config（先配置，再看 Displays）

### 2.1 话题、坐标系、超时和轨迹参数

编辑 [topics.yaml](src/fsd_visualization/config/topics.yaml)：

```bash
cp -n src/fsd_visualization/config/topics.yaml src/fsd_visualization/config/topics.yaml.bak
nano src/fsd_visualization/config/topics.yaml
bash start.sh mode:=bridge  # 关闭旧显示后执行，打开 RViz 并加载修改
```

| 字段 | 含义 |
|---|---|
| `fixed_frame` | 三维数据统一显示的固定坐标系，默认 `map` |
| `target_frame` | RViz 观察参考系和速度文字附着的坐标系，默认 `base_link`；速度文字会随此真实 TF 移动 |
| `frame_rate` | RViz 重绘目标，默认 30，不是推理频率 |
| `stale_seconds` | 速度输入超过此墙钟间隔后显示 STALE，默认 1 秒 |
| `trajectory_max_points` | 桥接轨迹最多保留点数，默认 5000 |
| `trajectory_min_distance` | 轨迹采样最小三维间距，默认 0.05 m |
| `trajectory_max_step` | 相邻位置超过此三维距离则重置轨迹，默认 5 m；应按定位频率/速度调整 |
| `topics.*` | 输入话题名；要求绝对话题名，空字符串禁用相应可选输入/原生图层 |

如果你的系统使用 `odom` 而没有 `map`，将 fixed_frame 改为 `odom`，并检查所有所需数据能通过 TF 转换到它。没有 `base_link` 时也需修改 target_frame，例如先改为 `odom` 做固定视角检查；本包不会伪造 map→odom 或车体 TF。

原生图层使用 `*_markers` 配置；桥接图层读取 `fused_cones`、`cone_map`、`centerline`、`final_waypoints`、`pose` 等输入。把原生话题设为空并不等于禁用桥接转换；要禁用桥接输入，需把对应算法输入键设为空。

### 2.2 显示样式与个人配置

编辑 [fsd.rviz](src/fsd_visualization/config/fsd.rviz)，或在 RViz 中调整后使用 **File → Save Config As** 保存个人配置。

启动时从模板和 topics.yaml 生成临时 `.rviz` 文件；**直接 Save Config 只写入临时文件，下次启动不保留，退出时该临时目录会被清理。** 需另存到永久路径，再用：

```bash
bash start.sh mode:=native rviz_config:=/absolute/path/my-view.rviz  # 自动打开 RViz
```

生成器仍会用 topics.yaml 覆盖已知图层的 Topic、Fixed Frame、Target Frame 和 Frame Rate。持久改这些字段请改 topics.yaml；其他图层开关、点大小、透明度和窗口布局保留模板值。自定义模板需保留原显示项名称，才能匹配自动话题映射。另存时若改名，需手工管理改名项的 topic。

将项目话题配置另存为个人文件时：

```bash
bash start.sh mode:=bridge topics_config:=/absolute/path/my-topics.yaml  # 自动打开 RViz
```

### 2.3 算法端要启用的调试输出

- 相机节点需要发布标注图像；当前 Python YOLO 支持启动参数 `publish_annotated_image: true`、`annotated_topic`。
- 当前 C++ 融合节点的引导簇/来源输出需要启动时 `publish_fusion_visualization: true`。在算法自己的 launch/参数 YAML 中启用并重启该算法节点；本包不修改其参数，也不启动它。
- 旧版 FSD 若没有这些输出，使用 bridge 模式。基础 ConeArray 不包含来源点集，因此不能由本包还原真实引导聚类；控制预瞄也必须来自控制器真实输出。
- 修改原算法参数仍按你的工作区构建/重启规则处理，与 `.rviz` 显示设置分开。

## 3. RViz 各项信息详解

| 图层 | 默认原生话题 | 意义 |
|---|---|---|
| Grid | 无 | 每格 1 m 的空间参照，与道路/地图内容无关 |
| Camera detections | `/camera/yolo/image_annotated` | 同帧相机检测框、类别、置信度；画面已有文字完全由相机节点提供 |
| Camera-guided clusters (fusion) | `/perception/fused/guided_clusters_viz` | 算法实际引导簇点集与包围框；仅原生输出，本包不重算 |
| Raw LiDAR (optional) | `/hesai/pandar` | 整帧原始点云，默认关闭，按 Z 着色 |
| Fused targets and sources | `/perception/fused/cones_viz` | 本帧融合目标与来源。原生图层颜色由算法决定，当前 FSD 通常绿色匹配、橙色引导、青色未匹配 |
| Cone map | `/mapping/cone_map_viz` | 跨帧累计、关联确认的地图锥桶 |
| Centerline | `/planning/centerline_viz` | 边界检测输出的中心参考路线 |
| Final planned path | `/planning/final_waypoints_viz` | 路径生成输出，控制器准备跟踪的航点几何路线 |
| Driven trajectory | `/planning/driven_trajectory_viz` | 定位系统估计的历史行驶路线，不是外部真值 |
| Control target | `/control/target_viz` | 控制器真实预瞄目标/范围，仅显示它实际发布的内容 |
| FSD localization speed | `/fsd_visualization/speed` | FSD 定位速度，输入过期或非有限值后显示 STALE |

Bridge 模式将融合目标、地图、中心线、最终路径和行驶轨迹改为 `/fsd_visualization/*` 输出。此时融合目标名字明确标注“semantic colors; sources unavailable”：红/黄/蓝/橙代表锥桶类别，白色代表未知，不表示融合来源。桥接中心线为绿色、最终路径为青色、轨迹为金黄色，方便区分。

### Displays 属性速查

| 属性 | 含义 |
|---|---|
| Enabled / 复选框 | 是否显示该项，不影响车辆控制 |
| Topic | 输入话题；需匹配插件的消息类型 |
| Status | 显示/消息/TF 状态，OK 不证明算法正确或数据新鲜 |
| Namespaces | Marker 中各组图形的显示开关 |
| Fixed Frame | 统一三维坐标基准；默认 map |
| Views / Target Frame | 观察参考系；默认 base_link |
| PointCloud2 / Decay Time=0 | 只显示最新点云，增加时会叠加历史扫描并增加开销 |
| Points / Size (Pixels) | 原始点云的屏幕点大小；其他样式使用 Size (m) |
| AxisColor / Axis=Z | 高度伪彩，不是相机语义颜色 |
| Intensity / Channel Name | 改为反射强度着色时需要输入包含 intensity 字段 |
| Alpha | 透明度，0 透明、1 不透明 |
| Image / Normalize Range | 数值图像的范围显示，不是 YOLO 阈值；普通彩色图中通常隐藏 |
| Best Effort / Keep Last / Depth 1 | 优先最新传感器数据，允许丢帧，不积累大队列 |
| Volatile | 等待新消息，不自动补发订阅前的历史数据 |

原生 MarkerArray 颜色、尺寸、文字由算法发布者决定。模型置信度不等于三维位置精度。图层没消息、引导簇为空、停车时无轨迹或控制点，都需要结合真实算法条件判断。

## 4. 输入契约与保护显示语义

| bridge 输入 | 消息类型 |
|---|---|
| fused_cones | `wuta_msgs/msg/ConeArray`，含 header、cones、position、color |
| cone_map | `wuta_msgs/msg/ConeMap`，含五色数组及is_closed |
| centerline / final_waypoints | `autoware_msgs/msg/Lane`，含 header 和 waypoints 的 pose |
| pose | `geometry_msgs/msg/PoseStamped` |
| velocity | `geometry_msgs/msg/TwistStamped` |
| odometry | `nav_msgs/msg/Odometry` |

源 frame_id 和采样 stamp 保留；空 frame 的几何输入丢弃，不擅自改成 map。航点有不同 frame 时清空该条路径，避免将不同坐标系直接连线。非有限坐标跳过；空锥桶/路径消息使用 DELETEALL 主动清除旧图形。实时融合圆柱设置 ROS 时间 1 秒寿命，地图/规划/轨迹保留到更新或清理；这些静态图层保留不能用于证明输入仍新鲜。

轨迹按距离采样，缓存有上限；位置跃迁、frame 变化、时间回退会开始新段，避免错误长线。速度过期使用墙钟，ROS 暂停时也会更新状态。

`/fsd_visualization/status` 是 JSON 字符串，包括速度源、速度是否过期、各被订阅输入的墙钟帧龄（尚未收到为 null）、轨迹点数和模式。native与bridge模式均提供算法结果诊断；原生图像/点云Marker图层仍需结合RViz自身Status检查。辅助节点自身异常退出或 RViz 退出，launch 会停止整个显示会话。

## 5. 诊断与测试

在已经加载 ROS 和算法消息环境的终端：

```bash
ros2 topic list -t
ros2 topic info /chcnav/velocity -v
ros2 topic info /perception/fused/cones_viz -v
ros2 run tf2_ros tf2_echo map base_link
ros2 topic echo /fsd_visualization/status --once --qos-reliability best_effort
```

有话题但没有数据：核对 ROS_DOMAIN_ID、ROS_LOCALHOST_ONLY、发布端与显示端 QoS、use_sim_time、/clock 和 TF。Best Effort 发布者与 Reliable 订阅者不兼容；保持显示订阅 Best Effort 可接收 Reliable 或 Best Effort 发布者。

运行接口回归：

```bash
bash check.sh
```

测试默认独立 domain 179、仅本机通信；不要将 FSD_VIS_TEST_DOMAIN 设成真实算法/车辆 domain。测试在隔离域发布合成锥桶、地图、路径、定位、速度、任务和控制指令输入，仅显示节点接收；不启动任何驾驶消费者。它不启动 RViz GUI；检查真实消息接口、5 m/s 速度计算、速度超时/NaN、空帧清理、坐标帧冲突、轨迹回退/跳变、安装后的 launch 启停，以及辅助发布者没有控制 topic。

有可用图形桌面时，可进一步进行窗口检查（每条命令打开并关闭一个 RViz 窗口）：

```bash
bash check-gui.sh --mode native
bash check-gui.sh --mode bridge
```

图形检查默认独立 domain 180，会生成合成图像、Marker、定位和测试用静态 TF，检查最终 Best Effort 订阅（含诊断面板）、进程持续运行和干净退出。测试 TF 仅由测试工具发布，正常 `start.sh` 不发布 TF。该检查不评估相机检测精度、屏幕实际绘制内容或真实驾驶性能。加载图层时可能短暂出现默认 Reliable 订阅的 QoS 警告；检查要求最终订阅都为 Best Effort。

只有自动化检查需要无图形启动时使用 `rviz:=false`；所有上面的用户显示启动默认打开 RViz。默认图像使用 `fsd_rviz_plugins/SafeImage`，保留标准 Image 渲染，修正 Humble 中重复释放订阅连接的问题。长期运行和其他机器上的显卡表现仍需现场验证。

## 6. 目录与移植

```text
fsd-visualization-code/
├── build.sh / start.sh / check.sh / check-gui.sh
├── scripts/environment.sh       # 只加载 ROS 与已有 FSD 环境
├── src/fsd_visualization/
│   ├── package.xml / setup.py / setup.cfg
│   ├── launch/view.launch.py    # 默认 RViz + 辅助节点
│   ├── config/topics.yaml       # topic/frame/超时配置
│   ├── config/fsd.rviz          # 11 个显示项模板
│   └── fsd_visualization/       # 转换节点与配置生成
├── src/fsd_rviz_plugins/        # 独立 SafeImage 显示插件
├── tests/                      # 隔离域 ROS 接口测试
├── VALIDATION.md                # 本次验证范围
└── LICENSE
```

压缩包只包含源码、配置、脚本、文档和测试；不含 build/install/log，不依赖交付机器的绝对路径。可以复制整个目录在另一台有 ROS/FSD 消息的机器构建，或将两个 ROS 包放入现有工作区，运行 `ros2 launch fsd_visualization view.launch.py`（默认打开 RViz）。

接口以当前 WUTA 消息结构为目标，不保证自动兼容任意第三方 FSD 的消息定义。第三方栈仅使用标准 MarkerArray/Image/PointCloud2 时可用 native 模式并映射话题；如果基础算法消息结构不同，需要改桥接适配代码。

RViz 和 launch 用法参考 [ROS 2 RViz Humble 源码与指南](https://github.com/ros2/rviz/tree/humble/rviz2/doc)、[launch_ros Node 参数接口](https://github.com/ros2/launch_ros/blob/humble/launch_ros/actions/node.py)。

## 7. 固定诊断面板与锥桶坐标表（2026-10-14）

默认RViz右侧打开 **FSD diagnostics**：上方显示任务阶段/实际门控字段、融合诊断、五色地图数量/闭合、中心线/最终路径点数与速度范围、控制速度/转角/开度、华测车体速度、定位、自检和急停输入。单元格悬停可查看完整详情及输入topic。正驱动请求不等于电机已经转动，当前没有驱动轮速反馈。

下方坐标下拉框默认选择“原始雷达/消息坐标系”，实际帧名以融合header为准。切换“车体坐标系 base_link”时显示按采样TF转换的XYZ（米）；不使用最新TF替代。缺少TF/时间戳时，车体XYZ显示`--`并列出原因。表格显示本帧序号、颜色、XYZ及置信度；序号不跨帧稳定，置信度不表示位置精度。

“未收到”区别于“空结果”；“超时（缓存）”保留最后数据用于诊断。输入接收墙钟年龄与可用的采样ROS时间年龄分别显示，默认1秒显示阈值仅用于监控，不代表驾驶门控。急停栏显示末次锁存事件，不因事件不重复发布而清除。显示桥超过2秒无快照时，面板将全部内容标为缓存。台架看控制开度时必须同时看新鲜度；旧正开度不能说明当前仍在请求驱动。

根`start_full_fsd.sh`已默认接入此布局，原`--rviz`/`--no-rviz`选项不变。`--sensor-only`将未启用的下游输入标为未启用。单独只打开显示（不启动硬件）仍使用：

```bash
FSD_SETUP=/home/wuta/WUTA2026_FINAL/WUTA-FSD/ros2_ws/install/setup.bash bash start.sh mode:=bridge
```

状态快照消息契约见[主仓库接口文档](https://github.com/707GLobal/WUTA2026_FINAL/blob/main/docs/ROS_INTERFACE.md)。显示桥不发布GO、控制、任务或TF。需要再次构建时运行`bash build.sh`；不会自动修改算法或增加新的权威生产者。

## E22无线0.6.0（2026-10-20）

默认UART115200、空速配置62500 bit/s、双向UART总预算3000 B/s。与CLI当前运行选择联动，只传本次必要日志及错误上下文，停止扫描历史运行；新运行同步清理Foxglove前一轮日志会话。车辆目标4 Hz，传感器快照每2秒，实际刷新取决于链路与负载。两端及CLI必须一起更新，部署、源时间与会话重置限制见[无线说明](radio_link/README.md)。本轮按用户要求未运行验证。

无线0.6.1修复CLI重启后的Foxglove话题丢失：切换会话后重新公告全部通道。Windows接收端更新并重启一次，后续CLI运行切换无需手动重连；无线协议和话题名不变。

无线0.6.2当前策略：完整转发CLI当次运行的manager.events.jsonl，不再筛选必要事件；Foxglove日志标题改为“本次启动完整日志”。历史运行不补传，源时间、持久化确认和双向扩展保留。部署见radio_link/README.md顶部。

2026-10-20：默认RViz启用最终规划路径，无线native转发/planning/final_waypoints_viz，bridge转发/fsd_visualization/planned_path，在Foxglove的/radio/scene/path显示。路径目标间隔1秒、权重3，完整调度与抽样范围见radio_link/README.md。

2026-10-20：Foxglove新增“传感器概览”页，使用原生Indicator/Gauge/Table展示设备状态、GNSS质量、数据新鲜度和参数。更新Windows接收端并导入radio_link/foxglove_layout.json；无需扩展包或Linux修改。

传感器原生布局更新：新鲜度与GNSS质量使用背景色，快照接收年龄使用条形Gauge。只维护radio_link/foxglove_layout.json这一份当前布局；旧交付截图和未采用方案已移出交付目录。

### 2026-10-20：无线0.7.0与CLI统一接口

原生Parameters与Publish接入CLI配置编辑、检查、启动和停止，新增“CLI 控制与参数”布局页。无线仅传输请求/结果，配置写入与操作由原CLI Manager执行，保存不隐式热更新或重启。需配套更新主仓库/CLI/显示子模块并重启两端；用法见[无线说明](radio_link/README.md)。本轮未运行测试、构建、WSL或实机验证。

无线0.7.1将管理指令及其RPC确认/执行结果放入独立高优先级队列，参数批量读取保留普通数据通道。两端及CLI配套升级，完整顺序与边界见[无线说明](radio_link/README.md#071指令与数据独立优先级)。
