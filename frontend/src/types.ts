export type TaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'canceled';
export type TaskType = 'tts' | 'clone' | 'design' | 'asr';
export type Backend = 'local' | 'cloud' | 'mock';
export type RunMode = 'local' | 'cloud' | 'hybrid';
/** 说话人区分实际由谁执行（后端回填到 params.diarize_backend） */
export type DiarizeBackend = 'local' | 'cloud';
/** 说话人区分优先位置（asr.diarize.prefer） */
export type DiarizePrefer = 'local' | 'cloud' | 'auto';
/** 导出档位：原件 / 标准交付 / 剪辑素材 */
export type ExportTier = 'original' | 'standard' | 'editing';

export interface TaskParams {
  speed: number;
  pitch: number;
  temperature: number;
  top_p: number;
  top_k: number;
  repetition_penalty: number;
  steps: number;
  output_format: string;
  language: string;
  streaming?: boolean;
  chunk_chars?: number;
  force_backend?: Backend;
  /** 生成参数：留空/null = 用引擎默认 */
  num_step?: number | null;
  guidance_scale?: number | null;
  denoise?: boolean | null;
  /** 目标时长（秒）：优先级高于 speed，生成后按目标时长伸缩/截断/补静音 */
  target_duration_s?: number | null;
  /** 导出档位：original / standard / editing（留空跟随设置） */
  export_tier?: string | null;
  /** 转写任务级开关：是否区分说话人 / 最少·最多说话人 */
  diarize?: boolean | null;
  max_speakers?: number | null;
  /** 说话人下限（1-8，须 ≤ max_speakers） */
  min_speakers?: number | null;
  /** 本次说话人由谁识别（后端回填，只读） */
  diarize_backend?: DiarizeBackend | null;
}

/** 转写时间轴片段（区分说话人时带 speaker） */
export interface TaskSegment {
  start_s: number;
  end_s: number;
  text: string;
  /** 展示名（改名后是「张三」，未改名等于 speaker_id） */
  speaker?: string | null;
  /** 原始编号 SPK1/SPK2，改名时以它为准 */
  speaker_id?: string | null;
}

export interface Task {
  id: string;
  type: TaskType;
  status: TaskStatus;
  text: string;
  language: string;
  voice_id: string | null;
  voice_name: string | null;
  instruct: string;
  design: string;
  params: TaskParams;
  backend: Backend;
  model: string;
  degraded: boolean;
  progress: number;
  first_packet_ms: number | null;
  elapsed_ms: number | null;
  rtf: number | null;
  vram_mb: number | null;
  duration_sec: number | null;
  sample_rate: number | null;
  output_file: string | null;
  output_format: string;
  transcript: string | null;
  /** 仅转写任务返回：只读派生视图，形如「[张三] 文本」 */
  transcript_by_speaker?: string | null;
  /** 仅转写任务返回：来自 chunks.json 的时间轴 */
  segments?: TaskSegment[];
  /** 说话人编号 → 展示名（改名后返回，如 {"SPK1": "张三"}） */
  speaker_aliases?: Record<string, string>;
  error_code: string | null;
  error_message: string | null;
  retry_of: string | null;
  canceled: boolean;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  logs?: { ts: number; message: string }[];
}

export interface Voice {
  id: string;
  name: string;
  kind: 'builtin' | 'custom';
  speaker: string | null;
  gender: string;
  age: string;
  language: string;
  dialect: string;
  accent: string;
  description: string;
  ref_text: string;
  has_audio: boolean;
  peaks: number[];
  duration_sec: number | null;
  created_at: number;
  available?: boolean;
}

export interface VoicesResponse {
  builtin: Voice[];
  custom: Voice[];
  languages: { id: string; label: string }[];
  supported_speakers: string[];
  counts: { builtin: number; custom: number };
}

export interface Asset {
  id: string;
  filename: string;
  duration_sec: number;
  sample_rate: number;
  peaks: number[];
  size_bytes: number;
  url: string;
}

export interface CloudHealth {
  backend: string;
  reachable: boolean;
  ready: boolean;
  auth?: boolean | null;
  latency_ms: number | null;
  url: string;
  model?: string;
  device?: string;
  vram_free_gb?: number;
  vram_total_gb?: number;
  error?: string;
}

