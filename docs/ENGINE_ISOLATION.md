# 引擎依赖隔离（每个引擎一个 venv）

> 一句话：除默认引擎 `qwen3-tts-1.7b` 留在主环境就地推理，其余引擎的依赖与 torch
> 全部关进 `<models_dir>/<engine_id>/.venv`，合成时用子进程跑 —— **主环境的
> `torch 2.4.1+cu121` 永远不被任何引擎安装动作替换**。

## 1. 为什么必须隔离（真实事故）

有人直接在主环境裸装 `voxcpm 2.0.3`（不带版本）。`voxcpm 2.0.3` 声明 `torch>=2.5`，
pip 的解析器就把主环境的 `torch 2.4.1+cu121` 换成了 CPU 版 `2.14.1+cpu`：

- CUDA 不可用 → 整套本地 TTS 从 GPU 掉到 CPU（慢约 25 倍）；
- `torchaudio` / `torchvision` 被连带换掉，版本关系全乱；
- 恢复要手工**强制重装** cu121 三件套。

第一层防护（`--no-deps` + 约束文件 + 安装前后快照）只保证「不破坏」，一旦引擎真的
需要更高的 torch，pip 只会报冲突 → 引擎装不上。**隔离才是能共存的做法**：
引擎在自己的 venv 里爱装什么版本都行。

## 2. 目录与解释器约定

| 项 | 路径 |
| --- | --- |
| venv 目录 | `<models_dir>/<engine_id>/.venv`（默认 `models/<engine_id>/.venv`） |
| 解释器（Windows） | `<venv>\Scripts\python.exe` |
| 解释器（Linux/macOS） | `<venv>/bin/python` |
| pip 约束文件 | `<venv>/constraints.txt` |
| 版本锁文件 | `backend/app/engine/adapters/locks/<engine_id>.txt`（逐行 `包名==精确版本`） |
| 最小核心依赖 | `backend/app/engine/adapters/requirements.engine-core.txt` |
| 子进程入口 | `backend/app/engine/adapters/runner.py` |

创建命令由程序执行（`BaseAdapter.ensure_venv()` → `venv.ensure()`）：

```powershell
<main_python> -m venv --clear "<models>\<engine_id>\.venv"
```

**绝不加 `--system-site-packages`**：加了就能 import 到主环境的 torch，隔离立刻失效。
建完先装核心依赖（`pydantic / pydantic-settings / PyYAML / numpy / soundfile / httpx`，
不含任何 torch 家族），再装引擎自己的依赖。

## 3. 推理链路（子进程桥）

```
Router/Worker → 适配器.synthesize()
              → subprocess: <venv_python> runner.py --engine <id> --request req.json --out out.wav
              → runner 内部 import 该引擎的适配器模块（**不经 registry**，registry 会连带 import qwen→torch）
              → adapter.synthesize() 就地推理 → 拼 AudioChunk → soundfile 写 wav
              → stdout 打一行 JSON {"ok":true,"duration_s":..,"sample_rate":..,"chunks":..}
              → 父进程读回 wav，产出单个 AudioChunk
```

- venv 引擎的 `load()` / `unload()` 是 no-op（模型在子进程里加载/释放）；
- 失败时 runner 打 `{"ok":false,"error":{"code":..,"message":..}}` 并退出非 0，
  父进程按码映射：超时 → `ENGINE_TIMEOUT`、解释器缺失 → `PACKAGE_MISSING`、
  其余用 JSON 里的 `error.code`；
- wav 先写 `.tmp` 再原子改名，失败**不会**留半截文件。

## 4. 程序给出的安装命令长什么样（复制即用，不会碰主环境 torch）

**所有命令都来自该引擎的锁文件** `backend\app\engine\adapters\locks\<engine_id>.txt`
（逐行 `包名==精确版本`，禁止 `>=` / `~=` / `*` / 无版本）。

引擎主包（`--no-deps`，不让它的 `torch>=2.5` 进入解析；版本取自锁文件）：

```powershell
"<models>\voxcpm1.5\.venv\Scripts\python.exe" -m pip install --no-cache-dir --no-deps ^
  -c "<models>\voxcpm1.5\.venv\constraints.txt" "voxcpm==2.0.3"
```

引擎依赖（按锁文件精确安装）：

```powershell
"<models>\voxcpm1.5\.venv\Scripts\python.exe" -m pip install --no-cache-dir ^
  -c "<models>\voxcpm1.5\.venv\constraints.txt" ^
  -r "backend\app\engine\adapters\locks\voxcpm1.5.txt"
```

恢复主环境 torch（唯一允许动主环境 torch 的命令，脚本自带绝对路径与 cu121 源）：

```powershell
"<main_python>" -m pip install --force-reinstall --no-cache-dir torch==2.4.1+cu121 ^
  --index-url https://download.pytorch.org/whl/cu121
```

这些字符串由 `deps.pinned_install_command()` / `deps.lock_install_command()` /
`BaseAdapter.install_commands()` 生成，同时出现在 `GET /api/engines` 的 `install_command(s)` 字段、
`python -m app.engines_cli deps <id>` 与前端卡片的「复制安装命令」按钮上 —— 三处**同一份**，
不存在手写的裸 `pip install`，也不存在无版本号/高版本命令。

