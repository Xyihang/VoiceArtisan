"""配置加载：config.yaml + 环境变量 + 运行期覆盖（data/runtime.json）。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .bootstrap import PROJECT_ROOT

CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_CONFIG_FILE = CONFIG_DIR / "config.yaml"
EXAMPLE_CONFIG_FILE = CONFIG_DIR / "config.example.yaml"
ENV_PREFIX = "QWENTTS__"

# 允许界面/接口在运行期修改的配置项（其余只读，需改配置文件）
RUNTIME_MUTABLE = (
    "run.mode",
    "run.asr_prefer",
    "run.hybrid_long_text_chars",
    "cloud.base_url",
    "cloud.api_key",
    "cloud.models",
    "cloud.verify_tls",
    "cloud.request_timeout_s",
    "cloud.chunk_timeout_s",
    "cloud.retries",
    "cloud.retry_backoff_s",
    "cloud.readiness_timeout_s",
    "cloud.tunnel",
    "cloud.idle_shutdown_enabled",
    "cloud.idle_shutdown_minutes",
    "cloud.idle_shutdown_url",
    "agent",
    "agent.llm",
    "asr.diarize",
    "asr.engine",
    "asr.local",
    "asr.cloud",
    "defaults",
    "post",
    "export",
    "qc.enabled",
    "qc.min_similarity",
    "tts.normalize",
    "tts.normalize_lang",
    "mock.enabled",
    "engine.active",
    "engine.allow_parallel",
    "local.keep_loaded",
    "local.warmup_on_start",
    "local.warmup_kinds",
    "local.unload_after_idle_seconds",
    "local.allow_small_fallback",
    "local.low_vram",
    "local.device",
    "local.size",
    "local.dtype",
    "local.vram_reserve_gb",
    "local.allow_vram_overcommit",
)


class ModelSet(BaseModel):
    base: str = "models/Qwen3-TTS-12Hz-1.7B-Base"
    custom_voice: str = "models/Qwen3-TTS-12Hz-1.7B-CustomVoice"
    voice_design: str = "models/Qwen3-TTS-12Hz-1.7B-VoiceDesign"


class ServerCfg(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8443
    https: bool = True
    open_browser: bool = True
    log_level: str = "INFO"


class StorageCfg(BaseModel):
    data_dir: str = "data"
    models_dir: str = "models"
    cache_dir: str = ".cache"


class RunCfg(BaseModel):
    mode: Literal["local", "cloud", "hybrid"] = "hybrid"
    hybrid_long_text_chars: int = 300
    asr_prefer: Literal["local", "cloud"] = "local"


class LowVramCfg(BaseModel):
    """实验性：显存不足时用 CPU/磁盘 offload 加载 1.7B。

    注意：qwen-tts 0.1.1 的 generate 循环会访问 offload 后的 meta 张量
    （`Tensor.item() cannot be called on meta tensors`），因此 offload 目前只能加载、无法生成。
    默认关闭；4-8GB 显存请使用云端模式。
    """

    enabled: bool = False
    gpu_reserve_gb: float = 0.9
    cpu_memory_gb: int = 32
    offload_dir: str = ""  # 留空则用 <data_dir>/offload


class LocalCfg(BaseModel):
    device: str = "cuda:0"
    # 本地模型规格：1.7B（默认，质量最好）/ 0.6B（约 2GB 显存可跑）
    size: Literal["1.7B", "0.6B"] = "1.7B"
    # fp16 显存占用与 bf16 相同，但在 4GB 卡上可通过 WDDM 共享内存跑 1.7B（实测可用）
    dtype: str = "float16"
    attn_implementation: str = "eager"
    keep_loaded: bool = True
    warmup_on_start: bool = True
    warmup_kinds: list[str] = Field(default_factory=lambda: ["custom_voice"])
    unload_after_idle_seconds: int = 600
    vram_reserve_gb: float = 0.1
    allow_small_fallback: bool = False
    max_new_tokens: int = 2048
    allow_vram_overcommit: bool = True
    low_vram: LowVramCfg = Field(default_factory=LowVramCfg)
    models: ModelSet = Field(default_factory=ModelSet)
    fallback_models: ModelSet = Field(
        default_factory=lambda: ModelSet(
            base="models/Qwen3-TTS-12Hz-0.6B-Base",
            custom_voice="models/Qwen3-TTS-12Hz-0.6B-CustomVoice",
            voice_design="models/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        )
    )


class TunnelCfg(BaseModel):
    enabled: bool = False
    ssh_host: str = ""
    ssh_port: int = 22
    username: str = ""
    key_path: str = ""
    password: str = ""          # 内存态；落盘请写 password_enc（DPAPI）
    password_enc: str = ""      # DPAPI 密文，重启后解密回 password
    local_port: int = 18443
    remote_host: str = "127.0.0.1"
    remote_port: int = 8443


class CloudCfg(BaseModel):
    base_url: str = "https://your-cloud-gpu-host:8443"
    api_key: str = ""
    verify_tls: bool = True
    request_timeout_s: float = 30.0
    chunk_timeout_s: float = 180.0
    retries: int = 3
    retry_backoff_s: float = 2.0
    readiness_timeout_s: float = 900.0
    models: ModelSet = Field(
        default_factory=lambda: ModelSet(
            base="Qwen3-TTS-12Hz-1.7B-Base",
            custom_voice="Qwen3-TTS-12Hz-1.7B-CustomVoice",
            voice_design="Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        )
    )
    tunnel: TunnelCfg = Field(default_factory=TunnelCfg)
    idle_shutdown_enabled: bool = False
    idle_shutdown_minutes: int = 15
    idle_shutdown_url: str = ""
    fallback_to_checkpoint: str = ""

    def effective_base_url(self) -> str:
        """隧道启用时，客户端只访问本地映射端口，对上层完全透明。"""
        t = self.tunnel
        if t.enabled and t.ssh_host:
            scheme = "https" if self.base_url.startswith("https") else "http"
            return f"{scheme}://127.0.0.1:{t.local_port}"
        return self.base_url.rstrip("/")


class AsrLocalCfg(BaseModel):
    # 字段名 model_size 会撞 pydantic 的 model_ 保护前缀，显式关掉命名空间检查
    model_config = ConfigDict(protected_namespaces=())

    # 引擎管理里选中的 ASR 引擎 id（如 asr-whisper-small）；为空时回落到 model_size（向后兼容）
    engine_id: str = ""
    model_size: str = "small"
    device: str = "auto"
    compute_type: str = "int8"
    beam_size: int = 5


class AsrDiarizeCfg(BaseModel):
    """转写时区分说话人（默认关闭；关闭时行为与之前完全一致）。"""

    enabled: bool = False
    max_speakers: int = 4
    threshold: float = 0.75
    # 说话人声纹模型（与转写引擎分开选，互不覆盖）
    engine_id: str = "asr-speaker-campplus"


class AsrCloudCfg(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = "whisper-1"


class AsrCfg(BaseModel):
    engine: Literal["local", "cloud"] = "local"
    local: AsrLocalCfg = Field(default_factory=AsrLocalCfg)
    cloud: AsrCloudCfg = Field(default_factory=AsrCloudCfg)
    diarize: AsrDiarizeCfg = Field(default_factory=AsrDiarizeCfg)


class LimitsCfg(BaseModel):
    max_upload_mb: int = 50
    max_ref_audio_seconds: int = 30
    max_asr_audio_seconds: int = 1800
    max_text_chars: int = 5000
    allowed_audio_formats: list[str] = Field(
        default_factory=lambda: ["wav", "mp3", "flac", "m4a", "ogg", "webm", "aac"]
    )
    allowed_output_formats: list[str] = Field(default_factory=lambda: ["wav", "mp3"])


class DefaultsCfg(BaseModel):
    speed: float = 1.0
    pitch: int = 0
    temperature: float = 0.9
    top_p: float = 0.8
    top_k: int = 50
    repetition_penalty: float = 1.05
    steps: int = 2048
    output_format: str = "wav"
    language: str = "Auto"
    # 默认关闭流式：整段一次生成，语气最连贯（首包=整段耗时）。需要边生成边播时在界面打开。
    streaming: bool = False
    chunk_chars: int = 28
    # 生成参数默认值：None = 不覆盖引擎默认（不回传 null，避免污染任务 params）
    num_step: int | None = None
    guidance_scale: float | None = None
    denoise: bool | None = None
    # 目标时长（秒）：None/0 = 不控制；优先级高于 speed（PHASE-3 3.5）
    target_duration_s: float | None = None


class PostCfg(BaseModel):
    """后期处理链（PHASE-3 3.3）：低切 → 高架 → 压缩 → 峰值归一化。默认全关。"""

    enabled: bool = False
    low_cut_hz: float = 80.0
    high_shelf_db: float = 0.0
    compress: bool = True
    threshold_db: float = -18.0
    ratio: float = 2.5
    attack_ms: float = 8.0
    release_ms: float = 120.0
    normalize_peak_db: float | None = -1.0


class ExportCfg(BaseModel):
    """导出档位（PHASE-3 3.4）：original 原生 / standard 44.1k MP3 192k / editing 48k 24bit WAV。"""

    tier: str = "original"


class QcCfg(BaseModel):
    """任务完成后自动做 ASR 回译质检（对比原文，发现内容缺失/错读）。默认关闭。"""

    enabled: bool = False
    min_similarity: float = 0.75


class TtsCfg(BaseModel):
    """文本规范化：把数字/单位/型号/日期转成中文读法，减少 TTS 误读。"""

    normalize: bool = True
    # 目前只实现 zh；en 等其它语言原样返回
    normalize_lang: str = "zh"


class MockCfg(BaseModel):
    enabled: bool = False


class EngineCfg(BaseModel):
    """多引擎：当前使用的引擎 id 与是否允许并行驻留。"""

    active: str = "qwen3-tts-1.7b"
    # 4GB 卡保持 false：一次只驻留一个引擎，切换时先卸载旧引擎并清显存
    allow_parallel: bool = False


class AgentLlmCfg(BaseModel):
    """云主机接入 Agent 的 LLM 决策层（OpenAI 兼容，模型固定）。"""

    enabled: bool = True
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-flash"    # DeepSeek 官方 V4.1 Flash；也可填 deepseek-v4-pro
    api_key: str = ""                # 也可用环境变量 QWENTTS_AGENT_KEY
    api_key_enc: str = ""            # DPAPI 密文（落盘用，绝不明文）
    temperature: float = 0.0
    max_turns: int = 12
    timeout_s: float = 60.0


class AgentCfg(BaseModel):
    llm: AgentLlmCfg = Field(default_factory=AgentLlmCfg)


class AppConfig(BaseModel):
    agent: AgentCfg = Field(default_factory=AgentCfg)
    server: ServerCfg = Field(default_factory=ServerCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)
    run: RunCfg = Field(default_factory=RunCfg)
    local: LocalCfg = Field(default_factory=LocalCfg)
    cloud: CloudCfg = Field(default_factory=CloudCfg)
    asr: AsrCfg = Field(default_factory=AsrCfg)
    limits: LimitsCfg = Field(default_factory=LimitsCfg)
    defaults: DefaultsCfg = Field(default_factory=DefaultsCfg)
    qc: QcCfg = Field(default_factory=QcCfg)
    post: PostCfg = Field(default_factory=PostCfg)
    export: ExportCfg = Field(default_factory=ExportCfg)
    tts: TtsCfg = Field(default_factory=TtsCfg)
    mock: MockCfg = Field(default_factory=MockCfg)
    engine: EngineCfg = Field(default_factory=EngineCfg)

    # ---- 解析后的绝对路径（运行期填充，不写入文件） ----
    root_dir: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    models_dir: Path = PROJECT_ROOT / "models"

    @field_validator("defaults")
    @classmethod
    def _check_defaults(cls, v: DefaultsCfg) -> DefaultsCfg:
        if v.output_format not in {"wav", "mp3"}:
            raise ValueError("defaults.output_format 仅支持 wav / mp3")
        return v

    def resolve_paths(self, root: Path | None = None) -> "AppConfig":
        root = Path(root or PROJECT_ROOT)
        self.root_dir = root

        def absify(p: str) -> Path:
            path = Path(p).expanduser()
            return path if path.is_absolute() else (root / path)

        self.data_dir = absify(self.storage.data_dir)
        self.models_dir = absify(self.storage.models_dir)
        self.storage.data_dir = str(self.data_dir)
        self.storage.models_dir = str(self.models_dir)
        self.storage.cache_dir = str(absify(self.storage.cache_dir))
        def model_path(p: str) -> Path:
            """模型路径按 storage.models_dir 解析（历史上错误地按项目根解析）。"""
            path = Path(p).expanduser()
            if path.is_absolute():
                return path
            if path.parts and path.parts[0] == "models":
                path = Path(*path.parts[1:])
            return self.models_dir / path

        self.local.models = ModelSet(
            base=str(model_path(self.local.models.base)),
            custom_voice=str(model_path(self.local.models.custom_voice)),
            voice_design=str(model_path(self.local.models.voice_design)),
        )
        self.local.fallback_models = ModelSet(
            base=str(model_path(self.local.fallback_models.base)),
            custom_voice=str(model_path(self.local.fallback_models.custom_voice)),
            voice_design=str(model_path(self.local.fallback_models.voice_design)),
        )
        if not self.local.low_vram.offload_dir:
            self.local.low_vram.offload_dir = str(self.data_dir / "offload")
        else:
            self.local.low_vram.offload_dir = str(absify(self.local.low_vram.offload_dir))
        return self

    def cache_dir(self) -> Path:
        return Path(self.storage.cache_dir)


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_overrides() -> dict:
    data: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX):].lower().split("__")
        node = data
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(value)
    return data


def _coerce(value: str) -> Any:
    low = value.lower()
    if low in {"true", "false"}:
        return low == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.startswith("[") or value.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    if "," in value:  # 环境变量里的列表：a,b,c
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _seal_secrets(data: dict) -> None:
    """写 runtime.json 前，把明文凭据换成 DPAPI 密文（不明文落盘）。"""
    try:
        from .secretstore import protect

        tunnel = ((data.get("cloud") or {}).get("tunnel") or {})
        password = tunnel.get("password")
        if password and not tunnel.get("password_enc"):
            enc = protect(password)
            if enc:
                tunnel["password_enc"] = enc
                tunnel["password"] = ""
        elif tunnel.get("password_enc"):
            tunnel["password"] = ""   # 已有密文：明文一律清掉，避免旧密码残留
        llm = (((data.get("agent") or {}).get("llm")) or {})
        key = llm.get("api_key")
        if key and not llm.get("api_key_enc"):
            enc = protect(key)
            if enc:
                llm["api_key_enc"] = enc
                llm["api_key"] = ""
    except Exception:  # noqa: BLE001
        pass


def _apply_secrets(app: "AppConfig") -> None:
    """把 DPAPI 密文解密回内存配置（明文永不落盘）。"""
    try:
        from .secretstore import unprotect

        enc = app.agent.llm.api_key_enc
        if enc and not app.agent.llm.api_key:
            plain = unprotect(enc)
            if plain:
                app.agent.llm.api_key = plain
        t_enc = app.cloud.tunnel.password_enc
        if t_enc:
            t_plain = unprotect(t_enc)
            if t_plain:
                app.cloud.tunnel.password = t_plain   # 密文优先，覆盖可能的旧明文
    except Exception:  # noqa: BLE001 - 解密失败不影响启动
        pass


class ConfigManager:
    """单例配置管理器：文件 → 环境变量 → 运行期覆盖，支持热更新与落盘。"""

    def __init__(self, config_file: Path | None = None) -> None:
        self._lock = threading.RLock()
        self.config_file = Path(config_file or os.environ.get("QWENTTS_CONFIG") or DEFAULT_CONFIG_FILE)
        self._file_data: dict = {}
        self._runtime_path: Path | None = None
        self._runtime_data: dict = {}
        self.app: AppConfig = AppConfig()
        self.reload()

    # ---- 加载 ----
    def _read_yaml(self) -> dict:
        if not self.config_file.exists():
            if EXAMPLE_CONFIG_FILE.exists():
                self.config_file.parent.mkdir(parents=True, exist_ok=True)
                self.config_file.write_text(
                    EXAMPLE_CONFIG_FILE.read_text(encoding="utf-8"), encoding="utf-8"
                )
            else:
                return {}
        return yaml.safe_load(self.config_file.read_text(encoding="utf-8")) or {}

    def reload(self) -> AppConfig:
        with self._lock:
            self._file_data = self._read_yaml()
            data_dir = Path(self._file_data.get("storage", {}).get("data_dir", "data"))
            if not data_dir.is_absolute():
                data_dir = PROJECT_ROOT / data_dir
            self._runtime_path = data_dir / "runtime.json"
            self._runtime_data = {}
            if self._runtime_path.exists():
                try:
                    self._runtime_data = json.loads(self._runtime_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    self._runtime_data = {}
            merged = _deep_merge(self._file_data, self._runtime_data)
            merged = _deep_merge(merged, _env_overrides())
            app = AppConfig.model_validate(merged).resolve_paths()
            _apply_secrets(app)
            self.app = app
            return app

    # ---- 运行期可变项 ----
    def update_runtime(self, patch: dict, persist: bool = True) -> AppConfig:
        rejected = [k for k in _flatten_keys(patch) if not is_mutable_key(k)]
        if rejected:
            raise ValueError(
                f"以下配置项不允许在运行期修改：{', '.join(rejected[:6])}。"
                f"可修改项：{', '.join(RUNTIME_MUTABLE)}；模型路径等请编辑 {self.config_file}"
            )
        with self._lock:
            self._runtime_data = _deep_merge(self._runtime_data, patch)
            if persist and self._runtime_path:
                _seal_secrets(self._runtime_data)
                self._runtime_path.parent.mkdir(parents=True, exist_ok=True)
                self._runtime_path.write_text(
                    json.dumps(self._runtime_data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            return self.reload()

    def reset_runtime(self) -> AppConfig:
        with self._lock:
            self._runtime_data = {}
            if self._runtime_path and self._runtime_path.exists():
                self._runtime_path.unlink()
            return self.reload()

    def runtime_snapshot(self) -> dict:
        return dict(self._runtime_data)


def _flatten_keys(data: dict, prefix: str = "") -> list[str]:
    keys: list[str] = []
    for k, v in data.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            keys.extend(_flatten_keys(v, f"{key}."))
        else:
            keys.append(key)
    return keys


def is_mutable_key(key: str) -> bool:
    """允许的键：本身在白名单内，或它的某个祖先在白名单内（如 cloud.models.base ← cloud.models）。"""
    if key in RUNTIME_MUTABLE:
        return True
    parts = key.split(".")
    for i in range(1, len(parts)):
        if ".".join(parts[:i]) in RUNTIME_MUTABLE:
            return True
    return False


config_manager = ConfigManager()
settings = config_manager.app
