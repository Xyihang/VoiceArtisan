# 规格：全量自检与回归防线（HARDENING-1）

- 日期：2026-10-20
- 触发：连续多次「改 A 坏 B」（`split_first_chunk` 未导入、`EXPECTED_MB` 未定义、
  `adapter` 未定义、切引擎不生效、0.6B fp16 NaN、双 effect 死循环…）
- 结论先行：**问题不在某一次改动，而在缺少「一改就能全量验证」的机制。**

---

## 1. 背景与根因

| 已发生的事故 | 本可被什么挡住 |
| --- | --- |
| `cloud.py` 用了未导入的 `split_first_chunk`（云端 100% 失败） | pyflakes（undefined name） |
| `qwen.py` 用了未定义的 `EXPECTED_MB`（安装预览崩） | pyflakes |
| `router.py` 用未定义的 `adapter`（所有 Qwen 任务失败） | pyflakes |
| 切到 0.6B 仍加载 1.7B 目录 | 引擎切换矩阵测试（选引擎→看 size/目录） |
| 0.6B fp16 采样 NaN | 每个引擎「各跑一次真实合成」 |
| 「使用」按钮转圈（后端 50s 慢查询） | 接口耗时断言 |
| 两个 effect 死循环导致界面抽搐 | 前端 DOM mutation 断言 |
| 测试脚本在音色库留垃圾 | 数据清洁度断言 |

**根因**：每次只验证「刚改的那一处」，没有**统一的全量自检脚本 + 固定验收清单**。

## 2. 目标

1. 一条命令 `python scripts/audit_all.py` 跑完：静态检查 + 接口契约 + 引擎矩阵 + 前端冒烟 + 数据清洁度，
   全部通过才允许交付。
2. 任何一项失败都给出**可执行的定位信息**（文件/行号/接口/引擎 id）。
3. 覆盖「多引擎」这个最容易互相影响的面（7 个引擎 × 切换 × 能力 × 音色可用性）。

## 3. 审计范围（Must Have）

| 编号 | 检查 | 通过标准 |
| --- | --- | --- |
| A1 | 后端编译 | `compileall backend/app` 退出码 0 |
| A2 | 后端静态检查 | pyflakes 无 `undefined name` / `undefined name ...` |
| A3 | 脚本静态检查 | `scripts/`、`launcher/` 无语法错误（py_compile） |
| A4 | 前端类型 | `tsc --noEmit` 无错误 |
| A5 | 前端构建 | `npm run build` 成功且产物存在 |
| B1 | 健康接口 | `/api/health` 200 且 `active_backend` 有值 |
| B2 | 引擎列表 | `/api/engines` 返回 7 个引擎，响应 < 1s |
| B3 | 引擎矩阵 | 每个已安装引擎：`select` 成功 → `local.size` 与引擎规格一致 → `health.ready` |
| B4 | 音色契约 | `/api/voices` 含 `engine` 字段；非 Qwen 时 `builtin[].available=false` 且带 `reason` |
| B5 | 能力门禁 | 0.6B/其它引擎 `capabilities` 不含 `design`；1.7B 含 |
| B6 | 真实合成 | 至少对 `qwen3-tts-1.7b` 与 `qwen3-tts-0.6b` 各跑一次短文本合成，状态 `completed` 且输出峰值 > -20 dBFS |
| B7 | 关键接口 | `/api/settings`、`/api/voices`、`/api/tasks`、`/api/system/status`、`/api/system/selfcheck` 均 200 |
| B8 | 隧道/云端容错 | 隧道不可用时 `/api/health` 仍 200（不阻塞） |
| C1 | 页面冒烟 | 5 个页面加载无 JS 异常、无 >400 的 XHR |
| C2 | 抖动检测 | 工作台静止 3s 内 DOM mutation < 50 |
| C3 | 布局溢出 | 1440/760/333 三种宽度横向溢出 0 |
| D1 | 音色清洁度 | 无「冒烟克隆 / 能力测试」这类测试残留 |
| D2 | 锁文件 ASCII | `locks/*.txt` 无非 ASCII 字节 |
| D3 | 备份可交付 | 最近一个 zip 含 exe + frontend/dist |

## 4. 明确不做（Won't Have）

- 不做 CI/CD 平台接入（本机脚本即可）；
- 不为每个引擎下载/安装新模型（只测已安装的）；
- 不做性能压测（只测单次响应时间上限）。

## 5. 验收标准

- [ ] `python scripts/audit_all.py` 一条命令输出 20+ 项结果，末尾给出「通过/失败」与失败项定位；
- [ ] 失败项退出码非 0，成功退出码 0（可用于交付前门禁）；
- [ ] 审计脚本本身不修改任何数据（只读 + 真实合成会产生任务，属必要副作用）；
- [ ] 连续两次运行结果一致（幂等，除任务数增加）。

## 6. 风险

| 风险 | 处理 |
| --- | --- |
| 真实合成较慢（1.7B 在 4GB 卡约 30-70s） | 默认只测 0.6B + 1.7B 各一句短文本，可用 `--skip-tts` 跳过 |
| 审计会创建任务 | 任务名带 `[audit]` 前缀，便于识别与清理 |
| 引擎未安装时矩阵测试失败 | 未安装的引擎只校验「状态字段正确」，不要求 select 成功 |

---

## 7. 实施结果（2026-10-20）

### 7.1 新增一条命令的全量自检

`scripts/audit_all.py`（20 项，约 3-4 分钟；`--skip-tts` 约 20 秒）：

```bash
python scripts/audit_all.py              # 交付前必须全绿
python scripts/audit_all.py --skip-tts   # 快速自查
```

覆盖：A1 编译 / A2 pyflakes / A3 脚本语法 / A4 tsc / A5 构建产物 /
B1-B8 接口与引擎矩阵（含真实合成 + 音量断言）/ C1-C3 界面冒烟·抖动·溢出 / D1-D3 数据清洁度。

### 7.2 审计当轮发现并修复的 bug

| 问题 | 根因 | 修复 |
| --- | --- | --- |
| 选中「已经是当前」的引擎时 `local.size` 不同步 | `manager.select()` 在 `previous == engine_id` 时提前 return，跳过 size 同步 | 提前返回分支也更新 `local.size`；并在启动时做一次自愈（`state._sync_local_size`） |

### 7.3 审计结果（实测）

```
第一次（含真实合成）：20 项 → 19 通过 / 1 失败（B3：1.7B 的 local.size 仍是 0.6B）
修复后：           20 项 → 20 通过 / 0 失败
   B6 真实合成 qwen3-tts-0.6b  completed 3.12s peak=0.482
   B6 真实合成 qwen3-tts-1.7b  completed 1.12s peak=0.723
   C1 5 个页面 0 JS 异常 / 0 失败请求
   C2 3 秒内 DOM 变更 0 次
   C3 1440/760/333px 横向溢出 0
```

### 7.4 深度探测（补充，手工执行）

逐引擎音色契约（3 个已安装引擎）、安装预览（1.7B/0.6B/voxcpm 均 200 且字段完整）、
`local.size` 热更、任务取消、错误码（不存在的引擎/音色均 404）、列表幂等 —— **全部通过，问题数 0**。

### 7.5 交付纪律（此后固定）

1. 改完任何代码先跑 `python scripts/audit_all.py --skip-tts`；
2. 涉及引擎/合成/前端交互的改动，再跑一次不带 `--skip-tts` 的全量；
3. 交付前必须 20/20 全绿，否则不发备份。
