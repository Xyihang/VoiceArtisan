"""可读错误：面向界面的错误码 + 文案 + 修复指引，绝不外泄原始堆栈。"""
from __future__ import annotations

from dataclasses import dataclass

ERRORS: dict[str, tuple[str, str]] = {
    "VRAM_INSUFFICIENT": (
        "本地显存不足",
        "四条路：①换更小规格引擎（Qwen3-TTS 0.6B 约 2GB 显存即可，需 ≥2.5GB 空闲）；"
        "②一键切云端继续同一任务；③把 local.device 改成 cpu 用 CPU 推理（功能完整但慢约 25 倍）；"
        "④缩短单句/降低 max_new_tokens，减少峰值显存。",
    ),
    "MODEL_MISSING": (
        "本地模型文件不存在",
        "请在设置页或运行 scripts/download_models.py 下载模型（支持国内镜像）。",
    ),
    "MODEL_LOAD_FAILED": (
        "本地模型加载失败",
        "请查看状态页日志；常见原因是显存不足、模型文件不完整或 CUDA 环境异常。",
    ),
    "CLOUD_UNREACHABLE": (
        "无法连接云端推理服务",
        "请检查云端地址/密钥与 SSH 隧道状态，或先切换到本地模式。",
    ),
    "CLOUD_NOT_READY": (
        "云端模型尚未就绪",
        "云端正在加载模型，请稍候；系统会自动重试并在就绪后继续。",
    ),
    "CLOUD_AUTH_FAILED": (
        "云端鉴权失败",
        "请核对设置页中的云端 API 密钥（X-API-Key）。",
    ),
    "CLOUD_HOST_MISSING": (
        "还没有云端主机档案",
        "先在「云端连接 → 一键接入云主机」完成接入，或在「云端引擎管理」里手工填写主机信息。",
    ),
    "CLOUD_ENGINES_FAILED": (
        "云端引擎操作失败",
        "看返回的日志尾部：通常是云端网络、磁盘或引擎 id 不存在。",
    ),
    "CLOUD_ENGINES_BUSY": ("云端引擎任务正在运行", "等当前安装/删除任务结束再试。"),
    "INVALID_INPUT": ("请求参数不合法", "请检查文本、音色与参数区间。"),
    "TEXT_EMPTY": ("文本为空", "请输入需要合成的文本。"),
    "TEXT_TOO_LONG": ("文本超出长度上限", "请分段合成，或在配置中调高 limits.max_text_chars。"),
    "UPLOAD_TOO_LARGE": ("文件超出大小上限", "请压缩或裁剪后重试，上限见配置 limits.max_upload_mb。"),
    "UPLOAD_FORMAT": ("不支持的文件格式", "支持 wav / mp3 / flac / m4a / ogg / webm。"),
    "AUDIO_TOO_LONG": ("音频时长超出上限", "请裁剪参考音频到推荐时长（3-10 秒）。"),
    "AUDIO_DECODE": ("音频解析失败", "请确认文件未损坏，或改用 wav 格式。"),
    "ASR_FAILED": ("语音转写失败", "请检查音频内容与 ASR 引擎状态。"),
    # ---- 说话人区分（B7/B8/B9 + CLOUD-DIARIZE-1）----
    "CLOUD_DIARIZE_UNSUPPORTED": (
        "云端未安装说话人区分引擎",
        "在云端执行 engines_cli install asr-speaker-campplus；或把 asr.diarize.prefer 改成 local 用本地 CAM++。",
    ),
    "DIARIZE_UNAVAILABLE": (
        "本地与云端都没有可用的说话人区分能力",
        "本地：设置 → 引擎与模型 → 语音识别，安装「CAM++ 声纹」；"
        "云端：在云端执行 engines_cli install asr-speaker-campplus。",
    ),
    "SPEAKER_TIMELINE_MISSING": (
        "缺少说话人时间轴",
        "该任务没有可用的时间轴或没有任何说话人信息（旧任务 / 未开启区分说话人）；"
        "请重新转写并勾选「区分说话人」后再操作。",
    ),
    "SPEAKER_SPLIT_NOT_FOUND": (
        "尚未按说话人拆分",
        "请先调用 POST /api/tasks/{id}/split-by-speaker 生成分轨文件，再打包下载。",
    ),
    "EXPORT_SOURCE_MISSING": (
        "缺少原始音频（source.wav）",
        "该任务生成时没有保留无损原件（通常是升级前的旧任务），无法按其它档位重新导出；"
        "请重新生成任务（新任务会自动保留 source.wav）。",
    ),
    "NO_REF_AUDIO": ("缺少参考音频", "声音克隆需要上传或录制 3-10 秒参考音频。"),
    "VOICE_NOT_FOUND": ("音色不存在", "请刷新音色库后重新选择。"),
    "CANCELED": ("任务已取消", "已生成的片段会保留。"),
    "INTERRUPTED": ("任务被中断", "服务重启导致中断，可点击重试。"),
    "INTERNAL": ("内部错误", "请查看状态页日志，或重试一次。"),
    # ---- 多引擎/模型管理 ----
    "ENGINE_NOT_FOUND": (
        "引擎不存在",
        "请刷新引擎列表后重试；引擎 id 见 GET /api/engines 的 items[].id。",
    ),
    "ENGINE_NOT_INSTALLED": (
        "引擎尚未安装",
        "请在「设置 → 引擎与模型」点击安装，或执行 python -m app.engines_cli install <id>。",
    ),
    "ENGINE_BUSY": (
        "引擎正忙",
        "该引擎正在使用或安装中：请先切换到其它引擎、等待当前任务结束后再操作。",
    ),
    "ENGINE_CAPABILITY_MISSING": (
        "当前引擎不支持该能力",
        "克隆与音色设计需要 Qwen3-TTS 1.7B：请在设置页把 engine.active 切换到 qwen3-tts-1.7b，"
        "或改用轻量引擎支持的普通文本转语音。",
    ),
    "TORCH_CHANGED": (
        "安装中止：torch 被替换",
        "pip 的依赖解析改动了 torch/torchaudio 的版本（常见于某个包声明 torch>=2.5），"
        "CUDA 版会被换成 CPU 版，导致本地推理只能跑 CPU。请按错误详情里的命令恢复原版本后重试；"
        "本项目所有 pip 调用已默认带 --no-deps 与 torch 约束文件，正常不会发生。",
    ),
    "PACKAGE_MISSING": (
        "缺少引擎依赖包",
        "依赖装在每个引擎自己的 venv 里（<models_dir>/<引擎 id>/.venv），"
        "请在设置页点该引擎的「安装」按钮，或复制卡片上的安全安装命令（带 venv python 绝对路径与 -c 约束文件）。"
        "切勿在主环境直接执行 pip install <模型包>：那会替换主环境 torch（真实事故）。",
    ),
    "SOX_MISSING": (
        "缺少 SoX（torchaudio 的 sox_io 后端）",
        "程序启动时已自动把 torchaudio 默认后端切换为 soundfile；若该引擎仍报 SoX，"
        "请确认引擎 venv 里有 soundfile（python -m pip install soundfile），然后重启服务。"
        "不需要安装系统 SoX。"
    ),
    "ENGINE_TIMEOUT": (
        "引擎推理超时",
        "隔离引擎在子进程里跑合成，首次生成要加载权重、耗时较长；"
        "可缩短文本、调高配置里的超时，或查看 data/logs 中的引擎日志后重试。",
    ),
    "VENV_FAILED": (
        "引擎专属 venv 创建或安装失败",
        "常见原因：磁盘空间不足、杀毒软件拦截 <venv>\\Scripts\\python.exe、"
        "或主环境 python 路径含空格/中文。可删除该引擎目录下的 .venv 后重试，"
        "或按 docs/ENGINE_ISOLATION.md 手动创建。",
    ),
    "VERSION_MISMATCH": (
        "安装后版本校验未通过",
        "实际安装的版本与引擎锁文件（backend/app/engine/adapters/locks/<引擎 id>.txt）不一致，"
        "已判定安装失败（不会把不一致的环境标成已安装）。"
        "请直接复制错误详情里的修复命令执行：它带 -c <venv>\\constraints.txt 与 "
        "-r locks\\<引擎 id>.txt，只会把该引擎 venv 内的包装回锁定版本；"
        "切勿在主环境用 pip install -U 或裸包名安装（会替换 main 环境 torch）。",
    ),
}


