# agent-zh-cn

[![CI](https://github.com/652036/agent-zh-cn/actions/workflows/check.yml/badge.svg)](https://github.com/652036/agent-zh-cn/actions/workflows/check.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

为 Devin、Cursor、Windsurf 和 Visual Studio Code 桌面端补充简体中文界面翻译。

项目包含多应用安装器和界面词典，用于补充官方中文语言包尚未覆盖的设置、智能体窗口、菜单及提示文案。基础编辑器界面使用微软简体中文语言包，补充翻译在本地加载。

## 支持范围

| 软件 | 命令行标识 | 适配内容 |
| --- | --- | --- |
| Devin | `devin` | 编辑器、独立智能体窗口，以及缺失的本地化语言资源 |
| Cursor | `cursor` | 编辑器工作台和专有界面，兼容旧版 Cursor 补充翻译入口 |
| Windsurf | `windsurf` | 编辑器工作台及共用智能体界面文案 |
| Visual Studio Code | `vscode` | 中文语言包配置，以及可识别的工作台、独立会话窗口 |

安装器根据软件身份和已知窗口入口识别目标。版本号相同不代表所有安装布局都兼容；无法识别时会报告错误。

CI 在 Windows、macOS 和 Linux 上检查安装样本、移除、故障回滚及运行时内容保护。测试通过表示这些检查覆盖的行为正常，不代表每个软件版本都已完成实际界面的逐页验证。Windsurf 尚未完成实际安装验证。

## 安装

### Windows

1. [下载源码 ZIP](https://github.com/652036/agent-zh-cn/archive/refs/heads/main.zip) 并完整解压，或克隆仓库：

   ```sh
   git clone https://github.com/652036/agent-zh-cn.git
   cd agent-zh-cn
   ```

2. 保存目标软件中尚未保存的工作。
3. 双击 `一键汉化.bat`。检测到多个安装时，按提示选择目标；仅检测到一个时直接使用该目标。
4. 脚本会关闭所选软件、应用翻译并重新启动。

脚本优先调用 Python；使用 Python 时需要 3.10 或更高版本。未找到 Python 时，脚本使用 Windows PowerShell 5.1 原生实现。请保留解压后的目录结构，安装器需要同目录下的词典和适配文件。

### macOS / Linux

需要 Python 3.10 或更高版本。在项目目录中执行以下命令，以 Devin 为例：

```sh
python3 agent_zh.py list
python3 agent_zh.py apply --app devin --kill --restart
```

其他软件使用上表中的命令行标识。自定义安装位置可通过 `--path` 指定。

修改 macOS 应用资源可能影响代码签名和自动更新。

## 常用命令

以下命令在项目目录中运行。macOS 和 Linux 通常使用 `python3` 替换 `python`。

```sh
# 列出检测到的安装
python agent_zh.py list

# 检查 Devin 的翻译安装状态
python agent_zh.py status --app devin

# 应用翻译并重启 Devin
python agent_zh.py apply --app devin --kill --restart

# 移除补充翻译并重启 Devin
python agent_zh.py revert --app devin --kill --restart
```

| 参数 | 作用 |
| --- | --- |
| `--app` | 选择 `devin`、`cursor`、`windsurf` 或 `vscode` |
| `--path` | 指定可执行文件、安装目录或 `resources/app` 路径 |
| `--kill` | 允许关闭所选软件；通常应先保存工作 |
| `--restart` | 操作完成后重新启动所选软件 |
| `--no-langpack` | 跳过语言包下载，仍使用已安装的语言包 |
| `--defer-restart` | 应用文件和配置改动，保留运行中的软件，稍后手动重启 |
| `--all` | 操作所有检测到的安装，不能与 `--app` 或 `--path` 同用 |
| `--json` | 将 `list` 或 `status` 的结果输出为 JSON |

通常需要先退出目标软件，或使用 `--kill`。如果希望稍后重启，可执行：

```sh
python agent_zh.py apply --app devin --defer-restart
```

`--defer-restart` 仅用于 Python 入口的 `apply`，不能与 `--kill`、`--restart` 同用。此模式跳过语言包下载，改动在完全退出并重新打开软件后生效；写入期间请勿新开目标软件窗口。

自定义路径也可使用 `DEVIN_PATH`、`CURSOR_PATH`、`WINDSURF_PATH` 或 `VSCODE_PATH` 环境变量。检测到多个目标时，安装和移除操作会要求选择；非交互调用应使用参数缩小范围，或通过 `--all` 明确选择全部。

Windows PowerShell 入口支持相同的基本操作：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\AgentZh.ps1 -Cmd apply -App devin -Kill -Restart
powershell -NoProfile -ExecutionPolicy Bypass -File .\AgentZh.ps1 -Cmd status -App devin -Native
```

`-Native` 强制使用无 Python 依赖的实现。旧 `cursor_zh.py`、`CursorZh.ps1` 和 `scripts/scan_cursor_strings.py` 保留为 Cursor 兼容入口。

## 更新与移除

更新目标软件或本项目后，重新运行安装器以加载当前词典。部分英文文案可能来自尚未收录的界面、第三方扩展或服务端，重新安装不能补齐所有漏翻。

Windows 可双击 `取消汉化.bat`，也可使用上面的 `revert` 命令。移除会删除本项目的脚本引用和补充资源，并按当前应用文件更新校验值；保留官方中文语言包和中文语言设置。因此，移除补充翻译不等于切回英文。

安装前会保存原文件和清单，备份按软件、安装位置、版本及内容区分：

| 系统 | 备份位置 |
| --- | --- |
| Windows | `%APPDATA%\AgentZh\backup` |
| macOS | `~/Library/Application Support/AgentZh/backup` |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/AgentZh/backup` |

安装器会尝试回滚失败操作中已写入的文件；回滚不完整时会报告错误。正常移除按当前文件处理，不会用历史备份覆盖升级后的应用。

## 翻译与数据

补充翻译按词典匹配界面文本。运行时通过保护规则跳过代码、输入框、终端输出、文件和会话名称及聊天正文；若发现误翻，请通过 [Issue](https://github.com/652036/agent-zh-cn/issues/new/choose) 提供脱敏后的复现信息。

安装器会修改应用窗口资源、对应校验值及中文语言配置。语言包下载通过目标软件自带的命令行工具完成；补充翻译脚本本身不发起网络请求，也不上传界面内容。详细修改范围见 [安全政策](SECURITY.md)。

本项目为非官方社区工具，与所支持软件的厂商无隶属关系。远程网页、跨源 iframe 和操作系统原生对话框不在 DOM 翻译范围内。产品名、模型名和技术标识符通常保留原文。

## 参与贡献

漏翻和译文修正可通过 [Issue](https://github.com/652036/agent-zh-cn/issues/new/choose) 或 Pull Request 提交。词典结构、生成步骤、测试命令和新软件适配要求见 [贡献指南](CONTRIBUTING.md)。

## 许可证

[MIT License](LICENSE)
