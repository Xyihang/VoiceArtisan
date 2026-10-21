# 前端设计说明

## 1. 设计定位

**音频工具的调性**：干净、克制、有秩序。每个页面只有一个视觉焦点（工作台的「开始生成」、结果播放器），
其余元素退居次要；层级靠**间距 + 字重 + 颜色**建立，不依赖多字体、多颜色或渐变霓虹。

## 2. 设计令牌（唯一数值来源）

所有颜色、间距、圆角、阴影、动效均来自 `frontend/src/styles/tokens.css`，页面与组件样式中不出现散落数值。

### 2.1 颜色角色

| 令牌 | 浅色 | 深色 | 用途 |
| --- | --- | --- | --- |
| `--color-bg` | `#f6f7f9` | `#0c1017` | 页面底色 |
| `--color-surface` | `#ffffff` | `#141a22` | 卡片 / 面板 |
| `--color-surface-nested` | `#f1f3f6` | `#1a222c` | 嵌套区域（参数组、上传区） |
| `--color-border` / `--color-border-strong` | `#e2e6ec` / `#cfd6df` | `#242e3a` / `#35414f` | 描边 / 强调描边 |
| `--color-text` | `#131820` | `#e6edf3` | 主文本 |
| `--color-text-secondary` | `#5a6672` | `#96a3b2` | 次要文本 |
| `--color-text-muted` | `#8b96a3` | `#6f7c8a` | 弱化提示 |
| `--color-primary` / `--color-primary-hover` | `#1f6feb` / `#1859c4` | `#4c8dff` / `#6ba0ff` | 主色 / 悬停 |
| `--color-accent` | `#0d9488` | `#2dd4bf` | 强调（显存、进度） |
| `--color-success` / `--color-warning` / `--color-error` | `#15803d` / `#b45309` / `#c62828` | `#3fb950` / `#d29922` / `#f2555a` | 状态色 |
| `--color-on-primary` | `#ffffff` | `#07121f` | 主色上的文字 |
| `*-soft` | 同色 10%~18% 透明度 | 同上 | 标签/提示底色 |

对比度：正文 `--color-text` 对 `--color-bg` ≥ 12:1；次要文本 ≥ 4.8:1；状态标签文字对 soft 底色 ≥ 4.5:1。

### 2.2 排版

| 令牌 | 值 |
| --- | --- |
| `--font-ui` | `-apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", …` |
| `--font-mono` | `ui-monospace, "SF Mono", Menlo, Consolas, …` + `font-variant-numeric: tabular-nums` |
| 字号阶梯 | 12 / 13 / 14 / 16 / 20 / 28 |
| 行高 | 1.5（`--lh-base`） |
| 字重 | 400 / 500 / 600 |

数字、时长、延迟、RTF 一律使用 `.num`（等宽 + tabular-nums），避免刷新时跳字。

### 2.3 空间与形状

| 令牌 | 值 | 说明 |
| --- | --- | --- |
| `--space-1…7` | 4 / 8 / 12 / 16 / 24 / 32 / 48 | 唯一间距刻度 |
| `--radius-sm` / `--radius-md` / `--radius-full` | 8 / 12 / 999 | 圆角 |
| `--shadow-1` / `--shadow-2` | 两级柔和阴影 | 深色主题下 `--shadow-1` 置为 none，改用描边 |
| `--shell-rail` / `--shell-topbar` | 216 / 60 | 结构尺寸 |

### 2.4 动效

| 令牌 | 值 |
| --- | --- |
| `--duration-fast` / `--duration-base` / `--duration-slow` | 150 / 200 / 250 ms |
| `--ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` |

`prefers-reduced-motion: reduce` 时全部时长归零（动效降级为逐帧切换）。

### 2.5 图标

统一使用 `lucide-react`，线宽 `1.75`，尺寸 12/13/14/16/18/22（按层级），不使用 emoji。

### 2.6 主题

默认跟随系统（`prefers-color-scheme`），顶栏按钮循环切换「跟随系统 → 浅色 → 深色」，选择写入 `localStorage`，
并通过 `document.documentElement.dataset.theme` + `color-scheme` 同步浏览器原生控件。

