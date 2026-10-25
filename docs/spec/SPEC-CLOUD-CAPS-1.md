# 规格：纯云端模式下能力门禁错判（SPEC-CLOUD-CAPS-1）

> 状态：**待评审（未实现）**
> 报告问题：云端有 1.7B 全模型、本地只有 0.6B、运行模式＝纯云端时，点「音色设计」无法设计。
> 影响版本：v1.1.24 及以前
> 关联代码：`frontend/src/pages/Workbench.tsx`、`backend/app/engine/router.py`、`backend/app/engine/adapters/qwen06.py`

---

## 1. 需求澄清清单

### 核心诉求
运行模式决定"谁来干活"，那么**能力门禁也必须跟模式走**：纯云端时以云端模型为准，不能再用本地引擎的能力把云端能力挡掉。

### 目标用户
本地小显卡（4GB）或没装 1.7B、但云端有全套 1.7B 的用户。

### 使用场景
- 模式＝纯云端 → 工作台「音色设计」应可用
- 模式＝混合 → 本地不支持的能力应自动走云端（而不是禁掉）
- 模式＝纯本地且确实不支持 → 才禁用，并说明原因与出路

### 功能范围

**Must Have**
1. 纯云端模式：TTS / 克隆 / 音色设计 / 转写 的能力判定基于**云端配置**，不受本地引擎限制
2. 混合模式：能力 = 本地能力 ∪ 云端能力
3. 纯本地模式：保持现状（按本机 active 引擎 `kind_tags`），但提示里要说清"可切云端"
4. 能力判定有**单一真源**（后端算好给前端用），不再让前端自己拼
5. 云端模型名缺失时要给明确错误（不能把空 model 发给云端）

**Should Have**
6. 顶部状态 chip 显示真实来源（本地引擎名 / 云端模型名），而不是恒定显示本地 0.6B
7. 提示文案区分"本地不支持"和"云端没配"

**Nice to Have**
8. 批量面板的能力相关选项按同一真源置灰
9. 教程页补充"云端 1.7B 才有的能力"说明

**Won't Have**
- 不改后端路由策略（`router.plan()` 逻辑本身是对的）
- 不做"自动降级到云端后再回来重试"的新机制
- 不改本地引擎的 `kind_tags` 定义

### 技术约束
- 不改数据库（无表结构变更）
- 前端不新增路由/页面，只改门禁与文案
- 后端新增字段必须向后兼容（旧前端忽略即可）

### 待确认问题
- [ ] 纯云端 + 云端 `cloud.models.voice_design` 为空时：**禁用页签**还是**允许点击后报错**？（建议：禁用并提示去设置填模型名）

---

## 2. 根因分析（已定位）

**前端单点门禁，用的是本地引擎能力。**

`frontend/src/pages/Workbench.tsx`

```ts
// 169-195：只读本机 /api/engines 的 active 引擎
const active = res.items.find((item) => item.id === res.active);
setEngineCaps({ id: active.id, name: active.name, caps: active.kind_tags || ['tts'] });

// 198-208：能力不足 → 自动踢回 tts 并 toast
if (!need || engineCaps.caps.includes(need)) return;
setTab(next);  // design → tts

// 626-632：生成按钮同样被 blocked 拦掉
const blocked = !!need && !engineCaps.caps.includes(need);
```

而 `backend/app/engine/adapters/qwen06.py:23` 声明：

```python
kind_tags = ["tts", "clone"]      # 0.6B 没有 design
```

于是：本地 active＝`qwen3-tts-0.6b` → `caps=['tts','clone']` → `design` 被前端拦死，**与 `run.mode=cloud` 无关**。

后端其实是好的：

```python
# engine/router.py:146-163  纯云端 → primary=cloud，不做本地可行性判断
if mode == "cloud":
    return Plan("cloud", None, "仅云端模式")

# engine/router.py:63-66  能力校验只在 primary=="local" 时生效
if plan.primary != "local":
    return
```

`engine/cloud.py:293-305` 也把 `kind="design"` + `design` 文本 + `model=cfg.models.voice_design` 正确发给了云端。

