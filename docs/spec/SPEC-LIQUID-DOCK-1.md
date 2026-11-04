# 规格：液态玻璃 Dock 主导航 + 去侧栏布局（SPEC-LIQUID-DOCK-1）

> 状态：**待评审（未改生产代码）**
> 视觉参考：`.cache/preview/glass-dock-demo-liquid.html`（已确认可接受的那版 demo）
> 影响范围：`frontend/src/App.tsx`（Shell）、`frontend/src/styles/layout.css`、`components.css`、`components/Chrome.tsx`（可选）、新增 `components/DockNav.tsx`
> 不改：后端、路由表模型、`CommandPalette` 的数据源

---

## 1. 需求澄清清单

### 核心诉求
把现在的「左侧栏 + 顶部标题栏」导航，换成**底部液态玻璃 Dock**；页面内容全宽，品牌移到内容区左上；搜索从顶栏收进 Dock 右侧的独立圆形按钮。

### 目标用户
桌面端为主（1440×900 ~ 1920×1080）的本地使用者；偶尔在 1366×768 笔记本使用。

### 使用场景
1. 在工作台/音色库/任务中心/设置/状态之间切换（主要动作）
2. 按 `Ctrl/⌘ + K` 或点 Dock 右侧圆钮搜索，跳到某个页面或某个设置分区
3. 有任务在跑时，任务中心图标上显示角标

### 功能范围

**Must Have（P0）**
1. 移除左侧 `rail`，内容区全宽；品牌块（声匠 / VoiceSmith）移到内容区左上
2. 底部居中 Dock：5 项（工作台 / 音色库 / 任务中心 / 设置 / 状态与日志），图标 + 文字
3. 选中项有**玻璃胶囊**；点击任意 tab，胶囊从当前位置**平滑滑动**过去（约 520ms）
4. **按住胶囊**：胶囊放大、上下凸出 Dock 边界，边缘 1–2px 出现折射（SVG 位移贴图）+ 彩色色散；松手 520ms 回落
5. **只有按住胶囊**才能拖动胶囊（跟手，松手吸附最近 tab）；按住 Dock 其它位置拖动不触发
6. Dock 右侧**独立圆形按钮 = 搜索**，点击打开现有命令面板（等价 `Ctrl K`）
7. Dock 内搜索输入框删除；顶栏的搜索按钮 `CommandButton` 同步移除（避免两处入口）
8. 任务中心保留运行中任务数角标

**Should Have（P1）**
9. 键盘可达：Dock 为 `role="tablist"`，`←/→` 在项间移动并同步胶囊；`Enter/Space` 激活
10. `prefers-reduced-motion: reduce`：胶囊滑动降为 150ms 淡入式位移，按住放大改为 120ms 直接到位
11. 窄屏（<1100px）：Dock 收成图标为主（文字隐藏），圆钮保留；内容区左右 padding 收紧
12. 深色/浅色都使用项目 token（`--color-primary` / `--color-accent` / `--glass-*` 新 token）
13. 页脚（Made by / 版权）从 rail 移到内容区底部，保持可见

**Nice to Have（P2）**
14. Dock 跟随路由变化时，若用户已在目标页则不重复动画
15. 按住胶囊时的粒子/高光扫光动效（可后续迭代）

**Won't Have（明确不做）**
- ❌ 不改 `CommandPalette` 的搜索数据源、排序与快捷键逻辑（只换触发入口）
- ❌ 不做 Dock 自定义排序 / 拖拽换位 / 收藏
- ❌ 不做移动端底部 TabBar 专属布局（窄屏只做"图标为主"的降级）
- ❌ 不在 Dock 里放"教程"（教程入口移到顶栏右侧或页脚，见待确认 3）

