# 规格：引擎无关化 + 切换按钮卡死修复（Multi-Engine Agnostic）

- 文档编号：SPEC-ENGINE-AGNOSTIC-1
- 日期：2026-10-20
- 关联：`docs/spec/UI-POLISH-1.md`（布局规格）、`docs/ENGINE_ISOLATION.md`（隔离机制）

---

## 1. 概述

### 1.1 背景

项目已有 7 个 TTS 引擎（Qwen3-TTS 1.7B / 0.6B、CosyVoice3 / 2 / 300M、VoxCPM1.5、IndexTTS-2）。
但**界面文案、能力开关、音色体系、显存判定**仍按「只有 Qwen3-TTS 1.7B」写死：

- 音色库 9 个内置音色只对 Qwen CustomVoice 有意义，选其它引擎时它们仍显示"可用"，选中后合成会失败或回退；
- 工作台四个能力页签不随引擎变化（0.6B / VoxCPM / IndexTTS 都不支持"音色设计"）；
- 设置页「本地模型」「模型预热」「推理设备 / 精度」的说明全部按 1.7B 表述；
- 路由的显存判定文案写死"1.7B 需要约 X GB"，其它引擎不适用；
- 用户不装 Qwen 模型、只用 CosyVoice/VoxCPM 时，多处提示让人以为"功能不可用"。

另外：引擎卡片点「使用」后按钮长期转圈，但右下角已提示切换成功。

### 1.2 目标

1. 不依赖 Qwen 模型也能完整使用项目：任意一个已安装引擎都能跑通"生成 / 克隆（若有）/ 转录"，
   界面明确告知**当前引擎支持什么、不支持什么、为什么**。
2. 「使用」按钮的 loading 与真实后端状态一致，切换完成立即复位。
3. 所有面向用户的文案不再写死 1.7B / Qwen（保留必要的型号说明作为"示例"）。

### 1.3 范围

- 在范围内：`/api/voices`、`/api/engines` 返回结构（新增字段，不改旧字段）、路由文案、
  工作台能力页签、音色库可用性、设置页相关分区文案与开关可用性、状态页引擎信息。
- 不在范围内：新增引擎、改变各引擎的推理实现、改安装/隔离机制。

---

## 2. 用户故事

1. 作为**只有 2GB 显存、仅装 0.6B** 的用户，我希望界面告诉我"音色设计不可用（当前引擎不支持）"，
   而不是让我点了之后报错。
2. 作为**只用 CosyVoice-300M-SFT** 的用户，我希望它自带的预置音色能直接出现在音色库里，
   并且内置的 9 个 Qwen 音色被标注为"当前引擎不可用"。
3. 作为**不装任何 Qwen 模型**的用户，我希望体检/状态页不把"Qwen 模型缺失"说成致命错误。
4. 作为任何用户，我点「使用」后希望按钮立刻恢复正常，而不是一直转圈。

---

## 3. 功能需求

| 编号 | 模块 | 优先级 | 描述 |
| --- | --- | --- | --- |
| F1 | 引擎切换 | P0 | 点「使用」后按钮 loading 在接口返回后立即结束（不再等第二次全量刷新） |
| F2 | 音色库 | P0 | `/api/voices` 返回 `engine`（当前引擎 id/名称/能力）与每个音色的 `available`/`reason` |
| F3 | 音色库 | P0 | 当前引擎不是 Qwen 时，9 个内置音色标为不可用并给出原因；自定义克隆音色保持可用 |
| F4 | 音色库 | P1 | 引擎自带预置音色（CosyVoice-300M-SFT）以"引擎预置"分组出现在列表里 |
| F5 | 工作台 | P0 | 四个页签按当前引擎能力启用/禁用，禁用项 hover 显示原因；默认页签落到可用项 |
| F6 | 工作台 | P1 | 等待文案、回退文案使用当前引擎名与规格，不再写死"1.7B / 20-60 秒" |
| F7 | 设置页 | P1 | 「本地模型」分区标注"当前引擎"和各规格归属；非 Qwen 引擎时提示这些路径不适用 |
| F8 | 设置页 | P1 | 「模型预热」「0.6B 回退」「推理精度」在非 Qwen 引擎下置灰并说明"仅 Qwen3-TTS 生效" |
| F9 | 路由 | P1 | 显存判定文案改为「引擎名 + 该引擎 vram_gb」，不再硬编码 1.7B |
| F10 | 状态页/体检 | P2 | Qwen 模型缺失在非 Qwen 引擎下只是提示，不影响"可用"结论 |

---

## 4. 详细规格

### 4.1 F1 切换按钮 loading

- **现象**：`handleSelect` 成功后 `await load(true)`；后端 `select` 会调用 `invalidate_info()` **清空**缓存，
  下一次 `/api/engines` 命中不到缓存 → 同步重算 6~7 个引擎（voxcpm 单引擎 46s）→ 请求超时 → 按钮一直转。