@dataclass
class AppError(Exception):
    code: str
    detail: str = ""
    status_code: int = 400

    def __post_init__(self) -> None:
        super().__init__(self.code)

    @property
    def message(self) -> str:
        return ERRORS.get(self.code, ("请求失败", ""))[0]

    @property
    def hint(self) -> str:
        return ERRORS.get(self.code, ("", "请稍后重试。"))[1]

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "detail": self.detail[:500],
        }


# 显存/内存不足的常见文本形态（GPU 驱动、CUDA 库、torch、系统都可能给出不同措辞）
OOM_PATTERNS: tuple[str, ...] = (
    "out of memory",
    "cuda oom",
    "cublas_status_alloc_failed",
    "cudnn_status_alloc_failed",
    "cuda_error_out_of_memory",
    "bad_alloc",
    "insufficient memory",
    "not enough memory",
    "allocation failed",
    "failed to allocate",
    "device-side assert",
    "outofmemoryerror",
    "内存不足",
    "显存不足",
)


def is_oom_text(text: str) -> bool:
    """判断一段错误文本是否属于「显存/内存不足」。"""
    low = (text or "").lower()
    return any(pattern in low for pattern in OOM_PATTERNS)


def wrap_internal(exc: Exception) -> AppError:
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if is_oom_text(lowered):
        return AppError("VRAM_INSUFFICIENT", text)
    if "no such file" in lowered or "not found" in lowered and "model" in lowered:
        return AppError("MODEL_MISSING", text)
    if "connect" in lowered or "timeout" in lowered or "unreachable" in lowered:
        return AppError("CLOUD_UNREACHABLE", text)
    return AppError("INTERNAL", text, status_code=500)


_SOX_MARKERS = (
    "sox could not be found",
    "failed to load sox",
    "sox_io",
    "libsox",
    "sox not found",
    "no module named 'sox'",
)


def is_sox_text(text: str) -> bool:
    """判断异常文本是不是缺少 SoX（torchaudio 的 sox_io 后端）。"""
    low = (text or "").lower()
    return any(marker in low for marker in _SOX_MARKERS)


SOX_FIX_HINT = (
    "程序已自动把 torchaudio 后端切换为 soundfile；若该引擎仍报 SoX，请执行 "
    "python -m pip install soundfile 后重启服务（无需安装系统 SoX）。"
)
