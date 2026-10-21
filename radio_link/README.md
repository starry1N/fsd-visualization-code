# FSD E22 低带宽双向遥测

宿主CLI正常启动时默认以独立进程调用本目录 `start_linux.sh --mode bridge`，不会导入无线ROS节点到CLI进程。CLI的 `--no-radio` 可关闭联启，`--radio-mode native`可切换新进程输入模式；检查、状态、停止和仅构建入口不联启。已有发送端保持原配置，有桌面时新发送端运行在独立GNOME终端，输出同时追加到宿主 `.runtime/fsd_radio/linux.log`；没有桌面时使用独立后台进程。已有发送端打开日志查看窗口，不重启它。

本目录脚本仍可单独启动；Linux CLI入口持有项目 `.runtime/fsd_radio/sender.lock`，两个启动方式共用此锁。无线不进入CLI的车辆进程登记/停止清单；CLI退出后无线继续运行，独立退出无线需向其PID发送SIGINT。无线失败不会让CLI退出，也没有自动重启策略。更新后需重新打开CLI，已有无线进程保持原状态。

工控机 Linux 从现有 ROS 2 读取显示数据，经 E22 透传无线链路发送；Windows自动选择USB适配器串口接收，在本机启动 Foxglove 官方 SDK 服务。Foxglove 连接 `ws://127.0.0.1:8765` 即可显示，无需在 Windows 安装 ROS。两端均可收发独立 JSON 扩展消息。

无线模块按用户确认的**透传、115200 bps 8N1、空速 62.5 kbps**使用。程序不发送模块配置命令，不修改频率、密钥、地址、工作模式或功率。Linux 串口读取 `radio.yaml:serial.linux_port`，启动参数可覆盖；使用前核对实际无线模块设备，当前默认by-id路径见部署说明。

## 当前版本与操作入口（0.7.6）

以下为当前源码默认值。文中带明确版本号的历史记录保留原条件，不能据此把160 B/s、旧筛选日志或旧页名当作当前部署配置。

| 项目 | 当前行为 |
| --- | --- |
| 串口/空速/分包 | 115200 8N1、62500 bit/s、模块240字节分包；软件不改模块寄存器 |
| 调度预算 | 双向合计3000 UART B/s，含帧、确认及重试；不是保证有效吞吐 |
| 目标间隔 | 车辆0.25s，控制目标0.5s，最终路径/融合1s，引导簇2s，地图/中心线/轨迹5s，CLI快照2s；实际受负载影响 |
| 日志 | 当次manager.events.jsonl全文，原源时间、PC落盘确认与断点；不批量补历史/组件原文件 |
| 指令 | 独立高优先级队列；请求/分块ACK/执行回复保持优先，批量参数读取在普通队列 |
| CLI 日志与控制 | 全文日志、CLI状态及操作中、检查/启动/停止/查询、车辆任务表，表下为RSSI原始值 |
| 传感器概览 | 专用传感器状态、GNSS质量、快照年龄、dashboard.rows表格及原始传感器详情 |
| 参数控制 | Parameters草稿、保存、重新读取；Linux CLI校验保存，不自动重启或热更新 |
| RSSI | PC附加解析开启、Linux默认关闭；各软件开关须与对应硬件一致，显示0..255原始值，不假定dBm换算 |
| 布局 | 本目录foxglove_layout.json；本机副本D:\ebyte\output\FSD_E22_CLI与传感器.json |

通用升级先等待在途操作完成，再从主仓库 `git pull --ff-only`、`git submodule update --init --recursive` 配套更新CLI与无线。PC显示功能变更加载Windows接收端和布局；只换布局无需重启。当前运行源码/交付见宿主docs/DELIVERY.md，参数/操作细节见宿主docs/FOXGLOVE_PARAMETERS.md。

## 完整CLI日志（自0.6.2起，当前保留）

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