## 3. 信息架构

| 页面 | 路由 | 职责 | 主焦点 |
| --- | --- | --- | --- |
| 工作台 | `#/workbench`（默认） | 4 类能力入口、文本/音色/参考音频/音色设计、参数、生成与流式播放 | 「开始生成」按钮 + 结果播放器 |
| 音色库 | `#/voices` | 内置 9 音色 + 自定义音色：搜索、筛选、试听、上传、重命名、删除 | 音色卡片网格 |
| 任务中心 | `#/tasks` | 列表、筛选、排序、分页、行展开日志、批量删除、重试、下载 | 任务表格 |
| 设置 | `#/settings` | 运行模式、云端连接与密钥、SSH 隧道、ASR、模型路径、默认参数 | 保存按钮 |
| 状态与日志 | `#/status` | 后端健康、显存、首包/RTF 统计、自检项、最近日志 | 健康卡片 |

顶栏固定包含：后端状态指示（本地/云端/离线，颜色 + 文字双编码，云端附带实时延迟）、模式徽标、主题切换。

## 4. 组件清单

| 组件 | 文件 | 说明 |
| --- | --- | --- |
| `AppShell`（`Shell`） | `App.tsx` | 左侧导航 + 顶栏 + 路由，移动端底部标签栏 |
| `BackendStatus` | `components/BackendStatus.tsx` | 后端状态双编码 + 延迟 |
| `ThemeToggle` / `Toasts` | `components/Chrome.tsx` | 主题切换、Toast 通知 |
| `TextEditor` | `components/TextEditor.tsx` | 自动增高、字数/预估时长、进度提示、清空、插入示例 |
| `VoicePicker` | `components/VoicePicker.tsx` | 卡片网格、波形缩略、标签（性别/年龄/语言/方言/口音）、搜索与筛选、选区、自定义管理 |
| `RefAudio` | `components/RefAudio.tsx` | 拖拽上传 + 点击选择 + 浏览器录音、波形、选区裁剪与试听、隐私确认 |
| `DesignInput` | `components/DesignInput.tsx` | 自然语言描述 + 快捷标签（性别/年龄/语速/情感/口音/质感） |
| `ParamsPanel` | `components/ParamsPanel.tsx` | 可折叠高级参数（语速/音高/温度/TopP/TopK/重复惩罚/步数/格式/语言）+ 恢复默认 |
| `AudioPlayer` / `useAudioEngine` | `components/Player.tsx` | 波形、播放/暂停、进度拖拽、时长、下载、首包/模型标签 |
| `Waveform` | `components/Waveform.tsx` | Canvas 波形，随流式分块增长，点击/键盘定位 |
| `ui.tsx` | `components/ui.tsx` | `SectionCard`/`Field`/`StatusBadge`/`ProgressBar`/`Meter`/`EmptyState`/`Skeleton`/`InlineNote`/`Spinner`/`Modal`/`KV` |
| `useTaskStream` | `hooks/useTaskStream.ts` | 订阅 `/ws/tasks/{id}`，把 wav 分块喂给 Web Audio，记录首包时间 |

## 5. 关键交互

### 5.1 流式播放

1. 创建任务后立即打开 `WS /ws/tasks/{id}`；
2. 第一段 `task.chunk` 到达 → 解码 → 立刻 `play()`，同时记录首包耗时（毫秒）；
3. 后续分块解码后**排队追加**（`AudioBufferSourceNode` 按时间轴调度，避免缝隙与断续）；
4. 波形每段追加峰值，随音频到达实时增长；
5. 取消：`POST /tasks/{id}/cancel`，后端保留已生成片段并标注「已截断」，前端显示黄色提示。

### 5.2 状态可见性

- 顶栏：后端状态 + 云端延迟，2.5s 健康推送；断线 3s 内变红（WS 断开 + 心跳超时）；
- 工作台右侧「运行状态」：当前后端、首包、耗时、RTF、音频时长、显存；
- 任务中心：状态标签（排队/运行中/已完成/失败/已取消）实时更新，不使用轮询；
- 生成中按钮变为「取消生成」并显示实时计时。

