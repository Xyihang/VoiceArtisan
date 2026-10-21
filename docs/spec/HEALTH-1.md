# 规格：整体健康度治理（HEALTH-1）

- 日期：2026-10-21
- 触发：一次全量通读（README / VERSION.json / PROJECT_HISTORY / 源码 / 路由清单）后，**在不带任何功能目标的前提下**先量了一遍「项目自身的健康度」
- 结论先行：**功能是完整的，缺的是「自我描述的一致性」与「变更可回滚的底座」**——版本号三处不一致、文档引擎口径 6/7/13 三种说法、
  回归测试本身已经红灯、没有版本控制。

---

## 1. 发现项（全部实测，非推测）

| 编号 | 问题 | 实测证据 | 影响 |
| --- | --- | --- | --- |
| A1 | **无版本控制** | `git status` → `fatal: not a git repository`；仅有 `QWENTTS-src-*.zip` 备份 | 任何一次改动都不可精确回滚；「改 A 坏 B」只能靠 zip 整体倒退 |
| A2 | **版本号三处不一致** | `VERSION.json` = `1.1.11`；`backend/app/__init__.py` = `1.1.8`；`backend/app/main.py` 的 `FastAPI(version="1.0.0")` | MCP `initialize` 的 `serverInfo.version` 报 1.1.8、OpenAPI 报 1.0.0，`/docs` 与交付说明对不上 |
| A3 | **回归测试本身红灯** | `pytest backend/tests` → `1 failed, 31 passed, 1 error` | HARDENING-1 建立的「交付前门禁」当前无法通过，等于门禁失效 |
| A3.1 | 失败项 1 | `test_engine_agnostic.py::test_each_engine_exposes_capabilities` 遍历 `registry.ids()`（13 个）并断言都含 `tts`，但其中 6 个是 ASR/声纹引擎 | 测试写于「只有 TTS 引擎」时期，未随 `ASR-ENGINE-1` 更新 |
| A3.2 | 错误项 2 | `test_torch_guard.py::test_pip_install_guard(no_deps: bool)` 缺 `@pytest.mark.parametrize`，pytest 把 `no_deps` 当 fixture | `--no-deps` 与「精选依赖」两条安装路径实际上从未被 pytest 执行过（直接运行脚本时才跑） |
| A3.3 | 失效断言 3（本次新增发现） | `test_pip_install_guard` 断言命令行含 `--no-cache-dir`，但 `installer.pip_install()` 早已改为把缓存重定向到项目 `.cache/pip`（`PIP_CACHE_DIR`），模块 docstring 却仍写「统一追加 `--no-cache-dir`」 | 同一件事在**实现 / 文档 / 测试**里三种说法；且该测试此前因缺 fixture 从未被 pytest 执行，缺陷长期不可见 |
| A4 | **文档引擎口径三种说法** | README F11 写「内置 6 个引擎」、README 变更表写「7 个引擎」、`registry.py` 实际注册 **13 个**（7 TTS + 5 ASR + 1 声纹） | 对外交付时「到底几个引擎」说不清；审计脚本的 B2 断言「返回 7 个」也已过期 |
| A4.1 | 变更表停在旧版本 | README「近期变更（2026-10-21 · v1.1.8）」，而 `VERSION.json` 已是 1.1.10 | v1.1.9 / v1.1.10 的修复（批次重试保参、ASR 取消语义、教程 5 分区）对外不可见 |
| A4.2 | 待办状态过期 | `PROJECT_HISTORY.md §10` 仍写「CosyVoice3/2/300M/IndexTTS-2 已实现但**未注册**」 | 实际 `registry.py` 已注册这 4 个；按文档会重复劳动 |
| A5 | **仓库卫生** | `scripts/_cloud2_*.py` 等 11 个一次性调试脚本（全仓 0 引用）、`frontend/package-lock.json.broken.bak`（P0-1 事故残留）、`webui/` 9.2MB 私有音频（28 文件） | 一次性脚本混在正式脚本目录里；私有音频一旦入库将永久留在历史里 |
| A4.3 | 待办项被错误合并 | `PROJECT_HISTORY.md §10` 有一行把「清理残留 torch-*.dist-info」与「模型下载 UI 化（后端下载接口 + 前端进度…）」两条待办粘成一条 | 两条待办各自被削弱，容易漏做 |
| A5.1 | 缓存落盘 | `scripts/__pycache__/`、`launcher/__pycache__/`、`backend/tests/__pycache__/`、`backend/.pytest_cache/` | 已被 `.gitignore` 覆盖，仅本地冗余 |

## 2. 目标（Must Have）

1. **可回滚**：建立本地 git 仓库，基线快照入库，任何后续改动都有提交可回溯。
2. **单一版本源**：版本号只由 `VERSION.json` 定义，`app.__version__` 与 FastAPI/OpenAPI 一律读它；新增测试守住不许漂移。
3. **门禁重新变绿**：`pytest backend/tests` 全绿（0 failed / 0 error），且失败过的两条路径确实被执行。
4. **口径唯一**：全仓对「引擎数量」只保留一种说法，并给出 TTS/ASR/声纹的分解。
5. **仓库只装代码**：一次性脚本与失效备份移出；私有素材永不入库（`.gitignore` 兜住）。

### Won't Have（本次明确不做）