**升级：先更新并重启Windows接收端，再在Linux拉取并重启发送端。** 0.3.0接收端兼容schema 2及旧J/Z几何；旧版本接收端不能读取schema 3。旧发送端没有独立速度/对象源时间，接收端不能补出，因此旧数据不会生成伪造时间的`/radio/speed`样本。以`/radio/layers`各层`schema=3`确认升级生效，再导入本目录[foxglove_layout.json](foxglove_layout.json)。本机交付副本见`D:\ebyte\output\FSD_E22_CLI与传感器.json`。

Linux拉取主项目时，在主项目根执行：

```bash
git pull --ff-only
git submodule update --init --recursive
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

当前配置的成本复现：`python tools/compare_bandwidth.py`。前表是历史0.3.0/96字节分片的编码样本，不是当前固定包长；当前预算为双向3000 UART B/s、144字节分片，间隔只是调度目标。主端为双向帧预留预算，分片、扩展、确认与重试都影响实际刷新率。

## 配置检查结果

本目录读取原有 `../src/fsd_visualization/config/topics.yaml` 和 `fsd.rviz`，复用原 `configuration.py` 渲染 native / bridge 的实际话题，保持原文件内容不变。

| 检查项 | 结果与处理 |
| --- | --- |
| 系统 | ROS 2 Humble；不是 ROS 1 rosbridge |
| 固定帧 | `map`；非 map 的几何数据在 Linux 按源时间戳查询 TF 后转换 |
| RViz Frame Rate=30 | 仅是本地重绘目标，不能据此要求无线每秒发送30帧 |
| 原始点云 | RViz 模板是 `/hesai/pandar`，YAML 是 `/rslidar_points`，启动渲染会覆盖模板；两者都不订阅或传输 |
| 相机图像 | `/camera/yolo/image_annotated`，完全排除 |
| 速度 | 当前topics.yaml与radio.yaml均为 `/chcnav/velocity`；radio.yaml可显式覆盖 |
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

native图层依赖相应MarkerArray生产者。当前显示桥地图订阅已使用ConeMap并经map_cones转换，不能再把历史ConeArray类型不一致当作当前实现；若实机安装副本落后，仍需更新构建并沿实际话题核对。

## 带宽与显示边界

62.5 kbps空速的理想原始上限约7812.5 B/s，实际受LoRa开销、分包、收发切换与重试影响。两端串口默认115200 8N1，当前软件双向合计预算为**3000 UART B/s**，包含帧头、Base64、CRC、确认、轮询与重试。这是发送节拍预算，不代表实测有效吞吐或固定可用带宽。

Linux主轮询，PC只响应有效轮询。应用帧最多226字节、数据片144字节；当前RSSI解析要求真实模块分包240字节且一应用帧对应一无线包。serial.module_packet_bytes只记录设置，不自动核验或改写硬件；不能在附加RSSI开启时直接采用更小模块分包。

图层interval是发送资格的最小间隔，不保证实际刷新率。完整日志突发、快照大小和重试都可能延长显示延迟；指令优先发送，剩余机会用于数据。调整markers/points或关闭不需要的输入可减小快照；不要把历史160 B/s下的时延估计当作当前实测速率。

几何坐标默认5厘米量化，使用整数变长编码和点间差分。点集按均匀索引抽样，LINE_STRIP保留首尾，LINE_LIST按完整线段抽样，派生引导簇显示为少量采样球点。各图层编码快照最多1200字节，未压缩二进制最多4096字节；超限继续抽稀或舍弃部分对象，源端统计仅保存在Linux `/fsd_radio/status.sampling`，不重复消耗无线。Foxglove `/radio/layers`显示本机解码对象数、接收年龄和过期状态，不具有源端逐快照抽样计数。因此本程序提供**可辨识的低带宽预览，不是RViz原消息的无损复制**。

当前输入接受ARROW、CUBE、SPHERE、CYLINDER、LINE_STRIP、LINE_LIST、CUBE_LIST、SPHERE_LIST和POINTS；按上表转成必要几何，不保留逐对象原样式或透明度。TEXT_VIEW_FACING标签直接省略；不传MESH_RESOURCE、TRIANGLE_LIST或外部模型资源。车辆四元数量化为4个int16；包围框/范围几何保留非默认姿态和5厘米量化尺寸。缺TF、空frame或非法几何数据会丢弃该对象并在Linux计数，不把传感器坐标冒充map。

## Windows 安装和运行

使用64位 Python 3.10及以上。保留整个 `fsd-visualization-code` 目录，不能只复制 fsd_radio 包，因为需要复用原有配置和生成器。

```powershell
cd D:\ebyte\WUTA2026_FINAL\fsd-visualization-code\radio_link
.\setup_windows.ps1
.\start_windows.ps1                      # 自动选择USB串口，native
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

