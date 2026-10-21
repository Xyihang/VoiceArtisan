import type {
  Asset,
  BatchCreateRequest,
  BatchCreateResult,
  EngineHealth,
  EngineInstallStatus,
  EngineList,
  CloudEngineJob,
  CloudEngineList,
  CloudHostProfile,
  EnginePlan,
  ExportTier,
  Health,
  Meta,
  SettingsPayload,
  SpeakerMergeResult,
  SpeakerSplitItem,
  SystemStatus,
  Task,
  TaskExportResult,
  TaskList,
  TaskSegment,
  VoicesResponse,
} from '../types';

export class ApiError extends Error {
  code: string;
  hint: string;
  status: number;

  constructor(message: string, code = 'REQUEST_FAILED', hint = '', status = 0) {
    super(message);
    this.code = code;
    this.hint = hint;
    this.status = status;
  }
}

const BASE = '';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(BASE + path, {
      ...init,
      headers: {
        ...(init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
        ...(init?.headers || {}),
      },
    });
  } catch (err) {
    throw new ApiError('网络异常，无法连接后端服务', 'NETWORK', '请确认后端已启动，且证书已信任。', 0);
  }
  if (!response.ok) {
    let detail: any = null;
    try {
      detail = await response.json();
    } catch {
      /* ignore */
    }
    const payload = detail?.detail ?? detail ?? {};
    const message = typeof payload === 'string' ? payload : payload.message || `请求失败（HTTP ${response.status}）`;
    throw new ApiError(message, payload.code || 'REQUEST_FAILED', payload.hint || '', response.status);
  }
  if (response.status === 204) return undefined as T;
  const type = response.headers.get('content-type') || '';
  if (type.includes('application/json')) return (await response.json()) as T;
  return (await response.blob()) as unknown as T;
}