- 不引入 CI/CD、不建远端仓库（用户未提，且涉及凭据）。
- 不重构业务代码、不改任何推理/路由/前端行为。
- 不删除 `webui/` 私有素材（属用户数据，只做忽略与登记）。
- 不追 `frontend/dist` 便携版发布包（`PROJECT_HISTORY §10` 的独立待办）。
- 不改 `asr-speaker-campplus` 的 `kind="speaker"` 语义（当前按声纹单独分组是可用行为，只修文档口径）。

## 3. 实施条目与验收标准

| 编号 | 动作 | 验收（可执行） |
| --- | --- | --- |
| H1 | `git init` + `.gitignore` 增补（`webui/`、`*.bak`）+ `core.autocrlf=false` + 基线提交 | `git log --oneline` 有 1 条基线；`git ls-files` 200 条、仓库 ≈1MB；`git ls-files` 中不含 `models/ data/ .cache/ webui/ node_modules/ frontend/dist/ *.bak` |
| H2 | `app/__init__.py` 版本改为 1.1.10；`main.py` 改用 `__version__` 并同步 `title` 为品牌名 | 新增 `backend/tests/test_version_consistency.py`：`app.__version__ == VERSION.json.version`；`create_app().version == app.__version__` |
| H3 | `test_engine_agnostic.py` 断言范围收敛到 `registry.ids_of_kind("tts")`；`test_torch_guard.py` 补 `parametrize(no_deps=[True, False])` | `pytest tests -q` → 0 failed / 0 error；`test_pip_install_guard` 出现 **2** 个用例 |
| H4 | README：F11 引擎行改为「13 个（7 TTS + 5 ASR + 1 声纹）」并列出；变更表补 v1.1.9 / v1.1.10；`docs/README.md` 补本规格索引；`PROJECT_HISTORY §10` 勾掉已注册项 | `grep -c "内置 6 个引擎"` = 0；全仓引擎数量口径只剩「13 个」；`PROJECT_HISTORY §10` 对应项为 `[x]` |
| H5 | 删除 12 个一次性脚本 + 失效 lock 备份（提交基线之后再删，可 `git revert`） | `scripts/_cloud2_*.py` 0 个；`frontend/*.bak` 0 个；`git status` 干净 |

## 4. 风险与依赖

| 风险 | 处理 |
| --- | --- |
| git 首次提交误收大文件（models 25.7GB、.cache 3.1GB、webui 9.2MB） | 先补 `.gitignore` 再 `git add -A`，入库前用「危险路径检查」断言为空，体积 1.97MB 才提交 |
| Windows 换行符污染历史 | `core.autocrlf=false`，保留源码原有 LF |
| 删除脚本误删仍在用的文件 | 删除前全仓 grep 12 个文件名，0 引用；且基线提交已含这些文件，可随时 `git checkout` 取回 |
| 版本号改动影响 MCP 契约 | MCP 只把版本作为 `serverInfo` 展示字段，不参与协议判定 |

## 5. 已知未治理项（登记，不本次做）

1. `scripts/audit_all.py` 的 B2 断言「7 个引擎」需改为「13 个」——它依赖运行中的服务，本次只记录不改（避免在无服务时引入未验证改动）。
2. `backend/tests/` 无 `pytest.ini` / `conftest.py`，测试靠各自插入 `sys.path`；统一配置留待下次。
3. 前端无单测框架（仅 Playwright 脚本），覆盖率缺口仍然存在。
4. README 与实际目录仍有细节差异（如 `webui/` 未在目录结构中说明，本规格只在 `.gitignore` 登记）。

## 6. 落地结果（2026-10-21 实测）

| 编号 | 结果 | 证据 |
| --- | --- | --- |
| H1 | 完成 | 基线提交 `bb019d8`，`git ls-files` 200 个文件 / 仓库 1.01 MiB；危险路径检查为空（无 models、data、.cache、webui、node_modules、dist、*.bak） |
| H2 | 完成 | `app.__version__ = 1.1.11`；`main.py` 用 `__version__` 且 title 改为品牌名；新增 `test_version_consistency.py` 3 条（含 MCP `serverInfo` 一致性） |
| H3 | 完成 | `pytest tests -q` → **38 passed, 0 failed, 0 error**（治理前 31 passed / 1 failed / 1 error）；`test_pip_install_guard` 现执行 2 个用例（`[True]` / `[False]`） |
| H3.3 | 完成 | A3.3 按「实现为准」收敛：docstring 改为描述 `PIP_CACHE_DIR` 重定向，测试改断言「缓存目录存在且位于项目 `.cache/pip`」 |
| H4 | 完成 | README F11 改「13 个引擎（7 TTS + 5 ASR + 1 声纹）」、变更表标题 v1.1.8 → v1.1.11、文档索引加本规格；`PROJECT_HISTORY §10` 勾掉「未注册」并拆分被合并的两条待办；`CONFIG.md` 同步口径 |
| H5 | 完成 | 删除 `scripts/_cloud2_*.py`/`_diag_engines.py` 共 11 个（全仓 0 引用）+ `frontend/package-lock.json.broken.bak`；删除后 `scripts/*.py` 剩 16 个正式脚本 |

### 6.1 治理期间发现的环境事实（重要）

本会话进行中，**另一个进程/会话在并发修改本项目**：`VERSION.json` 从 `1.1.10` 变为 `1.1.11`、
`README.md` 与 `PROJECT_HISTORY.md` 在 14:00:20–14:00:35 被写入（新增决策 #57「导出档位 3.4」）。
因此本次基线提交（`bb019d8`）抓取的是**该并发写入之后**的快照；本规格的所有修改均为定点字符串替换，
未覆盖他人改动。后续若再出现「文件已变化」提示，应先 `git status` 确认是否又有人在写。