上面的 `/path/to`、domain和串口按现场填写；无线串口不可复用惯导设备。两端模块须同频段且匹配信道/NETID/空速/密钥，处于透传模式，分包240。RSSI按接收端分别匹配：Windows模块已开启附加RSSI，对应serial.append_rssi_pc=true；Linux默认false，只有其模块也开启时才设true。程序不自动配置模块、串口权限或其他进程。

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

无线传输的是低频场景快照：全部分片到齐、CRC正确才原子更新该图层，半幅地图不先行覆盖。未完成重组最多12组，按当前link.assembly_timeout配置30秒超时，消息与片数也有硬上限。实时观测在本机保留最多约10秒，便于低速链路观看，**这与RViz原生1秒marker寿命不同**。地图/规划/轨迹保留到下一快照或清除；保留不代表数据新鲜。

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

无需安装Foxglove扩展。导入foxglove_layout.json后，“传感器概览”上部为设备状态，中部为快照新鲜度、GNSS质量、年龄，下部为dashboard.rows表格与原始传感器详情；CLI页和原始诊断页不再重复专用传感器组件。更新PC派生字段需重启Windows接收端，仅换布局无需重启。

接收端FoxgloveView从原有CLI传感器快照生成PC专用/radio/cli/dashboard，每秒刷新本机显示状态。states中0=未收到、1=在线、2=源端超时、3=缓存已过期；GNSS状态码独立显示，状态4固定解不等同于设备是否在线。源采集过期或超过原cli/health阈值（至少20秒）未收到快照时，状态灯显示缓存已过期；刷新提示不产生新传感器样本。表内频率来自源端CLI采集，不是无线刷新率。源纳秒时间仍在原始诊断和dashboard.source_stamp_ns保留，表内时间只转换为本机时区毫秒显示；未知源时间不补造。

原生组件只读现有接收结果，没有扩展包、npm、账户扩展安装或Linux变更，不增加无线字段、带宽及ROS订阅。新布局须配套本版PC接收端，旧接收程序没有dashboard通道。

### 当前布局与交付文件

统一使用本目录的foxglove_layout.json；不再分发旧车速布局、旧源时间布局或未采用的扩展草稿。数据新鲜度与GNSS质量均为background样式，接收年龄为bar样式；导入新版布局即可生效，无需重启接收端或占用第二个COM3串口。源码、布局与开发说明的目录约定见主项目docs/DELIVERY.md。

## CLI参数与管理操作（自0.7.0起）

当前“参数控制”页用Parameters编辑Linux YAML草稿，保存按钮成组提交；“CLI 日志与控制”页承载检查/启动/停止/查询和状态。模块选择、文件revision与保存结果均来自CLI统一接口，不再使用旧的合并页名。

CLI页的原生Publish按钮为检查、启动、停止、查询状态；参数页为保存配置、丢弃草稿并重新读取。均发布到 `/fsd_radio/extension/tx`，schema为fsd_radio.CliCommand，action取check/start/stop/status/save/reload；PC自动附加请求ID、boot、run与到期时间。/radio/cli/control显示状态/最终结果，各客户端共享同一PC草稿。

Linux适配器 `cli_control.LinuxControl` 只转发到CLI邮箱，业务由 `fsd_startup/control_api.py` 在Manager原操作线程执行；配置由CLI ConfigStore写入，目录与交叉校验复用宿主tools。检查/启动/停止直接调用已有Manager流程，不另建进程管理器。保存只写Linux文件、刷新审计，不代表在线算法生效，不自动重启；启动返回也不代表全栈已经就绪。

