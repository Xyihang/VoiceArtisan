# 版本锁定（所有安装命令必须引用这份清单）

**规则：任何由程序生成或文档给出的安装命令，一律使用 `==` 精确版本；禁止 `>=`、禁止无版本号、禁止 `pip install -U`、禁止让 pip 自行解析主版本。**

原因（真实事故）：`pip install voxcpm` 因为 voxcpm 声明 `torch>=2.5.0`，把主环境 `torch 2.4.1+cu121` 换成了 `torch 2.14.1+cpu`，
导致 CUDA 失效、整套本地 TTS 退化为 CPU 推理。

---

## 一、主环境（项目运行环境，不可变更）

`requirements.pins.txt` 是权威清单，安装/修复/校验一律用它：

```powershell
python -m pip install -r requirements.pins.txt --no-cache-dir
```

| 包 | 锁定版本 | 说明 |
| --- | --- | --- |
| torch | `2.4.1+cu121` | GPU 推理基线，**禁止升级** |
| torchaudio | `2.4.1+cu121` | 与 torch 严格配对 |
| torchvision | `0.19.1+cu121` | 仅作为 torch 版本对齐，项目本身不直接使用 |
| transformers | `4.57.3` | qwen-tts 0.1.1 实测通过 |
| numpy | `2.2.6` | 与 torch 2.4.1 的 ABI 兼容 |
| fastapi / uvicorn / pydantic / httpx / websockets / paramiko / cryptography / soundfile / scipy / PyYAML / python-multipart | 见 `requirements.lock.txt` | 已锁定 |
| qwen-tts | `0.1.1` | 默认引擎，禁止升级 |

修复 torch（唯一允许的重装命令）：

```powershell
python -m pip install --force-reinstall --no-cache-dir ^
  torch==2.4.1+cu121 torchaudio==2.4.1+cu121 torchvision==0.19.1+cu121 ^
  --index-url https://download.pytorch.org/whl/cu121
```

## 二、引擎依赖（一律装进引擎自己的 venv，不碰主环境）

每个引擎的依赖清单在 `backend/app/engine/adapters/locks/<engine_id>.txt`（精确版本，逐行 `pkg==x.y.z`）。
安装命令只能由程序按下面两段式生成：

```powershell
# 1) 引擎主包：不解析依赖，避免牵连 torch
"<models>\<engine_id>\.venv\Scripts\python.exe" -m pip install --no-deps --no-cache-dir ^
  -c "<models>\<engine_id>\.venv\constraints.txt" ^
  "voxcpm==2.0.3"

# 2) 引擎依赖：按锁文件精确安装
"<models>\<engine_id>\.venv\Scripts\python.exe" -m pip install --no-cache-dir ^
  -c "<models>\<engine_id>\.venv\constraints.txt" ^
  -r "backend\app\engine\adapters\locks\<engine_id>.txt"
```

`constraints.txt` 由程序在安装时写入，内容为**当前主环境**的 torch 家族版本（例如 `torch==2.4.1+cu121`）。
若某引擎必须使用不同 torch（例如它声明 `torch>=2.5`），则：

- **只在它自己的 venv 内**安装该版本，并在卡片上写明「该引擎需要 torch X.Y（隔离环境，不影响主环境）」；
- 主环境的 torch 版本**永不改变**；安装前后各做一次快照比对，一旦变化立即中止并报 `TORCH_CHANGED`（附恢复命令）。

## 三、命令输出规范（前端 / CLI / 文档统一）

1. 每条命令都必须带 `==` 版本，并带 `--no-cache-dir`；
2. 引擎依赖命令必须使用该引擎 venv 里的 python 绝对路径；
3. 必须带 `-c <constraints.txt>`（约束文件路径同样用绝对路径）；
4. 禁止在输出中出现：`pip install <裸包名>`、`pip install -U`、`>=`、`latest`；
5. 「复制命令」按钮复制的就是上面这种完整形式，用户直接粘贴即可，不会破坏主环境。

## 四、版本变更流程

1. 修改 `requirements.pins.txt` 或 `locks/<engine_id>.txt`；
2. 在本机执行全量回归（`scripts/smoke_test.py` + `scripts/full_test.py` + 一次真实 1.7B 生成）；
3. 通过后更新 `VERSION.json` 的 `verified_environment` 与本文表格；
4. 未通过则回滚版本，不允许"先上线再说"。

## 五、硬性约束（故障后补充）

1. **锁文件必须纯 ASCII**：`locks/*.txt`、`constraints.txt`、`requirements*.txt` 只允许 `pkg==x.y.z` 与英文注释。
   pip 在中文 Windows 上按 GBK 解码 `-r`/`-c` 文件，中文注释会直接 `UnicodeDecodeError`。
   → 由 `backend/tests/test_installer_guard.py` 守护（含中文即测试失败）。
