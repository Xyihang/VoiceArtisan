# 规格：v1.1.41 发布前功能回归与打包（SPEC-RELEASE-1.1.41）

> 状态：**已执行并全部通过（24/24）**
> 目的：本轮 UI 大改（去侧栏 + 液态玻璃 Dock + 顶栏轮播 + 玻璃表面层 + 性能优化 + 表单对齐）后，确认**没有功能损坏**，再打 zip。

---

## 1. 需求澄清清单

### 核心诉求
UI 重构是否伤到既有功能；确认无损伤后产出可交付的 zip 包。

### 功能范围
**Must Have（P0）**
1. 六个页面（工作台 / 音色库 / 任务中心 / 设置 / 状态与日志 / 教程）都能正常渲染、标题正确
2. 后端接口未被前端改动影响（pytest 全绿）
3. 构建链正常：`tsc --noEmit` 0 错、`vite build` 通过
4. 导航（Dock）、搜索面板、设置页表单可用
5. 数据列表（任务 / 音色）能正常拉到数据
6. 深浅两套主题可用
7. 无横向溢出、Dock 不遮挡、控制台无非预期错误

**Won't Have（本次不做）**
- 不改任何后端逻辑
- 不做新功能，只做回归验证与打包
- 不修改 `data/`、`models/`、`config/config.yaml`（打包时排除）

### 技术约束
- 打包工具沿用 `python .cache/make_backup.py` / `... portable`
- 打包必须排除：`node_modules`、`__pycache__`、`.venv`、`data`、`models`、`.cache`、`.git`、`launcher/build`、`config/config.yaml`
- 产物落在 `E:\trae_projects\QWENTTS-backups\`，不删除历史包

---

## 2. 验收标准与实际结果

### 2.1 后端与构建

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 单元测试 | `python -m pytest tests -q` | **103 passed** |
| 类型检查 | `npx tsc --noEmit` | **0 错** |
| 生产构建 | `npx vite build` | **通过**（JS 433KB / gzip 148KB） |

### 2.2 运行时冒烟（Playwright，1440×900）

| # | 检查项 | 结果 |
| --- | --- | --- |
| 1-6 | 六个路由渲染 + 顶栏标题正确 | PASS（工作台/音色库/任务中心/设置/状态与日志/教程） |
| 7 | 无左侧栏 | PASS |
| 8 | Dock 6 个 tab + 搜索圆钮 | PASS |
| 9 | 顶栏轮播存在、页面标题在最左（left=24） | PASS |
| 10 | 卡片不再使用 `backdrop-filter`（性能修复生效） | PASS（none） |
| 11 | 输入框边框可见 | PASS（rgb 207,214,223） |
| 12 | 页面背景网格/环境光仍在 | PASS |
| 13 | 无横向溢出 | PASS（overflowX=0） |
| 14 | Dock 悬浮在视口内 | PASS（bottom 876 / vh 900） |
| 15 | Dock 胶囊仍有真折射滤镜 | PASS（`url("#qwentts-dock-liquid")`） |
| 16 | 页脚已移除（并入顶栏轮播） | PASS |
| 17 | 点 Dock 切页 | PASS |
| 18 | 搜索面板打开并可 Esc 关闭 | PASS |
| 19 | 设置页 13 个分区可点 | PASS |
| 20 | 设置输入框可编辑 | PASS |
| 21 | 任务列表有数据 | PASS（12 行） |
| 22 | 音色卡渲染 | PASS（9 张） |
| 23 | 深色主题生效 | PASS |
| 24 | 控制台无非预期错误 | PASS（仅 3 条「云端实例不可达」400，环境因素） |

### 2.3 已知环境性问题（非回归）
- `/api/cloud/engines` 返回 400 `SSH_UNREACHABLE`（`116.136.52.182:47443` 连接被拒），因为云端实例未开机/隧道未连；顶栏同时显示「云端不可达」，与前几版行为一致。
- 本机 SoX 14.4.2 的 `sox -h` 缺少 `AUDIO FILE FORMATS:` 会令 python-sox 导入崩溃；用 `.cache/soxshim/sox.bat` 置于 PATH 前排绕过（项目本身有 `app/soxfree.py`，不依赖真实 SoX）。

---

## 3. 打包规格

| 包 | 命令 | 内容 |
| --- | --- | --- |
| 源码包 | `python .cache/make_backup.py` | 全部源码，不含 `frontend/dist`、`data`、`models`、`config/config.yaml` |
| 便携包 | `python .cache/make_backup.py portable` | 同上 + `frontend/dist`（用户免装 Node） |

命名：`QWENTTS-{src|portable}-v{版本}-{时间戳}.zip`，输出到 `E:\trae_projects\QWENTTS-backups\`。

---

## 4. 风险与结论

| 风险 | 说明 | 处置 |
| --- | --- | --- |
| 云端相关功能无法在本机端到端验证 | 实例未开机 | 已在验收中标注为环境限制；不影响本地/打包 |
| subgrid 仅 Chromium 支持 | Electron/Chrome 内正常 | `@supports` 包裹，老浏览器回退 `align-items: start` |
| 真模糊仅保留在 Dock/顶栏/弹层 | 大卡片模糊会把滚动压到 ~15fps | 已实测：滚动 95.8ms → 18.2ms/帧 |

**结论：无功能损坏，允许打包。**