### 技术约束
- 技术栈不变：React 18 + TS + Vite，**不引入新依赖**；SVG 位移贴图用原生 Canvas + `<svg><filter>`
- 现有路由是 `#/route`（`App.tsx:39 readHashRoute`），Dock 只调用 `navigate(id)`，不新造路由
- 样式继续走 `tokens.css` 变量；新增玻璃 token 需在浅/深两套 `:root` 里都定义
- 布局骨架类名保持 `.shell / .main / .topbar`，避免其它页面样式失配
- 现有页面内边距/最大宽度不能被破坏（`--content-max: 1480px`）

### 待确认问题
1. **教程入口**：现在在 rail 底部（`RAIL_BOTTOM_ROUTES`）。Dock 只有 5 格，教程放哪？（建议：顶栏右侧一个小图标按钮）
2. **顶栏是否保留**：去掉侧栏后顶栏只剩标题 + 模式徽标 + 后端状态 + 主题切换。建议保留（否则状态/主题没地方放）
3. **Dock 是否常显**：内容很长滚动时 Dock 固定悬浮（`position: fixed`），会给页面底部留 150px 空白，是否接受？
4. **折射滤镜的浏览器差异**：`backdrop-filter: url(#f)` 在 Chromium 生效、Safari 部分生效、Firefox 不生效——不生效时是否接受"只有色散边、没有折射"的降级？

---

## 2. 用户故事

- 作为使用者，我希望**一眼看到当前在哪个页面**（玻璃胶囊高亮），且切换时胶囊**滑过去**而不是闪现，这样不会"跳一下"让我迷失。
- 作为使用者，我希望**按住胶囊时它像一块真的玻璃**凸出来、边缘有折射和彩色色散，松手回位——这是我确认要的视觉特征。
- 作为使用者，我希望**点圆钮就能搜索并跳转**（页面或设置分区），不用记快捷键。
- 作为使用者，我希望**界面不被侧栏占去 216px**，正文更宽、更聚焦。

---

## 3. 功能需求

### 3.1 功能清单

| 模块 | 优先级 | 描述 |
| --- | --- | --- |
| 布局骨架 | P0 | 删除 `.rail`；`.shell` 改为单列；品牌块迁到内容区顶部 |
| DockNav 组件 | P0 | 5 项 tablist + 玻璃胶囊 + 滑动/按住放大 + 拖动 |
| 折射与色散 | P0 | SVG `feDisplacementMap`（边缘带宽 0.18 / 按住 9px、常态 3.2px）+ 1.5px 彩色玻璃边 |
| 搜索圆钮 | P0 | 位于 Dock 右侧，点击 `setPaletteOpen(true)` |
| 角标 | P0 | 任务中心显示运行中数量（沿用 `activeTasks`） |
| 键盘/可达性 | P1 | 方向键移动、焦点环、`aria-selected` |
| 动效降级 | P1 | `prefers-reduced-motion` 下缩短时长 |
| 响应式 | P1 | <1100px 隐藏 Dock 文字；<720px Dock 宽度自适应 |
| 页脚迁移 | P1 | rail footer → 内容区底部 `app-footer` |

### 3.2 详细规格

#### 3.2.1 布局（`layout.css`）
- `.shell`：`display:flex` 保留，但只有 `.main` 一个子元素；`margin-left: var(--shell-rail)` 侧栏相关规则删除
- `--shell-rail` 变量保留但不再使用（或置 0，避免其它样式引用断裂）
- `.main`：`padding: 20px 28px 150px`（底部给 Dock 让位）
- 品牌块：新增 `.content-brand`（复用 rail__brand 的视觉，44px 高），放在 `main` 顶部、`topbar` 左侧
- 媒体查询里原有的 rail 折叠逻辑（68px / 0px）删除或改为 Dock 相关规则

#### 3.2.2 DockNav 结构
```
<nav class="dock-zone">
  <div class="dock" role="tablist">
    <div class="pill" aria-hidden="true"></div>   <!-- 玻璃胶囊 + SVG 滤镜 -->
    <button class="tab" role="tab" aria-selected>…</button> × 5
  </div>
  <button class="fab" aria-label="搜索（Ctrl K）">🔍</button>
</nav>
```
- 尺寸：`height:76px`；胶囊常态 `tabW-4 × dockH-14`，按住 `×1.34 / ×1.70`
- 胶囊 `z-index` 高于 tab（避免双层重影），`pointer-events:none`
- Dock 固定：`position:fixed; bottom:24px; left:0; right:0; 居中`