两端CLI与无线必须配套更新。Linux主仓库 `git pull --ff-only` 后 `git submodule update --init --recursive`；按原部署模式重启CLI与Linux无线程序。CLI需常驻菜单，例如 `bash start_fsd_manager.sh --menu --skip-build`，无终端则 `--headless --menu --skip-build --no-rviz`，保留原串口、模型等部署选项。默认无--menu仍会自动启动。无线独立运行，CLI stop不会退出CLI，才能再次远程start。Windows更新后重启start_windows.ps1并导入新版foxglove_layout.json；本版仅更新源码，不代表当前进程已加载。

PC与Linux时间需同步：心跳源时间超过10秒、未来偏差超过5秒或本机8秒未收到心跳时，不允许新操作。CLI每秒写心跳，目标每2秒无线刷新，与既有日志/几何共用3000 UART B/s。RPC复用512字节扩展：最多256KiB JSON、zlib、144字节块/base64，每块ACK，2秒重传，最多8项在途请求/回复，约180秒等待。配置读取临时占扩展时隙，不增加总预算，不保证其他图层固定频率。

串口ACK和块ACK不代表保存成功。最终回复含id、boot、ok、stage、result或error、Linux source_stamp_ns。CLI持久执行标记防止同ID重复启动，boot变化/旧run停止请求拒绝执行；崩溃或超时结果可能未知，查原ID而非重新启动。Linux可 `python3 fsd_startup/control_api.py cli.result --request-id <ID>`；邮箱和结果在 `.runtime/fsd_manager/control`。

支持18项配置，缺失项标不可用；只编辑既有标量/定长标量数组，不改结构、null、别名或merge key。radio_linux只修改Linux副本，Windows配置不自动同步；改链路参数需双端配套。全部来源、覆盖项、事务及API详见主仓库 `docs/FOXGLOVE_PARAMETERS.md`。

本轮按用户要求未执行测试、构建、WSL、串口或实车验证；未触发远程检查/启动/停止，未保存现场配置。

## 0.7.1：指令与数据独立优先级

双向发送新增独立commands FIFO（默认8条，与普通extensions FIFO各自限长）。每次选择新无线分片时，commands先于日志落盘ACK、运行选择、完整日志、普通扩展和几何/状态数据；不受原“每5片”扩展时隙限制。链路ACK仍随原帧携带，PC仍只响应Linux轮询。正在等待ACK/重传的当前帧不强行中断，既有3000 UART B/s总预算和重试规则保持。

| 类别 | 优先级与内容 |
| --- | --- |
| 指令 | cli.stop/start/check/status/result、config.set，及这些操作的RPC分块ACK、成功/错误/超时结果；独立高优先级队列 |
| 参数读取 | config.list/read的请求、分块ACK、文件内容回复；普通扩展数据队列 |
| 数据 | 日志、心跳、状态、几何，沿用原有数据内部调度 |

RPC块新增可选p=1（指令）/0（数据），缺省0兼容旧消息；分块确认沿用对应传输的优先级，重传和最终执行回复也不降级。每个RpcPeer仍最多8项在途传输，普通传输最多占6个名额，为指令至少预留2个名额；普通数据队列满时不会阻断独立指令队列的发送。

Linux CLI邮箱也先处理待执行指令、后处理参数读取，同级按到达顺序；保持Manager单线程业务执行，不打断正在执行的操作。不会把较晚的stop重排到较早的start之前；不保证停止可绕过正在进行的检查/启动。`/radio/link.command_queue`与extension_queue分别显示两队列待发消息数（含尚未发完的消息）。

两端无线和CLI必须更新并重启才完整生效。原0.7.0仍可解析消息，但其发送端/CLI不具备新优先级；不能只更新Windows就宣称Linux结果回传优先级已提升。指令提交较少时，剩余带宽仍用于日志/可视化，不按方向固定切分带宽。升级前等待现有业务请求完成，避免把接收进程重启当成指令取消。

