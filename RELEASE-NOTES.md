# v1.1.63 发布说明

> 一个「许可 + 呈现」版本：代码功能与 v1.1.62 一致，重点是把项目变成**可被信任、可被听懂**的样子。

## 亮点

### 1. 许可改为双轨（source-available + 商业授权）

| 场景 | 许可 | 费用 |
| --- | --- | --- |
| 个人 / 学习 / 研究 / 非商业自用，可修改、可非商业再分发 | `LICENSE`（PolyForm Noncommercial 1.0.0） | 免费 |
| 商业使用（企业内部生产、接单交付、集成进商业产品、对外提供服务） | `LICENSE-COMMERCIAL.md` + `legal/LICENSE-EULA.txt` | 一次买断 |

- 新增 `LICENSE-COMMERCIAL.md`：写清"什么算商业使用"、买断范围与联系方式。
- `legal/LICENSE-EULA.txt` 增加「适用范围说明」：商业交付物适用 EULA，仓库源码适用 PolyForm，两者不冲突。
- 商业授权仍是**使用权**，不转让著作权与源码所有权；不得公开发布基于本软件的修改版。

### 2. README 双语重做

- 自绘 banner（`docs/assets/banner.svg`，纯矢量）+ 8 枚徽章
- **6 张当前版本真机截图**（工作台 / 任务中心 / 音色库 / 设置 / 启动器深浅主题）
- 问题 → 做法对照、13 引擎表、三种运行模式、4GB 显存实测、云端三种连法与 S0–S8、MCP 9 工具、OpenAI 兼容接口、mermaid 架构图、验证数字表、5 条 FAQ、路线图
- 中英双语（中文在上、English 在下，带锚点互跳）

### 3. 在线试听 Demo（真实样本）

新增 `docs/demo/`：**5 段真实合成音频 + 可复现参数**，直接用浏览器播放，无需安装。

| # | 能力 | 说明 |
| --- | --- | --- |
| 01 | 语音生成（中文旁白） | Vivian 温柔女声，9.2s |
| 02 | 语音生成 + 指令控制 | Uncle Fu 磁性男声，"沉稳、克制、语速稍慢" |
| 03 | 语音生成（英文） | Ryan 英文男声 |
| 04 | 方言 + 指令 | Eric 四川话口音 |
| 05 | 音色设计 | 一句自然语言描述造出"深夜电台女声" |

- 全部由 `POST /api/tasks` 真实生成，`Original` 档位（模型原生 WAV 24kHz），**未启用任何后期处理**。
- 页面上标注了文本 / 音色 id / 指令 / 语言 / 引擎 / 首包 / RTF，可原样复跑。

### 4. README 动图（真机流程）

`docs/assets/demo.gif`（972 KB，8 帧）：选音色 → 输入文本 → 开始生成（进度回传）→ 波形 + 首包 / RTF / 显存。所有帧来自真机截图，没有合成状态。

## 修复

- **打包泄漏**：`launcher/dist-qt/QWENTTS-Setup-Qt/launcher.json` 曾被纳入版本库与发布包，里面含开发者本机绝对路径。现已：
  - `git rm --cached` 停止跟踪，并加入 `.gitignore`
  - 打包脚本 `SKIP_REL` 增加该路径，后续包不再携带

## 升级说明

- 从 v1.1.62 升级：覆盖文件即可，无需迁移数据。
- 若你是**商业授权用户**：本次版本不改变你的授权范围，EULA 依旧适用。

## 校验

```
pytest          114 passed
UI 回归          24 项通过
MCP selftest     6 步通过
tsc --noEmit     0 错误
启动器体检首行    ≤0.5s
```

## 包内内容

- `start.bat` → 优先拉起 Qt 启动器 `launcher/dist-qt/QWENTTS-Setup-Qt/QWENTTS-Setup-Qt.exe`（免装 Python / PySide6），失败退回 tk 版
- `frontend/dist`（构建产物）、`backend/app`（源码）、`docs/`（含 `demo/` 试听页与 `overview/` 总览图）
- `运行教程.md`、`LICENSE`、`LICENSE-COMMERCIAL.md`、`LICENSE-EULA.txt`