`constraints.txt` 的语义（精确版本，纯 ASCII）：

1. 默认钉主环境 `requirements.pins.txt` 里的 torch/torchaudio/torchvision 与
   `transformers==4.57.3`、`numpy==2.2.6`；
2. 该 venv 内已装的版本覆盖主环境值（避免每次安装都把 2GB 的 torch 重装一遍）；
3. 引擎锁文件里显式写了不同版本时（例如 `torch==2.5.1+cu121`）**以锁文件为准**（仅该 venv），
   卡片 notes 会标注「本引擎需要 torch X（隔离环境，不影响主环境）」；
4. 钉住的值带 `+cuXXX` 本地标签时，文件里同时写入
   `--extra-index-url https://download.pytorch.org/whl/<cuXXX>`（PyPI 上没有这种 wheel）。
   文件内容必须是纯 ASCII：Windows 上 pip 用 locale(GBK) 解码 `-c` 文件，中文注释会让它 `UnicodeDecodeError`。

安装完成后**必须**通过严格版本校验：`<venv_python> -m pip list --format=json` 与锁文件逐包比对，
任何 `实际 != 锁定` 或缺失都会抛 `VERSION_MISMATCH`（detail 列出不匹配清单 + 修复命令），
校验不通过就不会把环境标成「已安装」。单独校验用：

```powershell
python -m app.engines_cli verify <id>          # 只校验，不下载、不安装
```

## 5. 怎么手动修

1. **依赖装不上**：先看 `python -m app.engines_cli deps <id> --json`，其中
   `suggested_command` 就是可直接粘贴的安全命令；仍失败就删掉 venv 重来：
   ```powershell
   Remove-Item -Recurse -Force "<models>\<engine_id>\.venv"
   python -m app.engines_cli install <id>
   ```
2. **想给某个引擎换 GPU 版 torch**（只影响它自己）：
   ```powershell
   "<venv>\Scripts\python.exe" -m pip install --no-cache-dir ^
     torch==2.4.1+cu121 --index-url https://download.pytorch.org/whl/cu121
   ```
   然后刷新卡片（`venv_exists / venv_size_mb` 会变，主环境不动）。
3. **确认主环境没被动过**：
   ```powershell
   python -c "import torch;print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
   # 期望 2.4.1+cu121 12.1 True
   ```

## 6. 常见故障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `VENV_FAILED`，日志只有「退出码非 0」 | 磁盘空间不足 / 杀软拦截 `python.exe` 写入 | 清理磁盘，把 `models/` 加入杀软白名单后重试 |
| 建 venv 很慢（本机实测 60-95 秒） | `venv` 要跑 ensurepip，再装 6 个核心依赖 | 正常，一次性成本；之后 `ensure_venv()` 秒回 |
| 每个 venv 约 80-120MB | venv 自带 pip/setuptools + 核心依赖 | 6 个引擎全建约 0.5-0.7GB，`remove` 会一并删掉并计入释放体积 |
| 解释器路径含空格/中文 | `"<py>"` 未加引号时被拆成两段 | 程序生成的所有命令都给路径加了引号；手敲请照抄 |
| 删除 venv 报「文件被占用」 | 还有 runner 子进程在跑 / 资源管理器开着该目录 | 等任务结束或关闭窗口后重试；`remove` 用 `ignore_errors` + 一次重试 |
| `runner.py` 报 `import` 失败 | 该引擎依赖还没装进它自己的 venv | 按第 4 节命令装进 `<venv>`，或点卡片「安装」 |

## 7. 离线 / 内网环境

- venv 创建本身不需要联网，但 `ensure_venv()` 要装 6 个核心依赖 → 离线时会失败；
  可先在有网机器上 `pip download` 这些包，再：
  ```powershell
  "<venv>\Scripts\python.exe" -m pip install --no-index --find-links "<wheel 目录>" ^
    -c "<venv>\constraints.txt" -r "backend\app\engine\adapters\requirements.engine-core.txt"
  ```
- 引擎自己的依赖同理：用 `--no-index --find-links` 指到内网 wheel 仓库即可；
  命令形式不变（仍是 venv python + `-c` 约束文件）。
- 引擎代码仓库（CosyVoice / IndexTTS-2）需要 git 与镜像，离线时按
  `CODE_REPO_UNAVAILABLE` 的提示手工放置目录。

## 8. 与其它配置的关系

- `isolation` 是适配器类属性：默认 `"venv"`；`qwen3-tts-1.7b` 显式 `"inprocess"`
  （它是默认引擎，`qwen-tts` 与 torch 本就在主环境，再套 venv 只是多一次进程启动）；
- `GET /api/engines` 每条记录新增：`isolation / venv_path / venv_python / venv_exists /
  venv_ready / venv_size_mb / venv_constraints / lock_file / lock_packages / versions_ok /
  versions_mismatch / packages_source / install_command(s)`；
- `remove` 会删模型目录 + `.venv`，释放体积一并返回。
