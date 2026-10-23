# 出处与致谢

## 编写者

本项目的代码由 **DeepSeek Harness 中的 DeepSeek 模型（deepseek-flash）** 编写；
需求提出、验收和部署由项目所有者完成。

本项目（QQ 音乐听歌伴侣）的代码是照着任务书从零写的，**没有复制任何第三方项目的源码**。
下面列出参考过的项目、用到的开源库和外部数据源，方便追溯。

## 一、参考过思路的项目

任务书里列出的三个项目，用来判断"Windows 上能拿到什么、MCP 大致长什么样"：

| 项目 | 地址 | 本项目参考了什么 |
|---|---|---|
| samebeat | https://github.com/Iris3971/samebeat | 只看了"本地采集 + 展示"这个方向，未使用其代码 |
| Duetto | https://github.com/avisforevelyn/Duetto | 同上，未使用其代码 |
| QQMusic-mcp | https://github.com/Yotohime777/QQMusic-mcp | 同上，未使用其代码 |

说明：

- 这三个仓库的**代码没有被复制、没有被修改、也没有作为依赖引入**。
- 本项目的实现方式是自己实测出来的：用 Windows 系统自带的媒体会话接口读取 QQ 音乐状态，再用 SQLite + MCP 自己做。
- 如果以后确实要复用其中某个项目的代码，会在这里写清"哪一段、来自哪个仓库、什么许可证"，并在文件头标注。
- 三个项目的许可证没有逐一核对（因为没有用到它们的代码）。如果将来要引用，必须先核对许可证。

## 二、用到的开源库

| 库 | 用途 | 许可证 |
|---|---|---|
| [pywinrt](https://github.com/pywinrt/pywinrt)（winrt-Windows.Media.Control 等） | 读取 Windows 媒体会话 | MIT |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | 提供 MCP 工具服务 | MIT |
| [FastAPI](https://github.com/fastapi/fastapi) | 云端 HTTP 接口 | MIT |
| [Starlette](https://github.com/encode/starlette) | ASGI 框架（FastAPI 依赖） | BSD-3-Clause |
| [Uvicorn](https://github.com/encode/uvicorn) | ASGI 服务器 | BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic) | 数据校验 | MIT |

依赖由 `cloud/requirements.txt` 和 `tools/fetch_wheels.py` 安装，仓库里不存放编译产物。

## 三、外部数据源

| 服务 | 用途 | 说明 |
|---|---|---|
| [lrclib](https://lrclib.net) | 歌词、歌曲时长 | 公开免费接口，不需要账号；本项目按需查询，不保存歌词正文 |

## 四、本项目自己的许可

本项目自己的代码采用 [MIT 许可证](LICENSE)。

再次说明：仓库里没有任何第三方源码。上面列出的库是通过 `pip` 安装的独立依赖，
它们的许可证约束的是它们自己，不改变本项目的 MIT 许可；
如果以后真的把某个项目的代码片段抄进来，必须在这里写清来源、许可证和改动，并保留原版权声明。