- **处理**：`invalidate_info()` 改为**标记过期**（保留旧数据、时间戳置 0），下一次请求立刻返回旧数据并在后台刷新。
- **验收**：点「使用」后 ≤2s 内按钮复位；`/api/engines` 响应 <200ms。

### 4.2 F2/F3/F4 音色可用性

`GET /api/voices` 新增字段（旧字段不变）：

```jsonc
{
  "engine": { "id": "cosyvoice-300m-sft", "name": "CosyVoice-300M-SFT（预置音色）",
              "capabilities": ["tts","clone"], "is_qwen": false },
  "builtin": [ { "...": "...", "available": false,
                 "reason": "内置音色来自 Qwen3-TTS CustomVoice，当前引擎不支持" } ],
  "custom":  [ { "...": "...", "available": true } ],
  "engine_voices": [ { "id": "cosyvoice300m-中文女", "name": "中文女（引擎预置）", "available": true } ]
}
```

- `is_qwen = adapter.id.startswith("qwen3-tts")`；
- 非 Qwen 引擎：`builtin[].available = false` 且带 `reason`；
- `engine_voices` 由适配器新属性 `preset_speakers: list[dict]` 提供（默认空）；
  CosyVoice-300M-SFT 填它自带的 spk 列表；其它引擎为空数组。

### 4.3 F5 工作台页签

- 组件挂载时读 `/api/engines` → 取 `active` 对应 `kind_tags`；`asr` 页签与 TTS 引擎无关，始终可用；
- `tts` 需 `tts`，`clone` 需 `clone`，`design` 需 `design`；
- 不可用页签：按钮置灰 + `title="当前引擎 X 不支持音色设计（仅 Qwen3-TTS 1.7B 支持）"`；
- 若本地记住的页签当前不可用 → 自动切到 `tts` 并 toast 提示一次。

### 4.4 F6 文案

| 位置 | 改前 | 改后 |
| --- | --- | --- |
| 首包等待 | "首次加载 1.7B 模型通常需要 20-60 秒" | "首次加载 {引擎名} 通常需要 {估算} 秒（加载后复用）" |
| 0.6B 回退提示 | "当前使用 0.6B 小模型回退（1.7B 显存不足）" | "当前使用 {引擎名} 回退（{原因}）" |

### 4.5 F7/F8 设置页

- 「本地模型」标题旁 hint 增加"（以下路径仅 Qwen3-TTS 使用）"；
- 非 Qwen 引擎时该分区顶部显示 InlineNote：「当前引擎为 X，它使用自己的模型目录（见「引擎与模型」卡片）」；
- 「模型预热 / 允许 0.6B 回退 / 推理精度 / 显存超额」分区加 note 或置灰：**仅 Qwen3-TTS 生效**。

### 4.6 F9/F10 路由与体检

- 路由文案：`f"{adapter.name} 需要约 {need:.1f}GB 显存，当前可用 {free:.1f}GB"`；
- 体检（安装器）与 `/api/system/selfcheck`：模型项保持 WARN（非致命），文案补"不装 Qwen 也可用其它引擎"。

---

## 5. 非功能需求

- 兼容性：`/api/voices` 只加字段不改字段，老前端不受影响；
- 性能：`/api/engines` 在切换后仍 <200ms（缓存标记过期策略）；
- 可测试性：新增回归测试 `backend/tests/test_engine_agnostic.py`，覆盖 4.1 / 4.2 / 4.5 的后端部分。

---

## 6. 验收标准

- [ ] 点「使用」（含 1.7B ↔ 0.6B ↔ 其它引擎互切）按钮 ≤2s 复位，`/api/engines` 全程 <200ms；
- [ ] 当前引擎为 CosyVoice/VoxCPM/IndexTTS 时，`/api/voices` 的 `builtin[].available` 全为 `false` 且有 `reason`；
- [ ] 当前引擎为 Qwen 时行为与改造前一致（`supported_speakers` 判可用）；
- [ ] 工作台切到 0.6B 时「音色设计」页签置灰且给出原因；切回 1.7B 恢复；
- [ ] 设置页在非 Qwen 引擎下，「模型预热 / 0.6B 回退 / 推理精度」标注"仅 Qwen3-TTS 生效"；
- [ ] 只装 0.6B 或只装 CosyVoice 的机器能完成一次「生成」任务；
- [ ] 回归：`test_engine_sizes.py`、`test_installer_guard.py`、`test_static_checks.py` 全过。

## 7. 风险

