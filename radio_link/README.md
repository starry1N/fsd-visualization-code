# FSD E22 低带宽双向遥测

工控机 Linux 从现有 ROS 2 读取显示数据，经 E22 透传无线链路发送；Windows 从 COM3 接收，在本机启动 Foxglove 官方 SDK 服务。Foxglove 连接 `ws://127.0.0.1:8765` 即可显示，无需在 Windows 安装 ROS。两端均可收发独立 JSON 扩展消息。

无线模块按用户确认的**透传、115200 bps 8N1、空速 62.5 kbps**使用。程序不发送模块配置命令，不修改频率、密钥、地址、工作模式或功率。Linux 串口读取 `radio.yaml:serial.linux_port`，启动参数可覆盖；使用前核对实际无线模块设备，当前默认by-id路径见部署说明。

## 当前策略：完整CLI日志（0.6.2）

按用户确认，**完整转发CLI当次运行的全部主日志事件**。CLI的radio_run.journal、radio_log_events及run.json的manager_radio_events均指向 `manager.events.jsonl`；不再使用必要信息子集筛选无线内容。正常info、检查/启动进度、普通构建输出、GNSS状态与多行错误堆栈均按源时间及原文传输。下文0.5.x/0.6.0的“只保留必要信息”说明仅为历史记录。

仍只跟随当前运行，不补传历史目录；组件独立日志文件中未进入CLI主日志的文字不另行扫描。源时间、压缩分块、PC落盘确认和断点续传保持，PC日志面板标题为“本次启动完整日志”。本机全文保存在cli.pc_storage，界面保留最近2000条/4 MiB；此显示缓存上限不丢弃传输或磁盘正文。新运行切换并清除旧显示，沿用0.6.1通道重公告修复。

Linux拉取主仓库并同步全部子模块后，重启CLI与无线发送进程，建立选中完整journal的新运行。发送端兼容旧必要journal名，但只有新CLI发布的完整journal才是全文源。Windows同步新版布局。UART115200、空速62500 bit/s及双向总预算3000 B/s不变；本轮未运行验证。

## 62.5 kbps与当次启动日志（0.6.0，历史策略）

默认串口115200 8N1、模块空速62.5 kbps。软件双向UART总预算从160提高到**3000字节/秒**，包含编码帧、确认、重试和上行扩展；这是调度预算，不是已实测的无线有效吞吐。校验按串口和配置空速计算上限，给模块开销留余量；不将62500 bit/s直接当作可用业务字节数。

收发换向等待降为每端10 ms，响应超时1.2秒，重组超时30秒；发送端50 ms调度，空闲预算最多保留一帧，避免长期空闲后无节拍突发。数据片仍为144字节（最大226 UART字节，模块保持240字节分包）。几何、源时间及双向扩展编码保持兼容。

| 内容 | 目标发送间隔 |
| --- | --- |
| 车辆位置/速度 | 0.25秒（4 Hz） |
| 纯跟踪控制目标 | 0.5秒 |
| 融合目标 | 1秒 |
| 引导簇、CLI传感器/车辆摘要 | 2秒 |
| 规划路径 | 3秒 |
| 地图、中心线、已行驶轨迹 | 5秒 |

图层关闭时不发送；大地图、日志突发或重试时，实际刷新间隔会延长。PC源时间排序缓冲降为0.5秒，不改变源纳秒时间。必要日志分块上限改为2048字节、源选择检查0.2秒、磁盘确认重试3秒；日志有待发时仍优先占每三次调度中的前两次，没有日志时图层使用余量。

### 只跟随CLI当次启动

现有日志所有者在`.runtime/fsd_manager/radio_run.json`原子发布当前运行选择：`scope`（运行UUID）、`journal`（本次必要journal绝对路径）、`label`、`t`（源端Unix纳秒）、`active`。CLI打开时选择空会话，每次按`s`新建运行时选中该运行；直接全栈收集入口复用同一RunLog选择机制。仿真运行不替换实车选择。

无线端**不扫描或补传历史目录**，只读这一指针；开始新运行即撤下旧运行待发块，保留其Linux原文件和旧断点。当前运行结束后保持本次选择，pending归档时更新路径，可把本次剩余关键日志传完；下一次启动立即切换。无需删除历史文件或清空断点。普通GNSS状态变化不重复写入无线日志，最新状态继续由传感器快照传输；错误、警告、任务变化及启动/退出汇总保留原文和源时间。

新增J/Z JSON控制消息`k=cli_run`，含`scope/stream/t/label/active`，优先于日志块发送并周期重发；源journal首次出现后补充stream ID。PC拒收非当前stream的旧块，旧运行选择被退役，不会被迟到消息重新打开。L/b日志与A落盘确认格式不变。

PC启动时不遍历历史journal重播；收到选择后只恢复/重播本次已落盘日志，UI缓存仍为最多2000条/4 MiB，全文在本机文件。新scope调用官方SDK的`clear_session`清空Foxglove上一轮日志，同时恢复当前图形缓存，因此速度曲线等时间轴也从新会话开始。CLI终端日志列表同步清空上一轮，图形日志窗口在运行结束后仍可查看本次manager.log。

Linux CLI、无线发送端、Windows接收端须配套更新到本版：先更新Windows源码，再在Linux主仓库`git pull --ff-only`、`git submodule update --init --recursive`，执行`radio_link/setup_linux.sh`并按原模式重启CLI和无线进程。旧CLI不会发布选择文件，旧接收端也不理解新运行控制消息；不能只改YAML。模块空速需通过模块自身配置，两端一致。

### 0.5.x历史行为

以下目录扫描说明记录0.5.x行为；0.6.0已改为上面的当次运行指针，不再扫描历史日志。