2. **pip 子进程强制 UTF-8**：`installer._pip_env()` 注入 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`；
   文件仍含非 ASCII 时 `_ascii_copy()` 生成 ASCII 副本再交给 pip。
3. **镜像错误分类**：`_classify_pip_error()` 区分 `missing_on_index`（换源）、`conflict`（**立即中止**）、`network`（换源）。
4. **pip 缓存**：统一 `.cache/pip`（`PIP_CACHE_DIR`），不写 C 盘，重试复用 wheel。
5. **引擎要不同 torch 时**：只在它自己的 `locks/<id>.txt` 钉版本（如 `voxcpm1.5` → `torch==2.5.1+cu121`），
   主环境 `requirements.pins.txt` 永远是 `2.4.1+cu121`。

## 六、快速自检

```powershell
# 主环境版本是否符合锁定清单
python -c "import torch,sys;print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
# 期望：2.4.1+cu121 12.1 True

# 逐条核对（会列出不一致项）
python -m pip install -r requirements.pins.txt --dry-run --no-cache-dir
```

## 七、引擎锁文件（locks/<id>.txt）

> 本节为**追加**内容：把上面「二、引擎依赖」里提到的 `locks/<engine_id>.txt` 机制写成可执行的规范。

1. **位置**：`backend/app/engine/adapters/locks/<engine_id>.txt`，当前覆盖
   `voxcpm1.5`、`cosyvoice3-0.5b`、`cosyvoice2-0.5b`、`cosyvoice-300m-sft`、`indextts2`、`qwen3-tts-1.7b` 六个引擎。
2. **格式**：逐行 `包名==精确版本`，允许 `#` 注释（行尾注释也可以）。
   程序解析器 `deps.parse_lock_file()` 遇到 `>=`、`~=`、`*`、无版本 会直接报错，**绝不猜版本**。
3. **版本来源必须可追溯**，每个文件头部三行注释写清：`engine:` / `generated:` / `source:`。来源只有三种：
   - 本机已装：`importlib.metadata.version()` 取实际版本；
   - pip 解析：`python -m pip install --dry-run --report - <包列表>`（只解析，不下载安装、不改环境）；
   - 官方声明：从 `requirements.pins.txt` / 官方 README 抄，并注明出处。
   解析不出来的包**不写版本**，要么从清单里删掉，要么单独注明原因，禁止编造。
4. **torch / torchaudio / torchvision 不写进 venv 引擎的锁文件**，由 `<venv>\constraints.txt` 统一注入
   （默认取主环境值；引擎必须在自己 venv 里用别的 torch 时，才在锁文件里显式写，并在卡片 notes 标注）。
   `qwen3-tts-1.7b` 例外：它走主环境（`isolation=inprocess`），锁文件内容就是主环境必须满足的版本。
5. **安装只能用锁文件**：`installer.install_from_lock()` 先 `--no-deps` 装主包（版本取自锁文件），
   再 `-r <锁文件>`，两条命令都带 `-c <venv>\constraints.txt`、`--no-cache-dir`。
   程序生成的所有命令（`deps.install_command()`、`BaseAdapter.install_commands()`、
   `engines_cli deps/install`、`GET /api/engines` 的 `install_commands`、前端「复制命令」）
   形态完全一致，用户复制粘贴即可。
6. **装完必须校验**：`<venv_python> -m pip list --format=json` 与锁文件逐包比对，
   任何 `实际 != 锁定` 或缺失 → `VERSION_MISMATCH`（detail 含不匹配清单与修复命令），
   校验不通过**不允许**置 `installed=true`。单独校验：`python -m app.engines_cli verify <id>`。
7. **改版本流程**：改锁文件 → 装到某个引擎 venv → `engines_cli verify <id>` 必须通过 →
   再更新 `VERSION.json` 与本文件表格（不允许只改锁文件不校验）。

| 引擎 | 锁文件 | 备注 |
| --- | --- | --- |
| `voxcpm1.5` | `locks/voxcpm1.5.txt` | 主包 `voxcpm==2.0.3` 走 `--no-deps` |
| `cosyvoice3-0.5b` | `locks/cosyvoice3-0.5b.txt` | 无 pip 主包（代码来自 git 镜像） |
| `cosyvoice2-0.5b` | `locks/cosyvoice2-0.5b.txt` | 与 CV3 共用依赖集 |
| `cosyvoice-300m-sft` | `locks/cosyvoice-300m-sft.txt` | 与 CV3 共用依赖集 |
| `indextts2` | `locks/indextts2.txt` | 官方 pyproject 钉 torch 2.8.*，本项目不采用 |
| `qwen3-tts-1.7b` | `locks/qwen3-tts-1.7b.txt` | 走主环境，仅用于版本校验 |
