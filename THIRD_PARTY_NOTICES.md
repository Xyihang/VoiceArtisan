# 第三方组件与模型声明

本项目的**自身源代码、界面与文档**按 [LICENSE](LICENSE) 的专有许可（保留所有权利）发布；
但项目依赖下列第三方组件与模型，它们受各自许可约束。**专有许可不改变这些第三方许可**，
亦不得被解释为对其施加额外限制。

## 一、运行时依赖（主要）

| 组件 | 用途 | 许可 |
| --- | --- | --- |
| PyTorch / torchaudio | 深度学习运行时 | BSD-3-Clause |
| Qwen3-TTS（`qwen-tts` 包） | TTS 模型推理封装 | Apache-2.0 |
| Transformers / Accelerate | 模型加载 | Apache-2.0 |
| FastAPI / Starlette | HTTP 服务 | MIT / BSD-3-Clause |
| Uvicorn | ASGI 服务器 | BSD-3-Clause |
| Pydantic / pydantic-settings | 数据校验与配置 | MIT |
| PyYAML | 配置文件解析 | MIT |
| httpx | HTTP 客户端（云端调用） | BSD-3-Clause |
| websockets | WebSocket 实现 | BSD-3-Clause |
| **paramiko** | SSH 端口转发 | **LGPL-2.1**（以动态导入方式使用，未修改其源码） |
| python-multipart | 表单上传 | Apache-2.0 |
| soundfile / libsndfile | 音频读写 | BSD-3-Clause / LGPL-2.1 |
| numpy | 数值计算 | BSD-3-Clause |
| cryptography | 自签证书生成 | Apache-2.0 / BSD-3-Clause |
| faster-whisper | 本地语音转文字 | MIT |
| ffmpeg（可选，外部程序） | mp3 编码 / 部分格式解码 | LGPL/GPL（取决于所编译组件） |

## 二、前端依赖

| 组件 | 用途 | 许可 |
| --- | --- | --- |
| React / React DOM | UI 框架 | MIT |
| Vite | 构建工具 | MIT |
| TypeScript | 类型检查 | Apache-2.0 |
| lucide-react | 图标库 | ISC |

## 三、模型权重（不随本项目分发）

| 模型 | 说明 | 许可 |
| --- | --- | --- |
| Qwen3-TTS-12Hz-1.7B-Base | 声音克隆 | Apache-2.0（以官方仓库声明为准） |
| Qwen3-TTS-12Hz-1.7B-CustomVoice | 内置音色 + 指令控制 | Apache-2.0 |
| Qwen3-TTS-12Hz-1.7B-VoiceDesign | 音色设计 | Apache-2.0 |
| faster-whisper 系列（Systran 等） | 语音转文字 | 见各模型仓库 |

模型由使用者自行从 ModelScope / HuggingFace 下载，**不在本仓库中**，本项目不对其许可作任何担保。

## 四、合规提示

1. 商用前请自行确认：所用模型许可是否允许你的使用场景、参考音频与生成音色是否已获授权；
2. 若需对外分发本软件，须同时保留本声明与第三方许可文本（尤其 LGPL 组件 paramiko、libsndfile 的声明义务）；
3. 若替换依赖版本，请重新核对其许可是否变化。