0.5.1适配新的运行日志布局：自动扫描`wuta_logs/<赛项或sessions>/<运行目录>/manager.radio.events.jsonl`，兼容旧`logs/`，也读取CLI状态中的`radio_log_events`和`log_dir`。直接全栈入口即使没有CLI状态文件，标准运行目录仍可被发现。当前进程已经发现的自定义目录会继续追踪；若无线进程重启后，自定义目录既不在标准根目录、也不再被状态文件引用，需要恢复其状态路径才能再次发现。

CLI结束运行并切回会话、pending目录归档移动后，按journal内部stream ID重新定位源文件；磁盘确认早于周期扫描到达时会立即重扫，沿用原偏移和断点。只扫描必要journal，不自动转发组件日志或完整事件文件。无法读取的单个journal保留并显示警告，其余有效源继续发送；源文件被删除或损坏仍需人工恢复。

按当前CLI的实际输出筛选无线日志，Linux的`manager.log`和`manager.events.jsonl`继续保留全文。原日志写入者在旁边生成独立的`manager.radio.events.jsonl`作为无线源，单独stream ID与断点；不读取界面刷新，也不转发所有`modules/*.log`全文。0.4.0的未筛选journal不再自动排队，也不删除其本地历史；新策略从CLI重启后的事件生效，不回填历史或猜测源时间。

| 无线保留 | 只留Linux本地 |
| --- | --- |
| 错误、警告与完整多行正文；后续连续异常堆栈、命令和失败日志尾部 | 菜单/快捷键、窗口打开、正常权限提示、日志路径提示 |
| 任务监控的阶段、模式选择、圈数、GO、急停、门控与设备自检变化 | 逐项预检查成功、模块启动和话题等待进度 |
| 预检查汇总、启动提交、全栈启动完成、运行退出码和进程清理结果 | 普通构建进度和其余未匹配的info/title输出 |

规则在CLI `log_context.py:necessary_radio_event`统一实施，交互与独立全栈入口共用。故障上下文保留到下一条带明确启动器标签的正常阶段、界面提示或运行汇总；模块日志的ROS `[INFO]`不会截断故障尾部。保留事件的文字、等级、源时间不缩写、不截断，不合并重复故障。未知info默认不传；若以后新增关键事件，应同步扩展此规则与回归。传感器状态仍通过独立快照通道发送。

Manager在记录事件时保存Unix纳秒源时间、等级及完整文字。直接全栈入口的现有`tools/run_logged.py`收集器也记录完整原文，时间为源端收集事件的时刻，正文自带的子进程日志时间保留。无线源端读取事件文件新增字节；接收端按stream ID与字节偏移写入磁盘，`flush/fsync`成功后才发业务确认。Linux仅在收到该确认后提交断点；分片ACK不代替磁盘确认。应用确认丢失、串口重连或双方程序重启后可从断点重传，PC校验已存在字节并去除传输重复；归档移动目录保持stream ID。未确认内容保留在Linux原journal，不能删除这些源文件或清掉断点来“清队列”。

筛选后的日志量持续超过无线吞吐仍会积压，不能保证零延迟。Linux `/fsd_radio/status.cli`和PC `/radio/cli/transfer.backlog_bytes`显示必要journal的待确认字节数（含事件元数据），以及源文件、状态采集错误。PC收到的必要journal与其文字分别存为`cli.pc_storage/<stream>.events.jsonl`和`<stream>.manager.log`，默认`~/.fsd_radio/cli_received`；CLI全部历史以Linux原文件为准。磁盘空间不足、源文件被外部删除等情况不能靠无线恢复；故障会暴露，不把失败记为成功。单条事件超过16 MiB时仍完整落盘，Foxglove投影暂停并报警；UI缓存最多2000条/4 MiB。

CLI传感器状态复用原`dashboard.py`采集：**GNSS、惯导、雷达、相机、融合**，包含采样时的在线/超时、频率、最后接收年龄及原详情（例如GNSS状态、惯导位置速度、点数、图像分辨率和融合锥桶数）。不增加图像/点云无线传输。状态快照使用CLI生成时间，各项保留原Header时间；无Header或零Header标记未知，另保留源端接收时间，不冒充采集时间。CLI采集缓存超过3秒未更新会标为过期；PC `/radio/cli/health`还标记无线接收年龄，不能把旧的“在线”当作当前在线。状态按最新值发送，不像日志那样逐次排队。

Foxglove布局新增“CLI 日志与传感器”页：

| 通道 | 内容 |
| --- | --- |
| `/radio/cli/log` | 官方`foxglove.Log`，必要事件的源时间、等级、完整文字 |
| `/radio/cli/sensors` | 五项传感器原详情、源时间和采样时的状态 |
| `/radio/cli/vehicle` | CLI已采集的任务/自检/模式/GO/急停/控制请求摘要，不推断未发布的赛项内部状态 |
| `/radio/cli/transfer` | 日志待确认字节数、采集器错误 |
| `/radio/cli/health` | 本机接收年龄和采集缓存是否过期 |

几何与速度继续沿用schema 3。CLI日志不受几何迟到丢弃策略影响；晚到的完整事件保留源时间并照常落盘、发布。较晚订阅只重播有界UI缓存，源时间不变。

### 0.5.x历史下行优先与部署

双向总限速仍为**160 UART字节/秒**，没有为上行预留一半带宽；PC仍仅响应Linux轮询，空确认34字节，偶尔携带PC命令或持久化确认。默认数据片增至144字节，最大UART帧226字节，必须保持E22模块240字节分包；满载数据帧搭配空确认时，约87%的双向帧字节属于Linux下行，业务确认/重试会降低此比例。这是串口帧占比，不是空口有效吞吐保证。

