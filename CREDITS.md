# 出处与致谢

## 编写者

本项目的代码由 **DeepSeek Harness 中的 DeepSeek 模型（deepseek-flash）** 编写；
需求提出、验收和部署由项目所有者完成。

## 一、这个仓库里的代码分两类

| 类别 | 位置 | 说明 |
|---|---|---|
| 自己写的 | `local/`、`cloud/`、`tools/`、`tests/` 等 | 从零编写，没有复制第三方源码 |
| **第三方源码（原样收录）** | `vendor/tinggu/` | **听骨（Tinggu）**的完整源码，MIT 协议，见下 |

## 二、第三方源码：听骨 / Tinggu

音频分析这一块没有自己造轮子，直接收录了 **听骨（Tinggu）** 的源码，放在 `vendor/tinggu/`。

- 仓库：https://github.com/SeithAsync/Tinggu
- 作者：**seith** · Co-created with **Claude（Anthropic）** · **kael**
- 协议：**MIT**（完整许可证原文见 `vendor/tinggu/LICENSE`，请一并保留）
- 版本：v1.5
- 收录方式：**目前原样收录，源码未做修改**。本项目只是在外面加了"录音 → 存 wav → 调它分析 → 取报告"的封装。
- 如果以后修改了它的源码，会在被改动的文件头部注明"修改自 Tinggu vX.Y，改了什么"，并更新本节。

### 听骨自己的来源与致谢（按 MIT 要求一并保留）

听骨的浅听基础分析源自 **eryu** 项目（MIT，作者 sebastianevan200-stack）；
它还致谢了下面这些项目与人：

| 项目 / 来源 | 许可证 | 在听骨里起什么作用 |
|---|---|---|
| [Basic Pitch](https://github.com/spotify/basic-pitch)（Spotify） | Apache-2.0 | 音符提取模型 |
| [whale-listen](https://github.com/migratorywhale)（migratorywhale） | MIT | 全曲 MIDI 化路线的先行者，音符层方向受其启发 |
| [Ocean Listen / 听海](https://github.com/ennisaaaaaaaa-stack) | MIT | 听骨的下游项目；泛音滤网参数按它的校准值重写，v1.3 张力轮廓的力度弧线思路也出自它 |
| [eryu](https://github.com/sebastianevan200-stack/eryu)（sebastianevan200-stack） | MIT | 浅听基础分析的原型 |
| Krumhansl-Schmuckler key-finding profile | 学术方法 | 听骨调性识别所依据的方法 |
| 和弦层与 v1.3 各层 | 听骨原创 | 功能状态判定按通用和声学规则表实现 |

## 三、自己写的那部分参考过什么

| 项目 | 地址 | 参考了什么 |
|---|---|---|
| samebeat | https://github.com/Iris3971/samebeat | 只看了"本地采集 + 展示"的方向，未使用其代码 |
| Duetto | https://github.com/avisforevelyn/Duetto | 同上，未使用其代码 |
| QQMusic-mcp | https://github.com/Yotohime777/QQMusic-mcp | 同上，未使用其代码 |

判断 QQ 音乐能给什么，是自己用 Windows 媒体会话接口实测的，没有抄这三个项目。

## 四、用到的开源库（通过 pip 安装，不在仓库里）

| 库 | 用途 | 许可证 |
|---|---|---|
| [pywinrt](https://github.com/pywinrt/pywinrt) | 读取 Windows 媒体会话 | MIT |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | MCP 工具服务 | MIT |
| [FastAPI](https://github.com/fastapi/fastapi) | 云端 HTTP 接口 | MIT |
| [Starlette](https://github.com/encode/starlette) | ASGI 框架 | BSD-3-Clause |
| [Uvicorn](https://github.com/encode/uvicorn) | ASGI 服务器 | BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic) | 数据校验 | MIT |
| [librosa](https://github.com/librosa/librosa) | 音频特征、节拍、频谱 | ISC |
| [soundfile](https://github.com/bastibe/python-soundfile) | 读写 wav | BSD-3-Clause |
| [soundcard](https://github.com/bastibe/SoundCard) | Windows 声卡回环录音 | BSD-3-Clause |
| [PyTorch](https://pytorch.org) | 听骨深听依赖（六轨分离、乐器识别） | BSD-3-Clause |
| [Demucs](https://github.com/facebookresearch/demucs) | 六轨音源分离 | MIT |
| [PANNs](https://github.com/qiuqiangkong/panns_inference) | 声音事件/乐器标签 | MIT |
| [matplotlib](https://matplotlib.org) | 频谱图 | PSF-based |

## 五、外部数据源

| 服务 | 用途 | 说明 |
|---|---|---|
| [lrclib](https://lrclib.net) | 歌词、歌曲时长 | 公开免费接口，本项目按需查询，不保存歌词正文 |
| 听骨内置的网易云歌词通道 | 深听配词（可选） | 听骨自带，本项目默认不启用；要启用时会在设置里写明 |

## 六、本项目自己的许可

本项目**自己写的部分**采用 [MIT 许可证](LICENSE)。

`vendor/tinggu/` 下的代码仍归听骨作者所有，**继续按它自己的 MIT 许可证使用**（保留 `vendor/tinggu/LICENSE`）。
本项目的 MIT 许可不会覆盖、也不能改变听骨及其上游项目的版权声明。

即使以后这个仓库改成私有，已经公开分发过的版本仍然受 MIT 约束——所以上面这些声明请保留在仓库里，不要删。