### 5.3 失败与回退

- 失败展示可读 `message + hint + 错误码`，不展示堆栈；
- `VRAM_INSUFFICIENT` / `CLOUD_UNREACHABLE` 等可切换错误提供「一键切换到云端」按钮（创建重试任务，`force_backend=cloud`）。

### 5.4 首次加载与空状态

- 首屏渲染骨架屏（`Skeleton`），无布局抖动；
- 音色/任务/结果为空的场景均有 `EmptyState` 与下一步动作；
- 后端离线显示可操作错误提示（如何启动服务、如何信任证书）。

## 6. 响应式

| 断点 | 布局 |
| --- | --- |
| ≥1280px | 双栏：左配置（能力/文本/音色/参数/主按钮）、右结果与状态 |
| 768–1279px | 单栏堆叠，顺序：配置 → 主按钮 → 结果 → 状态 |
| <768px | 单栏 + 底部标签栏导航；主按钮固定在底部安全区（`env(safe-area-inset-bottom)`） |

## 7. 无障碍

- 全部交互可键盘完成：可见 `:focus-visible` 描边（未移除 outline）；
- 表单控件均有 `label`/`aria-label`、`aria-describedby`；
- 波形可聚焦，`role="slider"` + 左右方向键定位；
- Tab 使用 `role="tablist"`/`aria-selected`；折叠面板 `aria-expanded`/`aria-controls`；
- 状态徽标 `aria-live="polite"`；Toast `role="status"`；
- 尊重 `prefers-reduced-motion`。

## 8. 性能

- 生产构建：JS ≈ 248KB（gzip 78KB）、CSS ≈ 25KB（gzip 5KB），本地首屏 < 2s；
- 无 UI 框架依赖（仅 React + lucide-react），波形用 Canvas 绘制；
- 任务状态走 WebSocket 推送，**无轮询**；日志页 4s 定时拉取（非任务状态）。

## 设计规范来源与外部参考（不随包分发）

本项目的视觉与交互标准整理在 [INTERACTION_GUIDELINES.md](INTERACTION_GUIDELINES.md)。
制定过程中参考了两份公开的 MIT 许可前端设计规范（`design-taste-frontend`、`ui-craft`，含其 dense-dashboard 子技能），
用于校准「排版阶梯 / 间距节奏 / 语义色 / 动效克制 / 键盘优先 / QA 闸门」这些通用原则。

**它们只作为写作参考，源码与素材不包含在本项目内，也不随备份或安装包分发**（下载缓存位于 `.cache/`，已在打包时排除）。
本项目未复制其任何代码或素材，界面中的令牌、组件与布局均为本项目自研。

### 本轮 UI 改造清单

| 项 | 内容 |
| --- | --- |
| 键盘优先 | `Ctrl/⌘+K` 命令面板（页面/操作/引擎三组）、`Ctrl+Enter` 提交、`j/k` 行导航、`r/d/Delete` 行操作、`Esc` 关闭 |
| 任务中心 | 行内 6 个操作、筛选写 URL、可移除 chips、批量全选/重试失败/删除、骨架行、错误码徽章、`Page N of M` |
| 工作台 | 禁用原因就近提示、记忆音色与参数、失败重试+复制错误、长文本提示开流式、右栏「最近任务 + 快捷键」填充留白 |
| 设置页 | 左侧锚点导航（悬浮视口高、滚动高亮）、分区搜索 + 命中高亮、云端「测试连接」、字段校验、危险操作 ConfirmDialog（列影响面） |
| 状态页 | 自动刷新开关、日志级别 chips（写 URL）、复制日志、健康卡片语义状态点 + 异常「去设置」 |
| 音色库 | 一屏克隆三步、搜索 + 排序、hover 操作、内置音色不可删（禁用 + 原因） |
| 自适应 | 任务中心一屏占满（表格内滚 + 分页贴底）；`page--fill` 只对任务页生效，其它页保持整页滚动；333/760px 零横向滚动 |
| 弹层 | `Modal` 走 `ReactDOM.createPortal(document.body)` + `position: fixed` 居中 + 打开时锁 `body` 滚动（补偿滚动条宽度） |