日志有待发块时至少优先选取每三次调度中的前两次；没有日志则让其他数据使用带宽，不固定预留闲置上行槽。状态快照目标间隔10秒，车辆2秒、控制目标4秒；融合8秒、引导簇15秒、地图/中心线/历史轨迹30秒、规划路径20秒。它们是调度目标，日志突发时实际间隔会延长；双向ACK、PC指令、重试均计入160字节/秒。

在Linux主项目根同步主仓库锁定的配套版本，再执行无线安装：

```bash
git pull --ff-only
git submodule update --init --recursive
bash fsd-visualization-code/radio_link/setup_linux.sh
```

CLI需按现场原流程重新打开以加载事件journal和结构化传感器状态；旧进程不会自动加载源码。无线发送程序同样需重启，保持原先native/bridge模式与已核对串口。独立使用CLI/显示仓库时也要取得两个配套更新，不能只升级无线组件。Windows导入[foxglove_layout.json](foxglove_layout.json)，打开“CLI 日志与传感器”。本轮不连接或部署Linux工控机。

CLI传输二进制：`L + >B16sQQH + data`表示版本1、16字节stream ID、字节偏移、源端块封装时间、原始字节数（1～2048）；较短时使用`b + zlib(完整L消息)`。每条事件自身的Unix纳秒时间在原始journal中完整保留，块封装时间不替代它。磁盘确认为`A + >B16sQ`（版本、stream、已持久化连续偏移），属于链路业务确认而不是传感器采集事件。传感器快照使用J/Z JSON的`k=cli_status,v=1`，字段`s`为传感器编号、接收标记、年龄0.1秒、频率0.1Hz、源时间、源端接收时间、完整详情；PC恢复名称与字段标签。消息上限和有限解压仍生效。

## 源时间对齐契约（自0.3.0起，几何schema 3）

车辆定位、速度和每个保留的Marker均携带各自的完整ROS源时间，精确到纳秒。速度保留`TwistStamped.header.stamp`，不再借用定位时间；同一Marker生成的多个显示对象共享该Marker源时间，Marker本身没有逐点时间时不编造逐点时间。快照保留图层的最新源时间，空快照使用最近一次有效Marker/删除事件的时间。

PC通过官方SDK的`log_time`将源时间写入Foxglove时间轴；场景对象的`timestamp`也使用对应对象源时间。当前配置本地缓冲0.5秒（旧配置3秒），在已缓存的样本中按源时间排序，同一通道超出缓冲后到达的旧样本不回写，计入`/radio/layers.timing.late_drops`。这是源时间排序和采样保持，不插值、不要求所有图层同时到达，也不能恢复无线链路未传输的中间帧。缓冲上限512条，满时丢最早入队条目并计数；可调`foxglove.alignment_buffer_seconds`（0～60秒），实际显示延迟还包括无线排队与重组。

速度仪表和曲线使用 **`/radio/speed.speed_m_s`**，该通道以速度自己的源时间发布。`/radio/state`以定位源时间发布，仅当速度与定位源时间相差不超过`foxglove.speed_alignment_tolerance_seconds`（默认0.2秒）时附带速度，否则为`null`；可查看`speed_source_stamp_ns`和`speed_pose_skew_s`。`source_stamp_ns`为十进制字符串，`timestamp.sec/nsec`为整数，避免JavaScript将大整数纳秒值舍入；旧的数值`source_ros_stamp_ns`仅用于兼容。

固定样式不走无线；位置、尺寸、颜色、双向ACK/轮询和调度预算沿用精简方案。`/radio/link`和`/radio/layers`是本机生成的诊断，不占无线业务带宽；图层`stale`随本机监控刷新，业务通道不每秒制造新源样本。较晚订阅的Foxglove仅重播最后已发布样本，保留原源时间。源时钟回退时Linux改变32位时钟epoch；PC清空缓冲与历史，并拒绝已退役epoch的在途旧包。

**升级：先更新并重启Windows接收端，再在Linux拉取并重启发送端。** 0.3.0接收端兼容schema 2及旧J/Z几何；旧版本接收端不能读取schema 3。旧发送端没有独立速度/对象源时间，接收端不能补出，因此旧数据不会生成伪造时间的`/radio/speed`样本。以`/radio/layers`各层`schema=3`确认升级生效，再导入本目录[foxglove_layout.json](foxglove_layout.json)。本机交付副本见`D:\ebyte\output\FSD_E22_源时间对齐.json`。

Linux拉取主项目时，在主项目根执行：

```bash
git pull --ff-only
git submodule update --init fsd-visualization-code
# 已有无线进程需先正常停止，勿同时运行两个串口读者。
bash fsd-visualization-code/radio_link/setup_linux.sh
bash fsd-visualization-code/radio_link/start_linux.sh --mode native
# bridge现场保持原来的--mode bridge；串口沿用已核对的radio.yaml或--port。
```

源时间为零、上游未填写Header、无Header的空MarkerArray都不能恢复真实采集时间：相关样本不进入对齐显示，`unknown_source_times`计数增加。需要上游生产者填有效Header，清空可发送带时间戳的DELETE/DELETEALL Marker。ACK/轮询为链路控制帧，没有传感器采集时间。

扩展JSON外层新增`t`（纳秒）、`clock_domain`和`time_origin`，两端接收出口保留为`source_stamp_ns`、`clock_domain`、`time_origin`。生产者可在JSON正文提供`source_stamp_ns`（整数或十进制字符串）和`clock_domain`（`ros`或`unix`）；缺省使用Linux ROS入队时间或PC Unix创建时间，并明确标记`time_origin=enqueue`，它不是上游传感器源时间。源时钟必须属于同一时间基准，不能将仿真ROS时间与PC Unix时间直接对齐，也不会根据无线到达时间估算两机钟差。