**结论：修复点在前端能力判定，把它从"本地引擎能力"改成"按模式计算的有效能力"。**

---

## 3. 同类问题排查结果

| # | 位置 | 问题 | 严重度 |
| --- | --- | --- | --- |
| 1 | `Workbench.tsx:198-208` | 纯云端/混合下，`design` 被本地 0.6B 拦掉（本报告问题） | P0 |
| 2 | `Workbench.tsx:626-632` | 同上，生成按钮 `blocked` 也会拦（即使页签切回来也无法提交） | P0 |
| 3 | `Workbench.tsx:374` | `engineIsQwen` 判断基于本地引擎 id，用于 instruct 输入框显隐 → 本地非 Qwen 引擎 + 云端 1.7B 时，instruct 被误隐藏 | P1 |
| 4 | `Workbench.tsx:598-601` | 状态 chip 恒显示本地引擎名，纯云端时误导（用户以为是本地在跑） | P1 |
| 5 | 混合模式（hybrid） | 本地不可用时后端会自动切云端，前端仍按本地门禁 → 克隆/设计被禁 | P1 |
| 6 | `BatchPanel.tsx:47` | 批量面板的「音色设计」选项不做能力过滤 → 选了才在提交后被后端拒（`ENGINE_CAPABILITY_MISSING`） | P2 |
| 7 | `backend/app/api/meta.py`（`task_types`） | 恒返回 4 种任务类型，与页签门禁不一致（页签禁用但类型列表显示可用） | P2 |
| 8 | `docs/` 与 `GuidePage.tsx:299` | 文案写"仅 1.7B 提供"，未说明"云端 1.7B 也行" | P3 |
| 9 | 云端模型名留空 | `cloud.models.voice_design=""` 时会把空 model 发给云端，报错含糊 | P1 |

---

## 4. 功能规格

### 4.1 单一真源：后端计算"有效能力"

新增模块 `backend/app/capabilities.py`：

```
def effective_capabilities(cfg, registry, mode) -> dict:
    local_caps  = registry.capabilities(registry.active_id())      # 本地 active 引擎
    cloud_caps  = caps_from_cloud_models(cfg.cloud.models)         # 非空即具备
    if mode == "cloud":  source="cloud";  caps = cloud_caps
    elif mode == "local": source="local"; caps = local_caps
    else:                source="both";   caps = local_caps | cloud_caps
    return {source, caps, tts, clone, design, asr, detail}
```

`caps_from_cloud_models` 规则：

| 能力 | 条件 |
| --- | --- |
| `tts` | `cloud.models.base` 或 `cloud.models.custom_voice` 非空 |
| `clone` | `cloud.models.custom_voice` 或 `cloud.models.base` 非空 |
| `design` | `cloud.models.voice_design` 非空 |
| `asr` | 恒 true（ASR 独立配置 `asr.prefer`） |

暴露方式：`GET /api/meta` 新增字段 `capabilities`（旧字段不变）：

```json
{
  "capabilities": {
    "source": "cloud",
    "caps": ["tts", "clone", "design"],
    "detail": "云端模型：Base / CustomVoice / VoiceDesign",
    "missing": []
  }
}
```

### 4.2 前端改造

| 位置 | 改动 |
| --- | --- |
| `Workbench.tsx` | 删除本地 `listEngines` 门禁，改用 `meta.capabilities.caps`；`engineCaps.name` 改为展示用标签（`source=cloud` 时显示"云端 · VoiceDesign 1.7B"） |
| `Workbench.tsx:374` | `engineIsQwen` 改为 `capabilities.caps.includes('design') || caps.includes('clone')`（不再靠引擎 id 猜） |
| `Workbench.tsx:626-632` | `blocked` 用新 caps；提示分两种：本地不支持 → "可切到云端"；本地+云端都没有 → "去设置填云端模型名" |
| `BatchPanel.tsx` | 用同一 `meta.capabilities` 决定「音色设计」选项是否可选 |
| 页签自动回退 | 仅当新 caps 里确实没有该能力时才回退，且 toast 文案带上 `source` |