| 风险 | 说明 | 处理 |
| --- | --- | --- |
| 引擎预置音色数据缺失 | 只有 CosyVoice-300M-SFT 有 `spk2info.pt`，其它引擎需参考音频 | `engine_voices` 默认为空，UI 显示"该引擎需要参考音频（克隆）" |
| 缓存标记过期导致短暂旧数据 | 切换后第一次刷新前仍显示旧 status | 可接受（<1s），且不会卡住请求 |
| 页签自动跳转打断用户 | 记住的页签不可用被强制切换 | 只跳一次并 toast 说明 |

---

## 8. 实施结果（2026-10-20，全部实测）

| 验收项 | 结果 |
| --- | --- |
| F1 切换按钮 ≤2s 复位 | ✅ 实测 **0.26s** 复位；`select` 接口 0.56s，切换后 `/api/engines` **0.016s** |
| F1 根因修复 | ✅ `invalidate_info()` 改为「时间戳置 0 保留数据」；`all_items()` 有缓存即返回、过期后台刷新 |
| F2 `/api/voices` 带引擎信息 | ✅ `{"id":"qwen3-tts-1.7b","name":"...","capabilities":[...],"is_qwen":true}` |
| F3 非 Qwen 时内置音色不可用 | ✅ 切到 VoxCPM1.5：`builtin[0].available=false`，`reason="内置音色来自 Qwen3-TTS CustomVoice，当前引擎「VoxCPM1.5」不支持…"`；克隆音色仍 `available=true` |
| F4 引擎预置音色 | ✅ 结构已就绪（`engine_voices[]` 读取适配器 `preset_speakers()`；CosyVoice-300M-SFT 装好后即可出现） |
| F5 工作台页签门禁 | ✅ 切到 0.6B：`音色设计 disabled=True`，`title="当前引擎「Qwen3-TTS 0.6B（小显存）」不支持音色设计（仅 Qwen3-TTS 1.7B 支持）"`；`音频转文字` 不受引擎影响 |
| F5 记忆页签自动落位 | ✅ 当前页签被禁用时自动切到可用页签并 toast 说明 |
| F6 文案 | ✅ 首包提示改为「首次加载 {当前引擎名} 模型…」；工作台顶栏新增「引擎：<名称>」chip |
| F7 本地模型说明 | ✅ 设置页「本地模型」顶部 InlineNote：「以下路径**只对 Qwen3-TTS（1.7B / 0.6B）有效**…」 |
| F8 预热/回退说明 | ✅ 「模型预热」hint 改为「仅 Qwen3-TTS 引擎生效；…」；「允许 0.6B 回退」hint 改为「仅 Qwen3-TTS 生效…」 |
| F9 路由文案 | ✅ 改为 `{引擎名} 需要约 X GB 显存，当前可用 Y GB`，并给出「换小规格引擎或上云」建议 |
| 回归测试 | ✅ 新增 `backend/tests/test_engine_agnostic.py`（5 条全过）；`test_engine_sizes.py` / `test_installer_guard.py` / `test_static_checks.py` 仍全过 |

改动文件：

```
backend/app/engine/registry.py     invalidate_info 标记过期（F1）
backend/app/api/voices.py          engine 字段 + 可用性原因 + engine_voices（F2/F3/F4）
backend/app/engine/router.py       显存文案去 1.7B 硬编码（F9）
frontend/src/pages/Workbench.tsx   能力门禁 + 引擎 chip + 文案（F5/F6）
frontend/src/pages/SettingsPage.tsx 本地模型/预热/0.6B 回退说明（F7/F8）
frontend/src/components/TextEditor.tsx 示例文本通用化
backend/tests/test_engine_agnostic.py 新增回归测试
```

---

## 9. 追加：克隆与「不使用参考音色」（2026-10-20 实测修复）

### 9.1 VoxCPM1.5 克隆必失败（PACKAGE_MISSING / MODEL_LOAD_FAILED）

**现象**：切到 `voxcpm1.5` 后任何任务都失败：

```
PACKAGE_MISSING VoxCPM1.5：缺少引擎依赖包（原始错误：ImportError: attempted relative import with no known parent package）
MODEL_LOAD_FAILED ... ValueError: reference_wav_path is only supported with VoxCPM2 models
```

**根因 1（模块名遮蔽）**：`runner.py` 以脚本方式运行，`sys.path[0]` 是 `backend/app/engine/adapters/`，
该目录里的 **`voxcpm.py`（适配器）遮蔽了 venv 里真正的 `voxcpm` 包**，子进程 `from voxcpm import VoxCPM` 实际导入的是适配器文件本身 → 相对导入报错。
→ 修复：`runner.py` 启动时先把自己的目录从 `sys.path` 移除。

**根因 2（API 用错）**：`VoxCPM 1.5` 的 `_generate()` 只接受 `prompt_wav_path + prompt_text`（必须成对），
`reference_wav_path` 是 **V2 专属**；适配器在「只有参考音频、没有参考文本」时误用了它。
→ 修复：
1. `BaseAdapter` 新增 `requires_ref_text`；`VoxCPM15Adapter` 置 `True`；
2. `worker._fill_ref_text_if_needed()` 在缺参考文本时**先用 ASR 自动转写**参考音频（失败给可读指引）；
3. 适配器兜底：缺文本时抛 `INVALID_INPUT`，不再把上游 `ValueError` 包装成「模型加载失败」。