## 必要几何与本地样式

当前发送端使用必要数据协议schema 3：固定形状、颜色映射、线宽、锥桶尺寸、标签和显示ID在本机生成。`radio.yaml`中的`foxglove.styles`仅影响本机样式，不走无线；路径/中心线/轨迹/控制目标颜色在本机固定。融合、地图和引导簇的来源显示色保留为单字节RGB332索引，不能把它当作新推断的真实锥桶类别。

| 数据 | 无线保留 | 本机生成 |
| --- | --- | --- |
| 车辆 | 位置、四元数、速度、时间和源年龄 | 车体箭头、颜色和尺寸 |
| 锥桶地图与融合目标 | 中心点、来源颜色索引 | 统一直立圆柱、尺寸、显示ID |
| 路径与轨迹 | 采样点及必要的线段连接方式 | 颜色、线宽、固定坐标帧与显示ID |
| 引导簇 | 采样点；包围框的真实尺寸和非默认姿态 | 点大小、固定样式 |
| 控制目标 | 目标位置；范围几何的尺寸或箭头端点 | 颜色、箭头宽度等样式 |

不传文字、namespace、原始marker ID、固定frame字符串、重复量化步长或逐快照源端统计。空完整快照表示清除该层，其他快照原子替换该层，因此暂不需要传逐对象删除ID；没有改成增量协议，也没有提高现有调度频率。

**部署顺序见上文。** 分片ACK成功也不代表图形已解码。双端fixed_frame与quantization_m必须一致，必要帧中的16位配置指纹用于检查常见配置不匹配。更新本机样式不改变该指纹。Linux实机验收由部署后的实际状态确认，不能用离线测试替代。

历史0.3.0的96字节分片编码对照（复现用`python tools/compare_bandwidth.py --fragment-bytes 96`；当前默认144字节）。同一组合成几何的双向UART成本（含Base64、分片与每片空ACK；不含空口开销、重试与额外扩展消息）。样本含纳秒尾数、独立速度时间、非零epoch，以及各对象相差1毫秒的混合源时间：

| 场景 | schema 2 | schema 3 | 增加 |
| --- | --- | --- | --- |
| 一份车辆位姿与速度 | 104 | 116 | 12 |
| 24个锥桶 | 300 | 460 | 160 |
| 48点路径 | 172 | 180 | 8 |
| 6个包围框 | 160 | 184 | 24 |
| 空地图清除快照 | 84 | 92 | 8 |

当前配置的成本复现：`python tools/compare_bandwidth.py`，同时输出最初JSON协议的成本作为参考。对象源时间都相同和任意纳秒尾数的开销会不同，不把表格当成固定包长。总预算仍为160 UART字节/秒，因此增加时间字段后可达到的刷新率可能降低，原调度间隔是目标而非保证；在当前160字节/秒的软件限速下，不能承诺5～10Hz全图层实时刷新。主端在发送前为双向帧预留预算，扩展时间字段导致分片增多也纳入限速。

## 配置检查结果

本目录读取原有 `../src/fsd_visualization/config/topics.yaml` 和 `fsd.rviz`，复用原 `configuration.py` 渲染 native / bridge 的实际话题，保持原文件内容不变。

| 检查项 | 结果与处理 |
| --- | --- |
| 系统 | ROS 2 Humble；不是 ROS 1 rosbridge |
| 固定帧 | `map`；非 map 的几何数据在 Linux 按源时间戳查询 TF 后转换 |
| RViz Frame Rate=30 | 仅是本地重绘目标，不能据此要求无线每秒发送30帧 |
| 原始点云 | RViz 模板是 `/hesai/pandar`，YAML 是 `/rslidar_points`，启动渲染会覆盖模板；两者都不订阅或传输 |
| 相机图像 | `/camera/yolo/image_annotated`，完全排除 |
| 速度 | 原 YAML 为 `/localization/velocity`，项目当前实车接口是 `/chcnav/velocity`；只在 radio.yaml 覆盖，不改原显示配置 |
| native 模式 | 直接消费给定配置中的7个 MarkerArray 图层 |
| bridge 模式 | 复用原配置生成器映射 `/fsd_visualization/*`，必须已有显示桥在运行 |

运行配置检查，不打开串口、不启动 ROS：

```powershell
.\.venv\Scripts\python.exe -m fsd_radio.cli inspect
.\.venv\Scripts\python.exe -m fsd_radio.cli inspect --mode bridge
```

### 传输内容

| 图层 | native 输入话题 | 默认限量 |
| --- | --- | --- |
| guided | `/perception/fused/guided_clusters_viz` | 最多6个 marker，每个最多12个派生簇采样点 |
| fused | `/perception/fused/cones_viz` | 最多12个 marker，每个最多12点 |
| map | `/mapping/cone_map_viz` | 最多48个 marker，每个最多64点 |
| centerline | `/planning/centerline_viz` | 最多4个 marker，每条最多48点 |
| path | `/planning/final_waypoints_viz` | 最多4个 marker，每条最多48点 |
| trajectory | `/planning/driven_trajectory_viz` | 最多3个 marker，每条最多48点 |
| control | `/control/target_viz` | 最多4个 marker，每个最多12点 |
| vehicle | `/localization/pose` 与 `/chcnav/velocity` | 位姿、平面速度；本机绘制车体箭头 |

Grid 无需无线传输，在 Foxglove 的3D面板本地开启网格。速度从真实 TwistStamped 计算 `hypot(vx,vy)`，不保留倒车符号，和原显示桥含义一致。