export interface Health {
  status: string;
  uptime_s: number;
  mode: RunMode | 'mock';
  active_backend: string;
  compute_device?: string;
  cpu_mode?: boolean;
  local: {
    backend: string;
    ready: boolean;
    device: string;
    device_label?: string;
    cpu_mode?: boolean;
    compute_device?: string;
    vram_total_gb: number;
    vram_free_gb: number;
    vram_allocated_mb: number;
    loaded_models: Record<
      string,
      { path: string; degraded: boolean; sample_rate: number; offload?: boolean; loaded_at?: number }
    >;
    last_error: string | null;
  };
  cloud: CloudHealth;
  tunnel: { enabled: boolean; running: boolean; local_port: number | null; error: string | null };
  idle: { enabled: boolean; idle_seconds: number; remaining_seconds: number | null };
  queue: { active: string | null; pending: number };
  model: string;
}

export interface Meta {
  languages: { id: string; label: string }[];
  /** 有效能力（按 run.mode 计算，前端门禁只用它） */
  capabilities?: CapabilitySet;
  limits: {
    max_upload_mb: number;
    max_ref_audio_seconds: number;
    max_asr_audio_seconds: number;
    max_text_chars: number;
    allowed_audio_formats: string[];
    allowed_output_formats: string[];
  };
  defaults: TaskParams;
  modes: { id: RunMode; label: string; description: string }[];
  output_formats: string[];
  task_types: { id: TaskType; label: string }[];
}