export const api = {
  health: () => request<Health>('/api/health'),
  /** 完整状态：asr_candidates（实际尝试顺序）与 config.asr_diarize 是说话人链路的判定依据 */
  status: () => request<SystemStatus>('/api/system/status'),
  selfcheck: () => request<any>('/api/system/selfcheck'),
  logs: (limit = 200, level?: string) =>
    request<{ items: { ts: number; level: string; logger: string; message: string }[] }>(
      `/api/system/logs?limit=${limit}${level ? `&level=${level}` : ''}`,
    ),
  meta: () => request<Meta>('/api/meta'),
  voices: (kind = 'all', q = '') =>
    request<VoicesResponse>(`/api/voices?kind=${kind}${q ? `&q=${encodeURIComponent(q)}` : ''}`),
  createTask: (body: Record<string, unknown>) =>
    request<Task>('/api/tasks', { method: 'POST', body: JSON.stringify(body) }),
  listTasks: (params: Record<string, string | number>) => {
    const query = new URLSearchParams(
      Object.entries(params).reduce<Record<string, string>>((acc, [k, v]) => {
        acc[k] = String(v);
        return acc;
      }, {}),
    );
    return request<TaskList>(`/api/tasks?${query.toString()}`);
  },
  getTask: (id: string) => request<Task>(`/api/tasks/${id}`),
  /** 成品真实规格：采样率/声道/位深/编码/体积（后端读文件头） */
  mediaInfo: (id: string) =>
    request<{
      file: string;
      bytes: number;
      sample_rate: number | null;
      channels: number | null;
      bits: number | null;
      codec: string | null;
      duration_sec: number | null;
      tier: string | null;
      tier_label: string | null;
    }>(`/api/tasks/${id}/media-info`),
  cancelTask: (id: string) => request<{ ok: boolean }>(`/api/tasks/${id}/cancel`, { method: 'POST' }),
  retryTask: (id: string, force_backend?: string, params?: Record<string, unknown>) =>
    request<Task>(`/api/tasks/${id}/retry`, {
      method: 'POST',
      body: JSON.stringify({ force_backend: force_backend ?? null, params: params ?? {} }),
    }),
  deleteTask: (id: string) => request<{ ok: boolean }>(`/api/tasks/${id}`, { method: 'DELETE' }),
  /** 说话人改名：mapping {"SPK1": "张三"}，空字符串表示还原 */
  renameSpeakers: (id: string, mapping: Record<string, string>) =>
    request<{ ok: boolean; segments: TaskSegment[]; speaker_aliases: Record<string, string> }>(
      `/api/tasks/${id}/speakers`,
      { method: 'PATCH', body: JSON.stringify({ mapping }) },
    ),
  /** 合并说话人：from 里的编号并入 to，时间轴不变 */
  mergeSpeakers: (id: string, sources: string[], to: string) =>
    request<SpeakerMergeResult>(`/api/tasks/${id}/speakers/merge`, {
      method: 'POST',
      body: JSON.stringify({ from: sources, to }),
    }),
  /** 按档位重新导出（从无损原件重编码，原件不动） */
  exportTask: (id: string, tier: ExportTier) =>
    request<TaskExportResult>(`/api/tasks/${id}/export`, {
      method: 'POST',
      body: JSON.stringify({ tier }),
    }),
  /** 按说话人拆分：直接返回分轨清单 */
  splitBySpeaker: (id: string) =>
    request<SpeakerSplitItem[]>(`/api/tasks/${id}/split-by-speaker`, { method: 'POST' }),
  /** 分轨打包下载（需先拆分），走 fetch+Blob 下载 */
  speakersZipUrl: (id: string) => `/api/tasks/${id}/speakers/zip`,
  /** 单条分轨：走文件路由（后端已做文件名白名单），不再让浏览器解压 zip */
  speakerFileUrl: (id: string, name: string) =>
    `/api/tasks/${id}/speakers/${encodeURIComponent(name)}`,
  /** 批量提交：items 每项都能覆盖音色 / 语言 / 指令 / 参数 */
  batchCreate: (body: BatchCreateRequest) =>
    request<BatchCreateResult>('/api/tasks/batch', { method: 'POST', body: JSON.stringify(body) }),
  batchProgress: (id: string) => request<any>(`/api/tasks/batch/${id}`),
  batchCancel: (id: string) =>
    request<{ ok: boolean; canceled: string[]; skipped: string[] }>(`/api/tasks/batch/${id}/cancel`, {
      method: 'POST',
    }),
  batchRetryFailed: (id: string) =>
    request<{ batch_id: string; task_ids: string[]; total: number }>(`/api/tasks/batch/${id}/retry-failed`, {
      method: 'POST',
    }),
  batchDelete: (ids: string[]) =>
    request<{ ok: boolean; deleted: number }>('/api/tasks/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  uploadAsset: (file: File | Blob, filename = 'audio.wav') => {
    const form = new FormData();
    form.append('file', file, filename);
    return request<Asset>('/api/assets', { method: 'POST', body: form });
  },
  trimAsset: (assetId: string, start: number, end: number) =>
    request<Asset>(`/api/assets/${assetId}/trim`, {
      method: 'POST',
      body: JSON.stringify({ start, end }),
    }),
  createVoice: (file: File | Blob, name: string, refText = '', meta: Record<string, string> = {}, filename = 'ref.wav') => {
    const form = new FormData();
    form.append('file', file, filename);
    form.append('name', name);
    form.append('ref_text', refText);
    form.append('meta', JSON.stringify(meta));
    return request<{ id: string }>('/api/voices', { method: 'POST', body: form });
  },
  renameVoice: (id: string, name: string) =>
    request<{ id: string }>(`/api/voices/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  deleteVoice: (id: string) => request<{ ok: boolean }>(`/api/voices/${id}`, { method: 'DELETE' }),
  settings: () => request<SettingsPayload>('/api/settings'),
  updateSettings: (patch: Record<string, unknown>) =>
    request<SettingsPayload>('/api/settings', { method: 'PUT', body: JSON.stringify(patch) }),
  switchMode: (mode: string) =>
    request<{ mode: string; health: Health }>('/api/settings/mode', {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),
  resetSettings: () => request<SettingsPayload>('/api/settings/reset', { method: 'POST' }),
  /** 后端侧探测云端（自动尝试隧道/直连、http/https）——浏览器在隧道/自签证书下探不了 */
  cloudProbe: () =>
    request<{
      ok: boolean;
      via: string;
      url: string;
      latency_ms: number;
      status: number;
      ready?: boolean;
      model?: string;
    }>('/api/cloud/probe', { method: 'POST' }),
  stopCloud: (body: { stop_tunnel?: boolean; action?: 'stop_service' | 'shutdown' } = {}) =>
    request<{
      ok: boolean;
      stopped: boolean;
      action?: string;
      shutting_down?: boolean;
      host: string;
      port_closed: boolean;
      tunnel_stopped: boolean;
      message: string;
    }>('/api/cloud/stop', { method: 'POST', body: JSON.stringify(body) }),

  preload: (kinds: string[]) =>
    request<{
      result: Record<string, string>;
      detail: Record<string, { status: string; path?: string | null; degraded?: boolean; offload?: boolean }>;
      note?: string;
      loaded: string[];
      elapsed_ms: number;
      vram_allocated_mb: number;
    }>('/api/system/preload', {
      method: 'POST',
      body: JSON.stringify({ kinds }),
    }),
  unload: (kinds: string[] = []) =>
    request<{ ok: boolean; loaded: string[] }>('/api/system/unload', {
      method: 'POST',
      body: JSON.stringify({ kinds }),
    }),
  downloadUrl: (taskId: string) => `/api/tasks/${taskId}/download`,
  /** 字幕导出：按任务记录的时间轴生成 srt / vtt / ass */
  subtitleUrl: (taskId: string, format: 'srt' | 'vtt' | 'ass' = 'srt', speakers = true) =>
    `/api/tasks/${taskId}/subtitles?format=${format}&speakers=${speakers ? 'true' : 'false'}`,
  fileUrl: (taskId: string, filename: string) => `/api/files/${taskId}/${encodeURIComponent(filename)}`,
  assetUrl: (assetId: string) => `/api/assets/${assetId}`,
  voiceAudioUrl: (voiceId: string) => `/api/voices/${voiceId}/audio`,
  /* --------------------------- 引擎与模型 --------------------------- */
  listEngines: () => request<EngineList>('/api/engines'),
  installEngine: (id: string) =>
    request<{ job_id: string; engine_id: string }>(`/api/engines/${encodeURIComponent(id)}/install`, {
      method: 'POST',
    }),
  installStatus: (id: string) =>
    request<EngineInstallStatus>(`/api/engines/${encodeURIComponent(id)}/install/status`),
  removeEngine: (id: string) =>
    request<{ ok: boolean; freed_mb: number }>(`/api/engines/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  selectEngine: (id: string) =>
    request<{ ok: boolean; active: string }>(`/api/engines/${encodeURIComponent(id)}/select`, { method: 'POST' }),
  engineHealth: (id: string) => request<EngineHealth>(`/api/engines/${encodeURIComponent(id)}/health`),
  /** 安装预览：后端只 HEAD 探测体积，不下载任何权重（响应是 `{ok,engine_id,plan}`，这里解包成 plan） */
  /* --------------------------- 云端引擎（远程管理） --------------------------- */
  /** 云端主机档案（密码永远脱敏，只有 has_password 布尔） */
  cloudHost: () => request<CloudHostProfile>('/api/cloud/engines/host'),
  saveCloudHost: (body: Record<string, unknown>) =>
    request<CloudHostProfile>('/api/cloud/engines/host', { method: 'PUT', body: JSON.stringify(body) }),
  testCloudHost: () =>
    request<{
      ok: boolean;
      host: string;
      ssh_port: number;
      username: string;
      fingerprint: string;
      python: string;
      project_dir: string;
      project_present: boolean;
    }>('/api/cloud/engines/host/test', { method: 'POST' }),
  /** 云端引擎列表（模型在云端主机上，不含本机） */
  cloudEngines: (kind?: string) =>
    request<CloudEngineList>(`/api/cloud/engines${kind ? `?kind=${kind}` : ''}`),
  installCloudEngine: (id: string) =>
    request<{ job_id: string; engine_id: string }>(
      `/api/cloud/engines/${encodeURIComponent(id)}/install`,
      { method: 'POST' },
    ),
  cloudEngineJob: (jobId: string) =>
    request<CloudEngineJob>(`/api/cloud/engines/jobs/${encodeURIComponent(jobId)}`),
  removeCloudEngine: (id: string) =>
    request<{ ok: boolean; engine_id: string; message?: string }>(
      `/api/cloud/engines/${encodeURIComponent(id)}`,
      { method: 'DELETE' },
    ),
  selectCloudEngine: (id: string) =>
    request<{ ok: boolean; engine_id: string }>(
      `/api/cloud/engines/${encodeURIComponent(id)}/select`,
      { method: 'POST' },
    ),
  enginePlan: (id: string) =>
    request<{ ok: boolean; engine_id: string; plan: EnginePlan }>(
      `/api/engines/${encodeURIComponent(id)}/plan`,
    ).then((res) => res.plan),
};