#### 3.2.3 交互状态机
| 事件 | 处理 |
| --- | --- |
| 点击 tab | `navigate(id)` + `spring.target = centerOf(i)`；胶囊 CSS 过渡 520ms |
| pointerdown 在胶囊上 | `lift=true`：尺寸放大、贴图重建（强度 9px）、色散增强 |
| pointermove（已 armed 且 >4px） | 跟手移动胶囊 + 实时切 `aria-selected`；`dock[data-drag]` 关闭过渡 |
| pointerup | `lift=false`（520ms 回落）+ 吸附最近 tab + `navigate` 对应路由 |
| pointerdown 不在胶囊上 | 不 arm，交给 click |
| `←/→` | 切到相邻 tab（有过渡） |
| `Ctrl/⌘+K` 或圆钮 | 打开命令面板 |

#### 3.2.4 折射实现要点（从 demo 移植）
- 生成位移贴图：按当前胶囊尺寸，`roundedRectSDF` + `smoothStep(band,0,d)`，R/G 通道编码 dx/dy，写入离屏 canvas → `feImage href`
- 滤镜应用到 `.pill`：`backdrop-filter: url(#dockLiquid) blur(.2px) contrast(1.18) brightness(1.06) saturate(1.18)`；按住时 `blur(.3px) contrast(1.06) saturate(1.35)`
- 贴图重建时机：尺寸变化、lift 切换（强度 3.2 ↔ 9.0）；**拖动过程中不重建**（尺寸不变）
- 浏览器不支持 `url()` 滤镜时：静默降级为纯色散边（`@supports not (backdrop-filter: url(#x))` 不需要，行为本身就是回退）

#### 3.2.5 异常与边界
| 场景 | 期望 |
| --- | --- |
| 页面加载时 Dock 宽度 0（字体未就绪） | 首次 `requestAnimationFrame` 后再定位胶囊；`resize` 时重算 |
| 路由被外部改写（地址栏） | 监听 `hashchange` → 胶囊同步（无动画，`immediate`） |
| 同时按住又点圆钮 | 圆钮 `pointerdown` 不冒泡到 dock（`stopPropagation`） |
| 缩放窗口到 <720px | Dock 文字隐藏，只留图标；胶囊宽度按图标 tab 宽重算 |
| 深色/浅色切换 | 玻璃 token 切换；SVG 贴图不用重建 |

---

## 4. 非功能需求

### 4.1 性能
- 页面初始 JS 增量 ≤ 6KB（gzip）；无新依赖
- 位移贴图生成：单次 ≤ 15ms（约 123×105 像素，~13k 像素）；只在尺寸/强度变化时执行
- 滚动时不产生 layout thrash：胶囊只用 `transform`；不改 `width/height`（除 lift 切换）
- `backdrop-filter` 元素数量：新增 1 个（胶囊）；Dock 本身已有 1 个 → 合计 2 个，可接受

### 4.2 兼容性
- Chromium 120+：完整效果（折射 + 色散）
- Safari 17+：折射可能不生效，色散与玻璃底色正常
- Firefox：无折射，色散正常
- 不支持 `backdrop-filter` 的环境：退化为半透明底色 + 色散边

### 4.3 可访问性
- `role="tablist"` + `role="tab"` + `aria-selected`；Dock `aria-label="主导航"`
- 焦点可见（`:focus-visible` 用 `--color-primary` 描边）
- 圆钮 `aria-label="搜索（Ctrl K）"`
- 动画尊重 `prefers-reduced-motion`

---

## 5. 数据规格
无新增数据/接口。仅使用现有：
- `useApp().tasks`（角标）
- `useApp().health`（顶栏模式徽标）
- `CommandPalette` 现有 props（`open/onClose/onNavigate`）

