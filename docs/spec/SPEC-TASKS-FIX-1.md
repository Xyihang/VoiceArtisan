# 规格：任务中心 / 工作台 四个阻断级缺陷修复（SPEC-TASKS-FIX-1）

> 状态：**已定位根因，待实施**
> 触发：用户实测反馈 4 个阻断问题（v1.1.45）
> 范围：`backend/app/worker.py`、`backend/app/api/tasks.py`、`frontend/src/pages/TasksPage.tsx`、`frontend/src/pages/Workbench.tsx`、`frontend/src/styles/{layout,components}.css`

---

## 1. 需求澄清清单

### 核心诉求
任务中心「看不见、取消没用、重试不能选本地」，工作台「一开始生成就没法追加任务」——这四条都直接阻断正常使用。

### 功能范围

**Must Have（P0）**
1. 任务列表在本机（1920×1080 + 125% 缩放）能看到至少 6 行
2. 取消任务：队列中立刻变「已取消」；运行中立刻显示「取消中…」，并在 1 个分块内真正停下
3. 重试：默认沿用**原任务的后端**，并额外提供「本地重试」「云端重试」两个明确入口
4. 工作台：生成过程中「开始生成」按钮**始终存在且可点**（追加任务），旁边另给「停止」按钮

**Should Have（P1）**
5. 取消/重试的反馈写进 toast + 行内状态，不静默
6. 表格区最小高度兜底（窗口再小也能看到几行 + 滚动）

**Won't Have**
- 不改任务数据结构（不加数据库列）
- 不改批量接口语义
- 不做多任务并发（仍单 worker）

---

## 2. 根因分析（已实测/读码确认）

| # | 现象 | 根因 |
| --- | --- | --- |
| 1 | 取消像摆设 | `worker.cancel()` 对**运行中**任务只 `event.set()`，不写库、不推事件 → 界面几分钟没反应；`_execute` 取出任务时也不复查 `status`，排队中被取消的任务仍会被执行 |
| 2 | 只能云端重试 | `api/tasks.py::retry_task` 里 `backend="cloud" if force == "cloud" else "local"` —— 默认**硬编码 local**，原任务是云端也不会沿用；UI 也只暴露了一个「重试」按钮，没有本地/云端两个入口 |
| 3 | 无法追加任务 | `Workbench.tsx` 的 `primary-action` 是**三元互斥**：`stream.streaming ? 取消生成 : 开始生成` → 生成期间"开始生成"整块被替换掉 |
| 4 | 列表啥都看不见 | `.page--fill` 高度 `calc(100vh - topbar - 48px - var(--dock-safe-bottom))` **已经**扣掉 Dock 安全区，而 `.shell .page` 又统一加了 `padding-bottom: var(--dock-safe-bottom)`（124px）→ 双重扣减。实测 900px 高窗口下表格可滚动区仅 **184px ≈ 3 行**；125% 缩放的 1080p 只剩约 1 行 |

实测数据（headless，浅色主题）：

```
1440×900  → page 584px / 表格卡 280px / 表格可视 184px / 12 行
1920×1080 → page 764px / 表格卡 460px / 表格可视 365px / 12 行
```

---

## 3. 功能规格

### 3.1 取消任务

- **输入**：`POST /api/tasks/{id}/cancel`
- **处理**：
  - 排队中（queued）→ 立刻写 `status=canceled`、`error_code=CANCELED`、`finished_at`，推事件
  - 运行中（running）→ 置取消事件 **且**写日志「已请求取消，正在收尾」+ 推事件；worker 每处理完一个分块就检查事件，命中则中止并落库 `canceled`（保留已生成片段，标注「已截断」）
  - 已结束（completed/failed/canceled）→ 返回 `{ok:false, reason:"already_finished"}`，前端提示「任务已结束」
- **异常**：任务不存在 → 404

### 3.2 重试

- **默认**：`force_backend` 缺省时沿用**原任务的 backend**（`row.backend`），不再硬编码 local
- **显式**：`force_backend = "local" | "cloud"`
- **前端入口**（行操作 + 详情弹层都要有）：
  - 「重试」= 原后端
  - 「本地重试」/「云端重试」= 指定后端
  - 快捷键 `R` 仍等于「重试（原后端）」

### 3.3 工作台按钮

```
生成中：[开始生成（追加）]  [停止]
空闲：  [开始生成]
```
- 生成中点「开始生成」= 用当前表单再建一个任务（追加），不需要等前一个结束
- 「停止」只停当前流，不影响已提交任务

### 3.4 列表高度

- `.shell .page--fill` 不再吃 `--dock-safe-bottom` 的 padding（高度公式里已扣过）
- `.card--fill .table-wrap { min-height: 280px }` 兜底
- 筛选卡在窗口高度 < 820px 时可折叠为一行（P1，可后做）

---

## 4. 验收标准

- [ ] 1920×1080（含 125% 缩放）任务中心至少可见 **6 行**，且能滚动
- [ ] 取消排队任务：点击后 ≤1s 行状态变「已取消」
- [ ] 取消运行中任务：行内出现「取消中…」，≤1 个分块时间后变「已取消」，日志含「已请求取消」
- [ ] 云端任务点「重试」→ 新任务 backend 仍为 cloud；点「本地重试」→ backend 为 local
- [ ] 本地任务点「云端重试」→ backend 为 cloud
- [ ] 生成过程中「开始生成」可点，点击后任务数 +1，当前流继续播放
- [ ] 生成过程中「停止」可点，点击后当前流停止、按钮回到「开始生成」
- [ ] `tsc` 0 错、`vite build` 通过、`pytest` 全绿、控制台 0 error

## 5. 风险

| 风险 | 说明 | 对策 |
| --- | --- | --- |
| 取消粒度受单块合成限制 | 单块可能几十秒 | 事件在每个分块前后都检查；云端 HTTP 流式请求支持中断 |
| 旧任务 `backend` 字段为空 | 历史数据 | 空时回退 `local`，并在日志注明 |
| 表格 min-height 撑破小窗口 | 极端 720p | min-height 用 `clamp(220px, 40vh, 320px)` |