### 9.2 必须选一个参考音色 → 改为可选（纯文本合成）

**现象**：工作台 TTS 页签强制要求选音色；非 Qwen 引擎下内置音色又全是「不可用」，导致"用其他模型必然报错"。

**修复（前后端）**：

| 层 | 改动 |
| --- | --- |
| API | `POST /api/tasks` 支持 `voice_id = "none"`，表示「纯文本合成，不提供参考音色/参考文本」 |
| worker | 无音色、无参考音频且类型为 tts → 直接走纯文本合成（不再要求音色） |
| 前端 VoicePicker | 当当前引擎**没有任何可用内置音色**时，顶部出现「纯文本合成（不使用音色）」卡片 |
| 前端 Workbench | 非 Qwen 引擎允许不选音色提交；禁用原因文案改为「请选择音色（或选「纯文本合成」）」 |

**实测**：

```
active=voxcpm1.5，voice_id="none" → 任务 e59365359d70 completed，音频 4.63s，RTF 0.071
界面：切到 VoxCPM1.5 后「纯文本合成（不使用音色）」卡片出现；
      选中 + 输入文本 → 「开始生成」按钮从禁用变为可用
```

### 9.3 选中不可用内置音色 → 产出近乎静音的音频（-45.8 dBFS）

**现象**：VoxCPM1.5 + 内置音色 uiltin-vivian 生成成功，但音频 peak=0.0052 (-45.8 dBFS)，听起来啥都没有。

**根因**：内置音色是 Qwen CustomVoice 的 speaker 概念，其它引擎没有；模型在没有 prompt 的情况下被错喂参数，输出近乎静音。

**修复**：
1. BaseAdapter.supports_builtin_speakers = False，Qwen 系置 True；
2. worker 在「有内置音色 + 引擎不支持」时**直接给可读错误**（不再静音）；
3. 前端：当前引擎不支持内置音色时，把上次记住的音色自动换成「纯文本合成」并提示一次。

**实测**：uiltin-vivian + voxcpm1.5 → ailed，错误「当前引擎不支持 Qwen 内置音色…请选择纯文本合成或克隆音色」；
oice_id=none 同引擎 → completed，峰值 **0.399（-8.0 dBFS）** 音量正常。

### 9.4 关闭流式后仍显示「生成中／首包」

**现象**：高级参数里关掉流式，结果已完成，界面仍显示「生成中：已完成 0/5 字，已耗时 72s」和「首包 79.15 s」。

**修复**：
- 新增 showStreamUi = stream.streaming && !params.streaming；
- 进度提示只在 stream.streaming 时渲染（完成即消失）；
- 关闭流式时：不显示「首包」用时、骨架文案改为「正在生成（未启用流式），完成后自动播放」、进度条不再 indeterminate；
- 回退提示文案改为使用当前引擎名。

**实测**：完成态界面不再出现「生成中」文本（Playwright 断言 仍在显示『生成中』: False）。

### 9.5 音色卡片布局

固定行高 176px 让内容少的自定义音色出现大片空白；改为内容驱动（grid-auto-rows: auto + min-height: 132px），
「纯文本合成」伪音色改用独立的 .voice-plain 整行区块（高度 85px），不再参与等高网格。

**实测**：卡片高度集合 [153, 177]（同排对齐）、伪音色块 85px、页面横向溢出 0。

### 9.6 主界面「抽搐」（无限重渲染）

**现象**：选过「纯文本合成」后，工作台界面持续抖动。

**根因**：两个 effect 互相改写同一个状态，形成死循环：

1. 「上次记住的音色被删除」effect 发现 oiceId = 'none' 不在音色列表里 → 回落到内置音色；
2. 「当前引擎不支持内置音色」effect 发现该内置音色 vailable=false → 又改回 'none'；
3. 1↔2 每帧反复 → React 无限重渲染 → 界面抽搐。

**修复**：
- 两个 effect 都先判断 oiceId === NO_VOICE_ID 直接返回（伪音色不参与已删除判定）；
- 回落目标改为「第一个可用音色」，没有可用音色时落到 NO_VOICE_ID；
- 切换提示加 useRef 去重，只弹一次。

**实测**（MutationObserver 统计 5 秒内 DOM 变更次数，抖动时会是几百次）：

| 引擎 | 5 秒内 DOM 变更 | 记住的音色 |
| --- | --- | --- |
| voxcpm1.5 | **1 次** | 
one |
| qwen3-tts-1.7b | **0 次** | uiltin-vivian |
