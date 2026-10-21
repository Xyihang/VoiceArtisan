# ASR-ENGINE-1 · ASR 模型纳入「引擎管理」规格

- 版本：v1 · 2026-10-20
- 状态：**已实施**（2026-10-20，后端 `engine/adapters/asr_whisper.py` + `kind` 分组，前端引擎管理页签）
- 关联：`docs/spec/CLOUD-AGENT-1.md`

---

## 1. 背景与目标

现状问题：

- TTS 有 7 个引擎走 `registry` + `/api/engines` + 「引擎与模型」页面统一管理（查看/安装/删除/切换/依赖/体积）；
- **ASR 完全游离**：只有 `config.asr.local.model_size` 一个下拉框（tiny/base/small/medium/large-v3），
  模型在首次转写时由 `faster-whisper` **隐式下载**到 `models/faster-whisper/`，用户看不到体积、也不知道装没装、无法删除、无法单独安装。

目标：把 ASR 模型以与 TTS 引擎**同一套机制**纳入引擎管理——可见（体积/状态/依赖）、可装（显式点击）、可切、可删，且不破坏既有 `asr.local.*` 配置与云端 ASR 行为。

---

## 2. 现状盘点

| 位置 | 现状 |
| --- | --- |
| `backend/app/engine/asr.py` | `LocalASREngine._ensure()` 按 `cfg.asr.local.model_size` 构造 `WhisperModel(size, download_root=models/faster-whisper)`，首次调用触发下载 |
| `backend/app/config.py` | `AsrCfg.engine ∈ {local, cloud}`、`AsrLocalCfg{model_size,device,compute_type,beam_size}`、`AsrCloudCfg{base_url,api_key,model}` |
| `backend/app/engine/registry.py` | `REGISTRY[engine_id] = 适配器`，`all_items()` 供 `/api/engines` 使用；每个条目含 id/名称/kind_tags/安装状态/体积/依赖 |
| `backend/app/engine/manager.py` | `install/select/remove/install_status`，安装是后台线程 + 事件推送（202 + job_id） |
| `frontend/src/pages/SettingsPage.tsx` | `SECTIONS` 里 `asr` 分区：ASR 引擎（本地/云端）+ 本地模型规模下拉 + 计算精度 |
| 「引擎与模型」分区 | 只渲染 TTS 引擎卡片，无 ASR |

---

## 3. 功能规格

### 3.1 引擎条目模型扩展

- 每个引擎适配器新增只读属性 **`kind: "tts" | "asr"`**（默认 `"tts"`，保证现有适配器零改动）。
- `registry.all_items()` 与 `manager.items()` 的返回增加 `kind` 字段；
- `/api/engines` 支持 `?kind=asr|tts`（缺省返回全部），保持向后兼容（前端旧代码不受影响）。

### 3.2 ASR 引擎清单（首批 5 个，全部基于既有 `faster-whisper==1.2.1`，不引入新依赖）

| engine_id | 显示名 | 体积（约） | 说明 |
| --- | --- | --- | --- |
| `asr-whisper-tiny` | Whisper Tiny | 75 MB | 最快，精度低 |
| `asr-whisper-base` | Whisper Base | 145 MB | 快速草稿 |
| `asr-whisper-small` | Whisper Small | 480 MB | **默认**，与当前行为一致 |
| `asr-whisper-medium` | Whisper Medium | 1.5 GB | 高精度，CPU 上很慢 |
| `asr-whisper-large-v3` | Whisper Large-V3 | 3.1 GB | 最高精度，建议 GPU |

（后续可增量加入 FunASR/SenseVoice 类适配器，接口不变。）

每个适配器需实现（沿用现有 TTS 适配器约定）：

| 能力 | 语义 |
| --- | --- |
| `info()` | id、name、kind、模型体积、所需包（`faster-whisper==1.2.1`）、是否已安装、模型目录 |
| `install(dry_run=False)` | 下载模型到 `models/faster-whisper/<size>`，进度通过回调推送（与 TTS 安装同一事件通道） |
| `is_installed()` | 模型目录存在且含必要文件（`model.bin`） |
| `remove()` | 删除该模型目录，仅当不是当前选中引擎（否则 `409 IN_USE`） |
| `health()` | `{ok, model, device, compute_type}` |

### 3.3 选择与生效

- 新增配置字段（向后兼容）：`asr.local.engine_id: str = ""`。
- 解析优先级：`engine_id` 非空 → 用它；为空 → 用 `model_size` 映射（`tiny/base/small/medium/large-v3` → 对应 `asr-whisper-*`），**老配置行为完全不变**。
- 切换 ASR 引擎 = `POST /api/engines/{id}/select`（沿用现有接口）→ manager 写 `asr.local.engine_id` 并热切换 `LocalASREngine`（清空已加载模型）。
- TTS 的 `active` 与 ASR 的 selected 分别独立：`/api/engines` 返回 `{"active": <tts>, "active_asr": <asr>, "items": [...]}`。