新增无串口回归用例覆盖数据队列满/日志积压时指令抢先、分片后恢复普通传输、RPC容量预留及确认优先级。按用户此前要求，本轮不执行这些用例，不运行WSL、构建或实车操作。

## CLI状态指示面板（自0.7.2起）

“CLI 日志与控制”的原生Indicator显示CLI阶段：绿色运行/通过，蓝色等待/已停止，橙色处理中（含操作中），红色失败，灰色等待/离线。数据取原CLI心跳phase及PC管理请求状态，过期不继续显示旧运行状态。仅PC显示派生，不增加无线数据。

### 当前布局：CLI操作与参数控制分离

“CLI 日志与控制”不显示专用传感器表格；右侧为CLI状态、操作按钮、vehicle.rows车辆任务表以及其下方RSSI，左侧为完整日志、传输状态和结果详情。“传感器概览”集中专用传感器信息；“参数控制”只保留编辑、保存与重新读取。仅换布局无需重启或改变传输。

布局分工以本节和顶部当前版本表为准。原“CLI 日志与传感器”“CLI 控制与参数”均为历史页名；当前导入文件仍保留FSD_E22_CLI与传感器.json这个交付文件名。

### 0.7.3：CLI操作中状态

CLI状态Indicator新增橙色“操作中”：在线CLI有活动管理请求，或本机的检查/启动/停止/状态查询/配置保存指令排队、等待最终结果时显示。完成或超时后恢复心跳phase，离线优先显示离线；参数目录/文件自动读取不触发操作中。PC /radio/cli/control增加busy显示元数据，不改变CLI真实phase、无线格式或带宽。需加载新版Windows接收端并重新导入布局，Linux无需为此变更升级；未运行测试或实车操作。

## 0.7.4：数据包尾RSSI与本机指示器

“CLI 日志与控制”右侧vehicle.rows表格下方新增原生RSSI背景Indicator，消费 `/radio/link.rssi.display_raw`。显示接收端测得的Linux→Windows信号原始字节，例如 `RSSI RAW 160 · 0xA0`；有效样本统一蓝色，不用未经确认的阈值区分强弱。等待、未开启解析、过期和接收端断开分别提示。样本默认10秒过期（沿用foxglove.stale_seconds），不继续显示过期值，也不把缺失值伪装成0。

用户已开启Windows模块的数据RSSI，因此本版radio.yaml的serial.append_rssi_pc=true；serial.append_rssi_linux=false，只有Linux模块也开启数据RSSI时才改true。软件开关必须与对应接收模块REG3.bit7一致；本程序不改写模块寄存器。关闭模块附加RSSI时务必同步关闭对应软件开关，不能自动猜测某个字节是RSSI还是下一帧。Windows读取的是Linux→Windows信号；Linux本地接收RSSI没有在本版额外转发回Windows。

解析部署前提：两端模块分包为240字节（serial.module_packet_bytes记录此设置），一条应用无线帧对应一个模块无线包。当前最大应用帧226字节，固定停止等待/单帧写入；RSSI在帧尾换行之后追加。Framer先通过原帧CRC，再把紧随的一个字节作为RSSI，解析状态跨串口read保留，支持拆分/合并读取，RSSI值等于换行0x0A或起始符0x7E时也按数据消费。原帧立即交给ACK流程，不为等待尾字节阻塞业务确认。该功能不适用于模块将应用帧拆成多个无线包、合并多个应用帧再只附一个RSSI、或透传其他未采用本应用帧协议的数据。开启解析却配置32/64/128字节分包会拒绝启动；此软件记录不等于已读回核验真实硬件分包。