---

## 6. 接口规格
无 API 变更。组件接口：

```ts
// components/DockNav.tsx
type DockNavProps = {
  routes: { id: RouteId; label: string; icon: LucideIcon }[];
  current: RouteId;
  onNavigate: (id: RouteId) => void;
  activeTasks: number;
  onOpenSearch: () => void;
};
```

---

## 7. 验收标准

**布局**
- [ ] 页面无左侧栏，正文左边缘从 216px 变为 28px 内边距
- [ ] 品牌块在内容区左上，顶栏保留标题 + 模式徽标 + 后端状态 + 主题切换
- [ ] 页脚仍可见（内容区底部），Dock 不遮挡可点击元素（底部留白 ≥ 120px）

**Dock 交互**
- [ ] 点最左 tab 再点最右：胶囊经过 ≥10 个中间位置的连续滑动（用 `getComputedStyle(...).transform` 每 40ms 采样验证）
- [ ] 按住胶囊：尺寸从 92×62 → ~123×105（`getBoundingClientRect` 验证），上下凸出 Dock 各 ≥10px
- [ ] 按住时 `backdrop-filter` 计算值包含 `url("#dockLiquid")`
- [ ] 松手 520ms 内回到常态尺寸并吸附到最近 tab
- [ ] 按住 Dock 空白处/其它 tab 拖动：胶囊 `x` 不变（回归验证）
- [ ] 文字/图标在按住前后尺寸一致（`tab i` 与 `svg` 的 rect 不变），**无重影**（页面上 `x` 数量的文字节点只有一份）

**搜索**
- [ ] 点圆钮打开命令面板；`Esc` 关闭；`Ctrl/⌘+K` 同样可用
- [ ] 输入「引擎」能命中「引擎与模型」「云端引擎管理」并跳转
- [ ] 顶栏不再有第二个搜索入口

**质量门禁**
- [ ] `npx tsc --noEmit` 0 错；`npx vite build` 通过；`pytest` 不受影响（无后端改动）
- [ ] 浏览器控制台 0 error；无 React key/warning
- [ ] 深浅两套主题各有截图留档

---

## 8. 风险与依赖

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| `backdrop-filter: url()` 兼容性差 | Safari/Firefox 无折射 | 色散边兜底；`@supports` 里只上更强的高光 |
| 固定 Dock 遮挡内容 | 长页面底部按钮点不到 | `.main` 底部留 150px；`scroll-padding-bottom` |
| 折射贴图在低端机卡顿 | 拖动掉帧 | 拖动中不重建；贴图按 dpr=1 生成 |
| 去侧栏后某些页面假设侧栏存在（`margin-left`） | 布局错位 | 全量 grep `--shell-rail` 引用并清理 |
| 教程入口迁移影响既有快捷键/深链 | 用户找不到教程 | 顶栏加图标按钮 + CommandPalette 保留「教程」项 |

---

## 9. 实施顺序（评审通过后执行）

1. `tokens.css`：新增玻璃 token（`--glass-bg/-strong/-border/-sheen/-pill/-pill-lift`，浅深两套）
2. `styles/layout.css`：去 rail、`.shell` 单列、内容区留白、`content-brand`、Dock/胶囊/圆钮样式
3. 新增 `components/DockNav.tsx`（含 SVG 位移贴图、拖动、键盘、reduce-motion、降级）
4. `App.tsx`：删 rail JSX、接 DockNav、品牌块迁移、圆钮触发 `setPaletteOpen`
5. 清理 `Chrome.tsx` 的 `CommandButton` 引用（若确认移除）；教程入口按待确认项 1 落地
6. 验证：tsc / build / 真机三态（深浅主题 + 窄屏）+ 采样脚本回归
7. 文档：`docs/API.md` 无需改；`docs/PROJECT_HISTORY` 记一条；`docs/spec` 保留本文件