原生/桥接话题必须确实有生产者。无线程序不启动算法、不启动 RViz，也不凭空生成缺失锥桶、引导簇或控制目标。根 `start_full_fsd.sh` 默认用 bridge 显示；如果要对齐该运行视图，两端启动均加 `--mode bridge`。bridge 模式的原始自定义消息转换仍由原显示桥负责，本程序只订阅其 MarkerArray，不另外复制建图或规划。

项目文档提示某些部署不发布原生 fused/guided 显示话题；这时 native 图层不会有数据，不能靠无线配置修复。特别是现有显示桥的地图输入类型需与实际 `/mapping/cone_map` 核对：接口文档为 `ConeMap`，旧显示桥按 `ConeArray` 订阅；遇到该差异时使用原生 `/mapping/cone_map_viz`，或先修正原显示桥，不能把无数据误判成无线丢包。

## 带宽与显示边界

62.5 kbps 空速的理想原始上限约为7812.5字节/秒，LoRa 分包、收发切换和干扰仍会消耗空口时间。串口波特率与空中速率是不同参数；两端程序读取同一份radio.yaml使用115200 8N1，两端模块需预先配置为62.5 kbps空速。默认把**双向合计串口字节预算设为160字节/秒**，计入帧头、Base64、CRC、ACK、轮询和重试，并保留空口开销余量。本次只校正串口/空速要求，保留既有限速、超时及分片参数；它是主机发送节拍限制，不代表已经测得无线有效吞吐，也不会自动利用全部62.5 kbps带宽。

Linux 主轮询，PC 只在收到有效轮询后回复，从协议上避免两个应用各自全速发送。一次串口应用帧最多226字节，144字节数据片，默认适用于模块240字节分包；**请核对模块分包长度为240**。这是配置前提，程序不自动改写。其他分包长度应调小 fragment_bytes 并重新实测，首版不承诺其定点/分包行为。

在全部图层开启时，地图/路径的完整低带宽快照可能需要数秒至数十秒，大场景或多次重试时可能超过一分钟。radio.yaml 的 interval 是发送资格的最小间隔，不是保证更新频率。车辆位姿权重较高，但同样受链路占用影响。大幅减少 markers / points 或关闭非必要图层，才能提高其它图层更新速度；禁用输入可在个人 topics.yaml 中置空相应键，再把 radio.yaml 指向它。

几何坐标默认5厘米量化，使用整数变长编码和点间差分。点集按均匀索引抽样，LINE_STRIP保留首尾，LINE_LIST按完整线段抽样，派生引导簇显示为少量采样球点。各图层编码快照最多1200字节，未压缩二进制最多4096字节；超限继续抽稀或舍弃部分对象，源端统计仅保存在Linux `/fsd_radio/status.sampling`，不重复消耗无线。Foxglove `/radio/layers`显示本机解码对象数、接收年龄和过期状态，不具有源端逐快照抽样计数。因此本程序提供**可辨识的低带宽预览，不是RViz原消息的无损复制**。

当前输入接受ARROW、CUBE、SPHERE、CYLINDER、LINE_STRIP、LINE_LIST、CUBE_LIST、SPHERE_LIST和POINTS；按上表转成必要几何，不保留逐对象原样式或透明度。TEXT_VIEW_FACING标签直接省略；不传MESH_RESOURCE、TRIANGLE_LIST或外部模型资源。车辆四元数量化为4个int16；包围框/范围几何保留非默认姿态和5厘米量化尺寸。缺TF、空frame或非法几何数据会丢弃该对象并在Linux计数，不把传感器坐标冒充map。

## Windows 安装和运行

使用64位 Python 3.10及以上。保留整个 `fsd-visualization-code` 目录，不能只复制 fsd_radio 包，因为需要复用原有配置和生成器。

```powershell
cd D:\ebyte\WUTA2026_FINAL\fsd-visualization-code\radio_link
.\setup_windows.ps1
.\start_windows.ps1                      # COM3，native
```

若本机脚本执行策略阻止运行，可在当前会话调用 `powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1`，或直接执行等价命令：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[pc]"
.\.venv\Scripts\python.exe -m fsd_radio.cli pc --port COM3
```

对齐已经运行的 bridge 视图：

```powershell
.\start_windows.ps1 -Port COM3 -Mode bridge
```

如果只需先检查 Foxglove，不插无线模块：

```powershell
.\.venv\Scripts\python.exe -m fsd_radio.cli demo
```

demo 只生成明确标记的本机测试图形，不打开COM3、不连接ROS、不发无线数据。正常运行不用 demo。Ctrl+C 退出当前程序，不停止既有 FSD 节点。

### Foxglove 面板设置

1. 打开已安装的 Foxglove，选择 Open connection / Foxglove WebSocket，地址 `ws://127.0.0.1:8765`。
2. 添加3D面板，固定显示帧设为 `map`，勾选 `/radio/scene/*`，按需本地开启网格。所有图形已在Linux转到固定帧，本机不需要额外ROS TF桥。
3. 添加 Plot 或 Gauge，用 `/radio/speed.speed_m_s` 看速度；`null` 表示不可用，不是0车速。
4. 添加 Raw Messages，查看 `/radio/link`、`/radio/layers`、`/radio/extension/rx`，检查连接、新鲜度、抽样和双向应答。
5. 若 fixed_frame 在原 YAML 改为 odom/rslidar，3D面板也使用该帧。

SDK 锁定 `foxglove-sdk==0.29.2`。当前 SDK 握手使用 `foxglove.sdk.v1`，本项目的真实 WebSocket 测试已验证；若现有旧版 Foxglove 只支持历史协议而报握手失败，应更新 Foxglove。程序不重新安装或改动现有 Foxglove。

## Linux 工控机安装和运行