### 3.4 「不自动下载模型」约束的边界

- **转写时不再隐式下载**：若选中的 ASR 模型未安装，转写直接返回
  `ASR_MODEL_MISSING`，文案：「ASR 模型未安装，请到设置 → 引擎与模型 → 语音识别 点安装」，附 `next_actions`。
- 只有用户在引擎管理页**显式点「安装」**时才下载（与 TTS 引擎安装一致）。
- 迁移期（`engine_id` 为空且 `model_size` 已隐式下载过）不报错，继续可用。

### 3.5 云端 ASR

- `asr.engine = cloud` 时行为不变（走 `AsrCloudCfg`），引擎管理页对 ASR 卡片的「切换」置灰并提示「当前 ASR 走云端，本地模型不生效」。

---

## 4. 接口规格

| 方法 | 路径 | 变化 |
| --- | --- | --- |
| `GET` | `/api/engines?kind=asr` | 新增可选筛选；返回 `{active, active_asr, items[]}` |
| `GET` | `/api/engines/{id}/plan` | 复用（ASR 适配器也实现 `install(dry_run=True)`） |
| `POST` | `/api/engines/{id}/install` | 复用，ASR 安装同样返回 `202 {job_id}` |
| `DELETE` | `/api/engines/{id}` | 复用；`IN_USE` 时 409 |
| `POST` | `/api/engines/{id}/select` | 复用；对 ASR 引擎写 `asr.local.engine_id` |
| `GET` | `/api/asr/health` | 新增：当前 ASR 引擎、已装/未装、设备与精度 |

事件：沿用 `engine` topic（`install_progress` / `install_done`），前端无需新通道。

---

## 5. 界面规格

「设置 → 引擎与模型」分区顶部新增分段控件：**语音合成（7） | 语音识别（5）**。

- 语音识别页签：卡片列表（名称、体积、已装/未装、依赖、模型目录），每张卡：
  - 未装 → 「安装」（走同一进度条与取消逻辑）
  - 已装未选中 → 「切换」
  - 已选中 → 徽标「当前使用」+ 「删除」置灰
- 设置页 `asr` 分区：
  - 「ASR 引擎」（本地/云端）保留；
  - 「本地模型规模」下拉**改为只读展示**「当前：Whisper Small（已装）」+ 按钮「去引擎管理」，避免两处配置打架；
  - 计算精度、beam 保留。

---

## 6. 非功能与兼容

| 项 | 要求 |
| --- | --- |
| 兼容性 | 老 `config.yaml` 无 `engine_id` 时行为与现在完全一致；`/api/engines` 不加 `kind` 参数时返回结构只多一个字段 |
| 不新增依赖 | 仅使用已锁定的 `faster-whisper==1.2.1` |
| 安装体积提示 | 安装前 `plan` 返回下载体积并二次确认（与 TTS 安装一致） |
| 失败可读 | 下载失败给 `ASR_INSTALL_FAILED` + 镜像/网络建议 |
| 数据 | 模型目录 `models/faster-whisper/<size>`；安装状态沿用 `data/engine-install-state.json` |

---

## 7. 验收标准

| 编号 | 步骤 | 预期 |
| --- | --- | --- |
| AC1 | 打开设置 → 引擎与模型 → 切到「语音识别」 | 列出 5 个 ASR 引擎，`small` 显示「当前使用」，体积与依赖可见 |
| AC2 | 点 `Whisper Base` 的「安装」 | 出现进度条与事件推送，完成后状态变「已装」，`models/faster-whisper/base` 目录存在 |
| AC3 | 点 `Whisper Base` 的「切换」 | `config.yaml` 出现 `asr.local.engine_id: asr-whisper-base`，转写使用 base 模型（日志 `size=base`） |
| AC4 | 删除当前选中的引擎 | 按钮置灰，接口返回 `409 IN_USE` |
| AC5 | 删除未选中的已装引擎 | 目录被删，卡片回到「未装」 |
| AC6 | 手动把 `config.yaml` 里 `engine_id` 删掉、只留 `model_size: small` | 转写仍工作（映射到 `asr-whisper-small`），无报错 |
| AC7 | 选中一个未安装的引擎后直接转写 | 返回 `ASR_MODEL_MISSING`，前端提示去引擎管理安装，**不再静默下载** |
| AC8 | `GET /api/engines?kind=asr` | 只返回 5 个 ASR 条目；`GET /api/engines` 仍返回 TTS+ASR 全部 |
| AC9 | 切到云端 ASR（`asr.engine=cloud`） | ASR 卡片「切换」置灰并给出提示，转写走云端 |
| AC10 | 回归 | `audit_all.py` 20 项仍全绿（B3 引擎矩阵、C1 页面冒烟不受影响） |