手册E22-xxxT22Useries_UserManual_CN_v1.1印刷第13页说明REG3.bit7开启后追加一个RSSI字节；环境噪声处给出的-RSSI/2没有单独明确包尾数据RSSI换算。本版保留unsigned 0..255原始值，不套用其他型号的dBm公式或“优/良/差”阈值。Indicator使用固定字节值标签及错误状态规则，不需要自定义扩展；原生组件字段见[Foxglove Indicator说明](https://docs.foxglove.dev/docs/visualization/panels/indicator)。

/radio/link.rssi字段：enabled、valid、raw、display_raw、age_s、captured_at_unix_ns、clock_domain=unix、time_origin=local_uart_capture、samples、packet_session、packet_seq。display_raw为有效原始值或-1等待/-2关闭/-3过期/-4断开；raw保留最近样本供诊断，过期时不继续作为有效值显示。时间来自本机读到尾字节的Unix纳秒采集时刻，不能冒充模块硬件时间或Linux ROS源时间。samples包含通过应用帧CRC的接收包/重传包，非业务消息计数。

此为模块生成的接收端串口附加元数据，不增加应用无线消息；USB串口RX计数会包含附加字节。按要求未运行测试、WSL、构建或实机验证，尚未核验硬件追加粒度/换算；新增test_rssi.py覆盖全部256个字节、逐字节拆分、粘包、关闭解析及过期行为，未执行。部署时发现用户已在22:06重新启动旧接收程序；确认无在途操作后，已重启Windows接收端加载本版，沿用COM3/115200/native，需重新导入布局。未发送CLI操作或写模块寄存器。

## 0.7.5：Windows自动选择USB串口

默认serial.pc_port=auto，start_windows.ps1不再固定COM3。接收线程每次启动/断线重连都枚举USB串口，按serial.pc_usb的VID/PID选择当前CH340适配器（1A86:7523）；因此端口变为COM4/COM7也无需改号。未插入时保留Foxglove服务并每秒等待设备；同型号候选超过一个时提示歧义，不任取第一个、不尝试打开其他设备。手动覆盖仍可用 `start_windows.ps1 -Port COM3`，显式端口按原行为重连。

此识别依据USB转串口芯片，不是E22射频型号认证。当前适配器没有序列号；多个同型号适配器可配置pc_usb.location固定USB位置，或有序列号的设备配置pc_usb.serial_number精确匹配。枚举不打开串口、不切配置模式、不发送探测/寄存器指令。Linux串口选择保持原配置；硬件RSSI/波特率等仍须匹配。

/radio/link.port显示实际选中COM号，port_selection为auto/explicit。使用已存在的接收进程不会自动加载新选择方式，应下次通过start_windows.ps1启动，不要同时开两个读者。本轮只枚举观察本机CH340位于COM3，未打开/重启接收程序、未执行测试或WSL；新增选择规则用例未运行。

## 0.7.6：参数操作状态面板

“参数控制”顶部新增原生背景Indicator，显示等待、离线、读取中、就绪、草稿未保存、保存中、Linux已保存、失败、超时结果未知；下部增加参数操作详情。使用独立parameter_state/parameter_detail，不被普通CLI检查/启动/停止结果覆盖；保存成功仅由Linux config.set成功回复确认，不代表算法在线生效。源phase/时间保留，不增加无线数据。需加载新版Windows接收端并导入布局；Linux无需为此显示变更升级。未执行测试、WSL或实车操作。

## 0.7.7：CLI状态指示显示启动任务退出码

CLI Manager的runtime_exit_code经原心跳/状态接口传输，PC指示器在任务结束、无操作且在线时显示退出码和含义：0正常结束；1启动/运行失败；2参数/用法错误；126无法执行；127不存在/无法启动；130/-2 SIGINT；143/-15 SIGTERM；137/-9 SIGKILL；134/-6 SIGABRT；139/-11 SIGSEGV。其他值统一提示查看详情，/radio/cli/control.exit_detail保留具体数值和说明。

这些码属于start_full_fsd启动子进程，不是CLI管理器本身或无线窗口退出码。0不代表仍在运行，SIGINT可能来自主动停止，SIGKILL不能单凭数值认定OOM。离线/操作中优先，运行或检查失败/清理未完成阶段不被旧退出码覆盖。新版CLI在新运行开始清空旧退出码；旧CLI仅在phase明确含“启动任务结束，退出码”时解析兼容，不凭停止状态猜码。

完整显示需配套加载CLI、Linux发送端、Windows接收端并导入布局；当前进程本轮未重启，未执行测试或实车操作。