### 4.3 后端兜底

`engine/cloud.py`：`kind` 对应的 `cloud.models.<kind>` 为空时，抛

```
CLOUD_MODEL_UNSET: 云端未配置 <kind> 模型名，请到「设置 → 云端连接」填写
```

避免把空 `model` 发给云端导致 400 含糊报错。

---

## 5. 异常处理

| 场景 | 期望行为 |
| --- | --- |
| 云端模型名全空 + mode=cloud | 页签全禁用 + 明确提示"云端模型未配置"，提供跳转设置按钮 |
| 云端地址未配置 + mode=cloud | 沿用现有 `CLOUD_UNREACHABLE`（plan 阶段已有），前端提示去「云端连接」 |
| `/api/meta` 拿不到 | 前端退回旧逻辑（按本地引擎），不阻塞生成 |
| 模式热切换 | 切完模式后 15 秒内（现有轮询）或立即（settings 保存回调）刷新 `meta` |

---

## 6. 验收标准

**功能**
- [ ] 云端全 1.7B + 本地仅 0.6B + `mode=cloud`：音色设计页签可点、可提交、**任务成功产出音频**（`backend=cloud`）
- [ ] 同配置 + `mode=hybrid`：设计可用（走云端）
- [ ] 本地仅 0.6B + `mode=local`：设计页签仍禁用，提示"本地 0.6B 不支持音色设计，可切云端"
- [ ] 云端 `voice_design` 留空 + `mode=cloud`：设计禁用，提示去设置填模型名
- [ ] 状态 chip：`source=cloud` 显示云端模型，`source=local` 显示本地引擎
- [ ] instruct 输入框在"云端 1.7B"下可见、在"本地 0.6B"下隐藏

**非功能**
- [ ] `pytest` 全绿，新增 `test_capabilities.py` 覆盖 6 种组合（cloud/cloud-full、cloud/cloud-empty、local/qwen06、local/qwen17、hybrid/两者有、hybrid/两者无）
- [ ] `npx tsc --noEmit` 与 `vite build` 通过
- [ ] 浏览器手点：0 JS 异常、toast 文案正确

**回归**
- [ ] 本地 1.7B + mode=local 的设计、克隆、TTS 行为不变
- [ ] 纯云端 TTS/克隆不受影响

---

## 7. 风险与依赖

| 风险 | 对策 |
| --- | --- |
| `meta` 新增字段与旧前端不兼容 | 只增不改；前端读取时做存在性判断 |
| 模式热切换时能力短暂不同步 | 保存设置成功后主动 reload `meta` |
| 云端模型名与实际不符（填了但云端没有） | 提交后由云端返回错误，前端把 `detail` 原样展示（不额外猜） |
| 能力判定与后端 `router` 逻辑漂移 | `capabilities.py` 直接复用 `registry.capabilities` 与 `cfg.cloud.models`，不另写一套规则 |

---

## 8. 实施拆解（评审通过后执行）

| 步骤 | 文件 | 说明 |
| --- | --- | --- |
| 1 | `backend/app/capabilities.py`（新增） | 有效能力计算 + 单测 |
| 2 | `backend/app/api/meta.py` | `/api/meta` 增 `capabilities` 字段 |
| 3 | `backend/app/engine/cloud.py` | `CLOUD_MODEL_UNSET` 兜底 |
| 4 | `backend/app/errors.py` | 新增错误码与中文提示 |
| 5 | `frontend/src/types.ts` / `lib/api.ts` | `meta.capabilities` 类型 |
| 6 | `frontend/src/pages/Workbench.tsx` | 门禁改真源、chip、instruct、toast 文案 |
| 7 | `frontend/src/components/BatchPanel.tsx` | 设计选项按能力置灰 |
| 8 | `docs/CONFIG.md` / `GuidePage` | 文案补"云端 1.7B 同样支持设计" |
| 9 | 验证 | pytest + tsc + build + 真机（cloud / hybrid / local 三态各一次） |