export interface TaskList {
  items: Task[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface WsEvent<T = unknown> {
  topic: string;
  event: string;
  ts: number | null;
  data: T;
}

export interface ChunkEvent {
  task_id: string;
  index: number;
  is_last: boolean;
  sample_rate: number;
  duration: number;
  backend: Backend;
  model: string;
  wav_b64: string;
}

/* =========================== 说话人 / 导出 / 批量 =========================== */

/** asr.diarize 配置块（设置页与 /api/system/status 共用） */
export interface AsrDiarizeConfig {
  enabled: boolean;
  prefer: DiarizePrefer;
  min_speakers: number;
  max_speakers: number;
}

/** /api/system/status 的 asr_diarize 块：说话人区分的权威可用性（不靠启发式） */
export interface AsrDiarizeStatus {
  prefer: DiarizePrefer;
  enabled: boolean;
  /** 勾选区分说话人后的真实尝试顺序 */
  candidates: DiarizeBackend[];
  local_available: boolean;
  local_reason?: string;
  /** null = 尚未探测云端；true/false = 云端是否认 diarize 字段 */
  cloud_supported: boolean | null;
  cloud_engine?: string | null;
  min_speakers: number;
  max_speakers: number;
}

/** GET /api/system/status：只取前端用得到的只读字段，其余保持宽松 */
export interface SystemStatus {
  asr_candidates?: DiarizeBackend[];
  asr_diarize?: AsrDiarizeStatus;
  config?: {
    asr_engine?: string;
    asr_candidates?: DiarizeBackend[];
    asr_diarize?: AsrDiarizeConfig;
  } & Record<string, unknown>;
  [key: string]: unknown;
}

/** 重新导出时后端探测到的成品规格 */
export interface ExportMediaInfo {
  file?: string | null;
  bytes?: number;
  sample_rate: number | null;
  channels: number | null;
  bits: number | null;
  codec: string | null;
  duration_sec: number | null;
  tier?: string | null;
  tier_label?: string | null;
}

/** POST /api/tasks/{id}/export 的返回（export 与 media 二选一，旧实现只给 media） */
export interface TaskExportResult {
  ok: boolean;
  task_id?: string;
  tier: ExportTier;
  tier_label?: string | null;
  file?: string;
  export?: ExportMediaInfo;
  media?: ExportMediaInfo;
  task?: Task;
}

/** POST /api/tasks/{id}/speakers/merge 的返回 */
export interface SpeakerMergeResult {
  ok: boolean;
  merged: { from: string[]; to: string };
  segments: TaskSegment[];
  speaker_aliases: Record<string, string>;
}

/** POST /api/tasks/{id}/split-by-speaker 的单个分轨文件 */
export interface SpeakerSplitItem {
  speaker: string;
  speaker_id: string;
  file: string;
  duration_s: number;
  bytes: number;
  segments: number;
}

/** POST /api/tasks/batch 的单项：每项都可以覆盖音色 / 语言 / 指令 / 参数 */
export interface BatchItemPayload {
  text: string;
  voice_id?: string | null;
  asset_id?: string | null;
  instruct?: string;
  design?: string;
  language?: string;
  params?: Partial<TaskParams> & Record<string, unknown>;
}

export interface BatchCreateRequest {
  type: TaskType;
  items: BatchItemPayload[];
  defaults?: Partial<TaskParams> & Record<string, unknown>;
}

export interface BatchCreateResult {
  batch_id: string;
  task_ids: string[];
  total: number;
}

/* =========================== 引擎与模型 =========================== */
export type EngineStatus = 'ready' | 'not_installed' | 'package_missing' | 'error' | 'experimental';
export type EngineInstallPhase = 'download' | 'pip' | 'verify' | 'done' | 'error';

/** 单个 Python 依赖的真实探测结果（后端 importlib.metadata / find_spec 得到） */
export interface EnginePackageStatus {
  name: string;
  installed: boolean;
  version: string | null;
  import_name: string;
}

export interface EngineItem {
  id: string;
  name: string;
  vendor: string;
  params: string;
  kind_tags: string[];
  languages: string[];
  vram_gb: number;
  sample_rate: number;
  supports_stream: boolean;
  installed: boolean;
  size_mb: number;
  path: string;
  status: EngineStatus;
  detail: string;
  packages: string[];
  package_installed: boolean;
  notes: string;
  license?: string;
  commercial_use?: boolean | 'requires_agreement';
  total_mb?: number;
  /* ---- 依赖检测（可选：旧后端不返回时前端自动降级） ---- */
  packages_status?: EnginePackageStatus[];
  packages_ok?: boolean;
  packages_missing?: string[];
  main_package?: string[];
  torch_repair_command?: string;
  /* ---- 依赖隔离（venv）---- */
  /** 依赖探测来源：venv = 引擎自己的 venv，main = 主环境 */
  packages_source?: 'venv' | 'main';
  /** 隔离方式：venv = 独立虚拟环境 + 子进程推理；inprocess = 主环境就地推理（默认引擎） */
  isolation?: 'venv' | 'inprocess';
  venv_path?: string;
  venv_python?: string;
  venv_exists?: boolean;
  venv_ready?: boolean;
  venv_size_mb?: number;
  venv_constraints?: string;
  /** 该引擎的精确版本锁文件（`backend/app/engine/adapters/locks/<id>.txt`，逐行 包名==版本） */
  lock_file?: string;
  /** 锁文件里声明的 `包名==版本` 列表 */
  lock_packages?: string[];
  /** 实际安装版本是否与锁文件完全一致（venv 未创建/无锁文件时为 false） */
  versions_ok?: boolean;
  /** 与锁文件不一致的清单，格式「包名 锁定版本→实际版本」（或 →缺失） */
  versions_mismatch?: string[];
  /** 后端生成的**安全安装命令**（venv python 绝对路径 + -c 约束文件，不会碰主环境 torch） */
  install_command?: string;
  install_commands?: string[];
}

export interface EngineList {
  active: string;
  items: EngineItem[];
}

export interface EngineInstallStatus {
  engine_id: string;
  phase: EngineInstallPhase;
  progress: number;
  message: string;
  done: boolean;
  /** 后端 install/status 会返回：线程是否还在跑（轮询兜底用） */
  running?: boolean;
  job_id?: string;
}

/** 有效能力（后端按 run.mode 计算，前端门禁的唯一真源） */
export interface CapabilitySet {
  source: 'local' | 'cloud' | 'both';
  mode: string;
  caps: string[];
  missing: string[];
  detail: string;
  local_caps: string[];
  cloud_caps: string[];
  cloud_configured: boolean;
  active_engine?: string;
  cloud_models?: string;
  missing_hint?: string;
}

export interface EngineHealth {
  ready: boolean;
  detail: string;
  vram_allocated_mb: number;
}

/** 安装预览里的单个待下载文件（后端只做 HEAD 探测，不下载） */
export interface EnginePlanDownload {
  url: string;
  /** 备用镜像地址（后端可选返回） */
  urls?: string[];
  /** 相对模型目录的目标文件路径 */
  dest: string;
  size_mb: number;
  /** head = HEAD 实测体积；manifest = 清单里声明的体积 */
  size_source?: 'head' | 'manifest';
}

/** 代码仓库条目（git / zip 等） */
export interface EnginePlanCodeRepo {
  kind?: string;
  url?: string;
  label?: string;
  dest?: string;
}

/** `GET /api/engines/{id}/plan` 返回的安装计划 */
export interface EnginePlan {
  name: string;
  models_dir?: string;
  downloads: EnginePlanDownload[];
  total_mb: number;
  code_repo?: EnginePlanCodeRepo[];
  code_submodules?: EnginePlanCodeRepo[];
  package?: string[];
  requires_env?: Record<string, string>;
  lines?: string[];
}

export interface SettingsPayload {
  run: { mode: RunMode; hybrid_long_text_chars: number; asr_prefer: 'local' | 'cloud' };
  cloud: {
    base_url: string;
    api_key: string;
    verify_tls: boolean;
    models: { base: string; custom_voice: string; voice_design: string };
    tunnel: {
      enabled: boolean;
      ssh_host: string;
      ssh_port: number;
      username: string;
      key_path: string;
      password: string;
      local_port: number;
      remote_host: string;
      remote_port: number;
    };
    idle_shutdown_enabled: boolean;
    idle_shutdown_minutes: number;
  };
  asr: {
    engine: 'local' | 'cloud';
    local: { model_size: string; device: string; compute_type: string; beam_size: number };
    cloud: { base_url: string; api_key: string; model: string };
    diarize?: AsrDiarizeConfig;
  };
  defaults: TaskParams;
  local: {
    keep_loaded: boolean;
    warmup_on_start?: boolean;
    warmup_kinds?: string[];
    unload_after_idle_seconds: number;
    allow_small_fallback: boolean;
    dtype: string;
    device: string;
    vram_reserve_gb: number;
    allow_vram_overcommit?: boolean;
    low_vram?: { enabled: boolean; gpu_reserve_gb: number; cpu_memory_gb: number; offload_dir: string };
    models: { base: string; custom_voice: string; voice_design: string };
    fallback_models?: { base: string; custom_voice: string; voice_design: string };
  };
  tts?: { normalize: boolean; normalize_lang: string };
  post?: {
    enabled: boolean;
    low_cut_hz: number;
    high_shelf_db: number;
    compress: boolean;
    threshold_db: number;
    ratio: number;
    attack_ms: number;
    release_ms: number;
    normalize_peak_db: number | null;
  };
  qc?: { enabled: boolean; min_similarity: number };
  export?: { tier: string };
  limits: Meta['limits'];
  config_file: string;
  mutable: string[];
}

/* --------------------------- 云端引擎（远程管理） --------------------------- */
export interface CloudHostProfile {
  configured: boolean;
  /** 未保存时的预填值（来自「一键接入」或「SSH 隧道」配置） */
  prefill?: { host?: string; ssh_port?: number; username?: string; project_dir?: string; python?: string };
  /** 每个预填字段的来源：saved / agent / tunnel / default */
  sources?: Record<string, string>;
  host?: string;
  ssh_port?: number;
  username?: string;
  project_dir?: string;
  python?: string;
  has_password?: boolean;
  updated_at?: number;
}

export interface CloudEngineItem {
  id: string;
  name: string;
  status: string;
  size_mb: number;
  kind?: 'tts' | 'asr' | 'speaker';
  kind_tags?: string[];
  packages_ok?: boolean;
  venv_exists?: boolean;
  languages?: string[];
}

export interface CloudEngineList {
  active: string;
  /** 云端当前 ASR 引擎 / 声纹引擎（与 TTS 的 active 分开） */
  active_asr?: string;
  active_speaker?: string;
  host?: string;
  source?: string;
  items: CloudEngineItem[];
}

export interface CloudEngineJob {
  job_id: string;
  engine_id: string;
  status: 'running' | 'completed' | 'failed';
  phase?: string;
  progress: number;
  logs?: { ts: number; message: string }[];
  error?: string | null;
}
