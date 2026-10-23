# E22 macOS 接收端

选择 arm64（Apple 芯片）或 x86_64（Intel）压缩包，完整解压到可写目录。包内已包含 Python 和接收依赖，无需自行安装 Python。构建环境为 macOS 15，其他系统版本尚未实机验收；实际构建信息见 BUILD_INFO.txt。

先退出原接收程序，再双击 Start_Receiver.command。它会打开终端并运行旁边的 FSD_E22_Receiver；Ctrl+C 停止。保持两者在同一文件夹。Foxglove 连接 ws://127.0.0.1:8765，导入包内 FSD_E22_CLI.json，Linux CLI/无线需要配套更新。

默认自动识别原 CH340 USB 串口，UART 115200、模块空速 62.5 kbps、PC 数据 RSSI 开关保持原配置，不自动写模块寄存器。仍需安装 Foxglove，以及 macOS 未识别适配器时所需的 USB 串口驱动。

终端覆盖参数示例：

```sh
./FSD_E22_Receiver --mode bridge
./FSD_E22_Receiver --port /dev/cu.usbserial-XXXX --mode bridge
```

默认内置 native 模式，需与 Linux 发送端输入模式配套。自定义本机配置先运行 `./FSD_E22_Receiver --export-config ./config`；导出后退出，不打开串口，也不覆盖已有文件。config 放在可执行文件旁时自动读取 config/radio_link/radio.yaml，或用 --config 指定。修改参数的 Linux CLI 接口与此本机配置不同；日志仍存 ~/.fsd_radio/cli_received。

此包未使用 Apple Developer ID 签名或公证。macOS 可能阻止首次运行：确认来源为本仓库 Release 后，按系统“隐私与安全性”的提示允许打开；不需要关闭系统整体安全保护。构建只证明打包成功，没有启动接收器或完成串口/实机验收。
