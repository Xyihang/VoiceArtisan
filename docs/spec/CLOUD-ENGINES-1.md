# CLOUD-ENGINES-1 · 云端引擎远程管理

## 1. 目标

一键接入（`CLOUD-AGENT-1`）成功后，**本地后端**能直接管理**云端主机上的引擎与模型**：

- 查看云端装了哪些引擎（含手工直传上去的模型——状态以云端文件系统为准）
- 在云端**下载/安装**引擎模型
- **删除**云端模型
- 切换云端的当前引擎

本地与云端的引擎管理**彻底分开**：设置页出现独立的「云端引擎管理」分区，不复用本地引擎卡片的状态。

## 2. 非目标（本轮明确不做）

- ❌ 本地 → 云端的模型直传兜底（用户明确说暂时不需要；直传仍可手工做，但不在界面上）
- ❌ 云端引擎的显存/健康探测（云端引擎由云端服务自己管，本地只做文件级管理）
- ❌ 多台云端主机同时管理（一个本地后端只记一份云端档案）

## 3. 传输通道

本地后端 → 云端 = **SSH**（复用 `cloud_agent._SSH`），执行云端的 `engines_cli`：

```
cd <project_dir>/backend && PYTHONPATH=<project_dir>/backend \
  HF_ENDPOINT=https://hf-mirror.com <python> -m app.engines_cli <subcommand>
```

选 SSH 而不是云端 HTTP 的理由：服务没起来时也能装模型（正是「装模型 → 起服务」的顺序）；且删除/下载本来就该按文件系统算。

## 4. 连接档案（持久化）

`<data_dir>/cloud-host.json`：

| 字段 | 说明 |
| --- | --- |
| `host` / `ssh_port` / `username` | SSH 目标 |
| `password_enc` | 密码（DPAPI 密文，明文绝不落盘） |
| `project_dir` | 云端项目目录，默认 `/root/autodl-tmp/QWENTTS` |
| `python` | 云端解释器，默认 `/root/miniconda3/bin/python` |
| `updated_at` | 更新时间 |

- 一键接入成功（S8）时自动写入（密码来自当次任务内存）
- 也允许在界面上手工填写/覆盖（换机、改密码）

## 5. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/cloud/engines/host` | 读档案（密码脱敏成 `has_password`） |
| PUT | `/api/cloud/engines/host` | 写档案（`password` 可选，空表示沿用旧值） |
| POST | `/api/cloud/engines/host/test` | 试连：返回指纹 + 云端 python 版本 |
| GET | `/api/cloud/engines` | 远端 `engines_cli list --json`（含 `active`/`items[]`） |
| POST | `/api/cloud/engines/{id}/install` | 202 + `job_id`，后台流式跑 |
| GET | `/api/cloud/engines/jobs/{job_id}` | 任务详情（阶段/百分比/日志尾部） |
| DELETE | `/api/cloud/engines/{id}` | 删除云端模型，返回释放体积 |
| POST | `/api/cloud/engines/{id}/select` | 切换云端当前引擎 |

进度事件：`bus.publish("cloud-engines", "job.update", {...})`，前端轮询 job 详情即可（不强制走 WS）。

## 6. 界面

设置页新增分区 **「云端引擎管理」**（`settingsAnchor: cloudEngines`）：

1. 顶部：云端档案表单（主机/端口/用户/密码/项目目录/解释器）+「测试连接」+ 状态灯
2. 未配置时：整块显示空态 + 指到「云端连接 → 一键接入云主机」
3. 已配置时：引擎卡片列表（按 TTS / ASR / 声纹分组），每张卡显示状态、体积、能力，"安装 / 删除 / 设为当前"
4. 安装中：进度条 + 阶段文字 + 最近 20 行日志
5. 明确标注「这是云端主机上的模型，不是本机的」

## 7. 验收

- AC1：档案保存后刷新页面仍在，密码不回显明文
- AC2：`GET /api/cloud/engines` 能列出直传上去的 `asr-whisper-small` / `asr-speaker-campplus`
- AC3：安装一个引擎能看到阶段+百分比推进，成功后 status 变 ready
- AC4：删除后体积归零、再装能恢复
- AC5：切换当前引擎后 `active` 变化
- AC6：未配置档案时接口返回 `CLOUD_HOST_MISSING`（不是 500）