复制整个 `fsd-visualization-code` 到工控机。使用 ROS 2 Humble 对应的系统 Python 3.10，不用与ROS不匹配的 Conda/Python解释器。

```bash
cd /path/to/fsd-visualization-code/radio_link
# 使用系统 Python；缺少 ensurepip 时可复用已安装的系统 pip。
bash setup_linux.sh

source /opt/ros/humble/setup.bash
# 按现有项目实际路径 source FSD 的 install/setup.bash；不在这里启动全车。
source /path/to/WUTA-FSD/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=42   # 改成真实算法正在使用的domain

ls -l /dev/serial/by-id/
bash start_linux.sh
```

上面的 `/path/to`、domain 和串口均须按实机填写。**不要直接猜 `/dev/ttyUSB0`：项目惯导可能正在使用它。** 推荐使用 by-id 的稳定名称。用户需有对应串口权限；本程序不修改权限、不占用已有惯导串口、不关闭其他进程。两端无线模块必须同频段、匹配信道/NETID/空速/密钥，绿色传输模式、RSSI尾字节追加关闭、模块分包240；这些条件通过厂商工具或此前配置确认。

Linux 启动脚本复用 `../scripts/environment.sh` 加载 ROS/FSD 环境；缺少 `.venv` 时会提示先运行 `bash setup_linux.sh`。

本机已确认的 E22 默认路径在 `radio.yaml` 的 `serial.linux_port` 中配置为 `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`。不传串口参数时读取该配置；也可传入实际串口路径覆盖。该 CH340 没有唯一序列号，单个同型号适配器时路径通常不随 USB 插口或插入顺序变化，多个同型号设备可能重名，需重新核对。

根入口当前使用 bridge，已有显示桥时：

```bash
bash start_linux.sh --mode bridge
```

如果没有运行显示桥但希望看原生 marker，使用默认 native，并确认算法启用了对应 `_viz` 输出。订阅 QoS 为 Best Effort / Volatile / depth1；不会自动接收订阅前仅发布一次的历史 marker。不存在输入时不生成替代图形。

ROS bag 测试可添加 `--use-sim-time`，由现有bag发布 `/clock`、TF和数据；无线调度仍使用单调墙钟。没有相机或原始点云订阅。该包不依赖 `wuta_msgs` / `autoware_msgs` 解码，因为传输输入是标准 MarkerArray/PoseStamped/TwistStamped。

只读检查当前输入：

```bash
ros2 topic info /planning/final_waypoints_viz -v
ros2 topic info /chcnav/velocity -v
ros2 run tf2_ros tf2_echo map base_link
ros2 topic echo /fsd_radio/status --once
```

## 双向扩展消息

链路扩展消息是最多约500字节的JSON对象（整个内部封装最多512字节）。建议提供唯一业务id，实现业务层幂等。队列最多8条；满时拒绝，不无限积压。扩展不会转发到 `/control/command`、任务模式、GO或急停接口；Linux只发布独立 `/fsd_radio/extension/rx`，后续新功能通过显式消费者接入。

### 本机发往工控机

Windows程序运行后，在另一个终端发送Ping：

```powershell
cd D:\ebyte\WUTA2026_FINAL\fsd-visualization-code\radio_link
.\.venv\Scripts\python.exe -m fsd_radio.cli send
.\.venv\Scripts\python.exe -m fsd_radio.cli send --message '{"type":"note","id":"test-001","text":"hello Linux"}'
```

命令返回 `queued: true` 仅表示本机排队成功，不表示远端收到或执行。轮询到来后才上行；Linux停止时PC不自发占用无线。Linux接收的所有扩展发布为 `std_msgs/msg/String` 的JSON：

```bash
ros2 topic echo /fsd_radio/extension/rx
```

Ping 会由 Linux 返回同id的 `pong`，本机控制台和Foxglove `/radio/extension/rx` 可见。图层多或链路重试时Ping可能需要较长时间，不适合作为低延迟控制通道。

也可在 Foxglove 的 Publish 面板，topic 设为 `/fsd_radio/extension/tx`、编码 JSON，发布 `{"type":"ping","id":"foxglove-001"}`。SDK只接受这个明确话题，其他客户端发布话题不会被传到ROS。

### 工控机发往本机

```bash
ros2 topic pub --once /fsd_radio/extension/tx std_msgs/msg/String \
  '{data: "{\"type\":\"note\",\"id\":\"linux-001\",\"text\":\"hello PC\"}"}'
```

本机从 `/radio/extension/rx` 接收。任意本机程序还可以连接 `127.0.0.1:8766`，发送一行JSON加LF，读取一行排队结果；默认只监听回环。该本机接口和Foxglove客户端发布共用同一个有界上行队列。

```python
import json, socket
with socket.create_connection(("127.0.0.1", 8766), timeout=3) as s:
    s.sendall(json.dumps({"type":"ping", "id":"python-001"}).encode()+b"\n")
    print(s.makefile("rb").readline().decode())
```

无线帧ACK仅证明对端链路收到了该分片，不证明业务已处理。串口重连、对端重启或序号会话切换时，待发送扩展清空并记录“投递未知”，不会悄悄重放旧扩展。内存去重也不提供跨进程重启的业务恰好一次语义。

## 时间 截断与断线行为

Linux保持有界 Marker `(namespace,id)` 缓存，处理ADD/DELETE/DELETEALL和ROS时间寿命；ROS时钟回退会清空历史。包括frame_locked marker在内均按源时间戳查TF，冻结为该源时刻的无线快照，不用最新车辆位置替代历史检测。

无线传输的是低频场景快照：全部分片到齐、CRC正确才原子更新该图层，半幅地图不先行覆盖。未完成重组最多12组、120秒超时，消息与片数也有硬上限。实时观测在本机保留最多约10秒，便于低速链路观看，**这与RViz原生1秒marker寿命不同**。地图/规划/轨迹保留到下一快照或清除；保留不代表数据新鲜。

