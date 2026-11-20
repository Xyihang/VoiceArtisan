# v1.1.64 发布说明

> 英文品牌正式更名：**VoiceSmith → VoiceArtisan**（中文名「声匠」不变）。

## 为什么改名

`VoiceSmith` 在 GitHub 上已有两个 200★ 以上的同名项目（`jurihock/voicesmith` 288★、`dunky11/voicesmith` 230★）以及一个 `voicesmith-mcp`，公开发布会被搜索稀释、也容易被误认为是别人的分支。

新名 **VoiceArtisan**（artisan = 匠人，直译「声匠」）：

| 检查项 | 结果 |
| --- | --- |
| GitHub 同名仓库 | 零命中 |
| PyPI / npm | 均未被占用 |
| `voiceartisan.com` / `.dev` | DNS 未解析，大概率可注册（下单前请复核） |
| 语义 | 匠人 · 手艺，与中文名一致，非造词 |

标语：`VoiceArtisan — your voice, your machine.` / 「把声音做成手艺」。

## 改了什么

| 位置 | 变化 |
| --- | --- |
| 界面与文档 | `声匠 · VoiceArtisan`、`VOICEARTISAN`（顶栏轮播）、README / 教程 / 帮助页 / Demo 页 / 总览图 / 参赛文案全部同步 |
| 安装器与启动器 | `VoiceArtisan-Setup.exe`（tk 版 9.0 MB）、`VoiceArtisan-Setup-Qt.exe`（PySide6 onedir，exe 1.5 MB / 目录 106 MB），均已重新构建 |
| `start.bat` | 优先拉起新名；**同时保留 `QWENTTS-Setup*` 旧名兜底**，已发出的旧包仍能启动 |
| MCP 命令 | `voiceartisan-mcp`；**保留 `qwentts-mcp` 作为兼容别名**（`pyproject.toml` 两个 entry point 都指向 `app.mcp_server:main`），旧客户端配置无需改 |
| 启动器源码 | `launcher/voiceartisan_launcher.py`、`launcher/voiceartisan_launcher_qt.py`（构建脚本、检查脚本同步） |
| 版本 | `VERSION.json` / `app.__version__` 同步为 **1.1.64**（一致性测试覆盖） |

## 保持不变（重要）

- 中文名「声匠」、包名 `qwentts`、目录 `QWENTTS`、数据目录 `data/`、配置 `config/config.yaml`
- 引擎 id（`qwen3-tts-1.7b` 等）、环境变量前缀 `QWENTTS_*`
- 已有用户的任务库、音色库、设置无需迁移；覆盖升级即可

## 修复

- **打包再次堵住 `launcher.json` 泄漏**：改名后新构建目录里又生成了一份含本机绝对路径的缓存文件。打包脚本改为**按文件名拦截**（任何 `launcher/` 下的 `launcher.json` 都不进包），并停止跟踪 `launcher/dist-qt`、`launcher/dist` 两个构建目录（发布包照常从磁盘打包，仓库因此瘦身）。

## 上一版（v1.1.63）一并回顾

- 许可改为双轨：`LICENSE` = PolyForm Noncommercial 1.0.0（个人/非商业免费），商业使用走 `LICENSE-COMMERCIAL.md` + EULA
- README 双语重做 + 真机截图 + 架构图 + 验证表 + FAQ
- 新增在线试听 `docs/demo/`（5 段真实样本）与真机流程 GIF `docs/assets/demo.gif`

## 校验

```
pytest                114 passed
Qt 启动器 --self-test 13 项通过 / 2 项提醒 / 0 项失败（退出码 0）
tsc + vite build      0 错误，dist 449 kB（gzip 155 kB）
包内容复查            launcher.json 0 个；本机绝对路径 0 命中
```

## 包

| 包 | 大小 | 文件 |
| --- | --- | --- |
| `QWENTTS-portable-v1.1.64-20261120-230512.zip` | 79.03 MB | 439 |
| `QWENTTS-portable-v1.1.64-sale-20261120-230543.zip` | 60.85 MB | 381 |
| `QWENTTS-src-v1.1.64-20261120-230353.zip` | 18.96 MB | 266 |

> 同版本更早的 `...230415.zip` / `...230450.zip` 含 `launcher.json`，请勿上传。