本机业务消息的SDK时间与场景对象时间使用上文所述源时间，不使用接收墙钟冒充采集时间。`receive_age_s`、`source_age_lower_bound_s`、`stale`在`/radio/layers`独立暴露；源年龄保留0.1秒精度，其下限不包含完整无线排队/传输延迟。源时间保真不等于已经测得端到端延迟，源端自身的时间正确性仍由生产者负责。

输入中断后，图层监控的源年龄增长；业务样本不会通过定时重发变成新数据。动态实体的源时间寿命为10秒，地图/规划/轨迹保持到替换或清除；静止的源时间轴不代替断线检测，应查看`/radio/layers`与`/radio/link`。对端会话改变或新的源时钟epoch会清空本机历史。较晚订阅时的缓存重播同样保留原时间，不能把本机重播计作无线更新。

## 协议和实现索引

```text
fsd_radio/configuration.py   复用原生显示配置解析
fsd_radio/ros_sender.py      Linux ROS订阅、采样TF、扩展ROS出口
fsd_radio/geometry.py        marker缓存、量化、抽样和限量
fsd_radio/protocol.py        Base64帧、CRC32、压缩、分包重组
fsd_radio/essentials.py      必要几何二进制编解码、配置指纹和本地样式展开
fsd_radio/link.py            主轮询/从应答、ACK重试、限速和有界队列
fsd_radio/transport.py       pyserial独占读写路径、短写处理
fsd_radio/worker.py          串口重连与会话恢复
fsd_radio/foxglove_view.py   官方SDK SceneUpdate与Publish面板接入
fsd_radio/pc_receiver.py     PC接收与回环扩展接口
fsd_radio/cli.py             linux / pc / inspect / send / demo
fsd_radio/cli_telemetry.py   CLI文件增量、磁盘确认、续传和传感器快照
```

无线帧 `~ + Base64(binary) + LF`；原始特殊模块命令前缀不出现在Base64业务正文中。二进制头为大端 `>BBIHIHHBB`：版本、角色、会话u32、序号u16、被确认会话u32、被确认序号u16、消息id u16、片序u8、总片数u8；随后最多144字节数据与大端CRC32。CRC覆盖头和数据。CRC32使用标准ISO-HDLC，`123456789`的结果为CBF43926。

扩展消息仍是`J + JSON`或`Z + zlib(JSON)`。必要几何使用`S`场景、`V`车辆，压缩更短时使用`z + zlib(完整S或V消息)`；新接收端也接受旧几何J/Z消息。

S/V公共头在前缀后使用大端`>BBQHHI`：schema=3、图层编号、uint64来源纳秒时间戳（限定非负int64）、uint16源年龄（0.1秒）、uint16配置指纹、uint32源时钟epoch。旧schema 2的`>BB6sHH`毫秒头仅为兼容读取。图层编号依次为vehicle、fused、control、guided、map、centerline、path、trajectory。场景随后为对象数与必要几何记录；车辆为3个ZigZag变长坐标、4个int16四元数和uint16厘米/秒速度（FFFF不可用），最后追加速度源时间相对定位源时间的带符号差分。

每个schema 3几何记录先写对象源时间相对快照时间的差分，再写类型字节、flags字节、变长点数、逐点XYZ差分ZigZag变长整数；仅在需要时附加单色或逐点RGB332字节、3个变长尺寸、4个int16姿态。时间差分先将int64纳秒差编码为ZigZag，外加单位标记：差值可整除1,000,000时用毫秒值、标记0，否则用纳秒值、标记1；将`(ZigZag值<<1)|标记`写为最多10字节的变长整数，解码后恢复精确纳秒。flags位0表示尺寸、位1表示姿态、位2表示单色、位3表示逐点色，位2与位3互斥。类型0至7依次为锥桶中心、折线、线段、点集、盒体、椭球、圆柱和双端点箭头。解析器拒绝畸形长度、超限解压和非法几何，不靠USB回调划分消息。

ACK同时用于轮询/应答，没有数据时count=0。每向一个未确认分片最多补发2次；序号与会话去重，图层采用最新值快照，扩展采用FIFO。链路仅支持一对主从无线模块，不是多节点MAC协议。它与前一份开发文档中建议的短消息帧不同，属于针对可视化数据实现的独立协议，两端应部署本目录同一版本。

## 验证与实机接入

Windows离线回归：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[pc,test]"
.\.venv\Scripts\python.exe -m pytest -q
```

包含逐字节/粘包、坏CRC、解压上限、消息重组、重复包、丢ACK双向收发、联合限速、TF与量化、marker删除和过期、真实pyserial TCP串口、官方SDK实际WebSocket订阅与客户端发布。TCP只是测试替身，不是实际无线配置；功能加速测试使用更快虚拟时钟预算，限速用例单独按160字节/秒测试。

`tests/test_ros_smoke.py`在有ROS 2的Linux环境额外启动隔离domain 191、本机TCP串口替身及合成Marker/pose/velocity，验证真实ROS订阅和扩展Ping/Pong；没有rclpy时明确跳过。该测试不打开实物串口、不启动FSD算法、不发布驾驶话题。工控机加载ROS后可安装测试依赖并运行该单项：`python -m pytest -q tests/test_ros_smoke.py`。

实机按顺序验收：

1. 核对双端模块通信条件、COM3与Linux无线串口身份，关闭占用相同端口的串口调试工具。
2. Windows先启动接收端，Linux明确指定无线串口启动；检查 `/radio/link` 的 peer_age 与收发字节。
3. 从本机执行 `send`，确认Linux收到相同id的ping、本机收到pong，再开场景图层。
4. 先只看车辆和路径，记录数据年龄、重试和失败；再逐个加入地图、融合和引导簇。
5. 拔插任一无线模块、断开对端、停止某输入，验证连接和stale状态、动态层清除、扩展不会自动回放。
6. 连续记录实际首发成功率、重试后完整快照率、更新周期与双向Ping往返时间，再调整分包和速率预算。

schema 3在Windows完成离线、官方SDK实际WebSocket与虚拟串口测试；本机没有ROS 2环境，此版本没有进行双端无线联调。2026年10月17日曾直接检查旧接收程序COM3，确认有有效无线帧、位姿/速度和轨迹；该结果不能替代新版本验收。Linux ROS订阅、真实TF、空口时序与现有Foxglove界面仍需按版本验证，历史测试使用当时的链路配置，不能作为115200/62.5 kbps配置的实物验收证据。

参考：[Foxglove SDK WebSocket](https://docs.foxglove.dev/docs/sdk/websocket-server)、[Foxglove消息类型](https://docs.foxglove.dev/docs/sdk/schemas)。模块行为依据上级工作目录所附E22 USB手册；本程序只使用已配置好的数据透传通道。

## 本机部署检查

本机串口映射、USB拓扑、资源观察及验证限制见[设备共存检查](../docs/DEVICE_COEXISTENCE.md)。`connected=true`不证明空口连通；检查`rx_bytes`、`peer_session`、`peer_age_s`与输入图层，并完成带ID的Ping/Pong。Linux安装脚本兼容缺少ensurepip但已有系统pip的环境；构建依赖要求setuptools>=64，测试使用pytest<9以兼容Humble测试插件。

## 0.6.1：CLI重启后继续显示

修复0.6.0在CLI切换运行时仅调用SDK clear_session导致Foxglove清空话题、无法继续订阅的问题。现在会话重置后关闭旧通道并重新创建全部场景、JSON和日志通道，重新公告话题；缓存重放绑定新通道，保留源时间。话题名与布局不变，不增加无线数据。升级Windows接收端并重启一次生效，此后CLI重启无需手动关闭连接再打开。仅人工阅读修改，按用户此前要求未运行自动化验证。

## 最终规划路径与当前调度分配（2026-10-20）

RViz模板的Final planned path已启用；无线继续复用模板开关与topics.yaml。native源为/planning/final_waypoints_viz；bridge源为/fsd_visualization/planned_path（由显示桥读取/planning/final_waypoints生成），PC通道为/radio/scene/path，既有布局已打开该通道。Linux重新部署配置并重启RViz/无线后生效；Windows沿用现有布局。只有源端发布路径且源时间/TF有效时才会有实际几何。

| 数据 | 目标间隔 | 权重 |
| --- | --- | --- |
| 车辆位置/速度 | 0.25秒 | 4 |
| 最终规划路径 | 1秒 | 3 |
| 控制点 | 0.5秒 | 2 |
| 融合锥桶 | 1秒 | 2 |
| 传感器/车辆/任务快照 | 2秒 | 2 |
| 引导簇 | 2秒 | 1 |
| 地图/中心线/已行驶轨迹 | 5秒 | 1 |

路径最多4个Marker、每Marker最多48个点，沿用0.05米量化与1200字节压缩快照上限；不是原始Lane的所有waypoint逐点上传，也不传额外速度文字。原算法结果和源时间不改变，PC只恢复抽样几何。

总预算仍3000 UART B/s，含双向帧与确认/重试。日志连续增量发送，有待发时每3次分片机会中优先占2次；其余按权重×等待时间调度，运行选择及持久化ACK优先，扩展消息按原机会插入。无日志时图层可用全部余量，PC只响应Linux轮询。这里是动态调度方案，不给每层固定保留B/s或承诺每秒完整路径；多分片、日志高峰和重试均会降低实际刷新。日志全文和双向扩展均保留。

## 原生传感器概览面板（2026-10-20）

无需安装Foxglove扩展。更新并重启Windows接收端，导入radio_link/foxglove_layout.json后选择“传感器概览”：顶部使用原生Indicator显示五类设备状态；中部使用背景色状态块显示快照有效性和GNSS定位质量，使用原生Gauge条形仪表显示接收年龄（0～20秒）；下部原生Table显示设备、状态、源端采样频率、采样时年龄、源时间与完整参数。原“CLI 日志与传感器”页也改用表格；“原始诊断”页保留原JSON数据。

接收端FoxgloveView从原有CLI传感器快照生成PC专用/radio/cli/dashboard，每秒刷新本机显示状态。states中0=未收到、1=在线、2=源端超时、3=缓存已过期；GNSS状态码独立显示，状态4固定解不等同于设备是否在线。源采集过期或超过原cli/health阈值（至少20秒）未收到快照时，状态灯显示缓存已过期；刷新提示不产生新传感器样本。表内频率来自源端CLI采集，不是无线刷新率。源纳秒时间仍在原始诊断和dashboard.source_stamp_ns保留，表内时间只转换为本机时区毫秒显示；未知源时间不补造。

原生组件只读现有接收结果，没有扩展包、npm、账户扩展安装或Linux变更，不增加无线字段、带宽及ROS订阅。新布局须配套本版PC接收端，旧接收程序没有dashboard通道。

### 当前布局与交付文件

统一使用本目录的foxglove_layout.json；不再分发旧车速布局、旧源时间布局或未采用的扩展草稿。数据新鲜度与GNSS质量均为background样式，接收年龄为bar样式；导入新版布局即可生效，无需重启接收端或占用第二个COM3串口。源码、布局与开发说明的目录约定见主项目docs/DELIVERY.md。
