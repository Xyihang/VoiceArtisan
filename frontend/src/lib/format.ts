export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '--:--';
  const total = Math.max(0, Math.round(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return '--';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function formatSeconds(seconds: number | null | undefined, digits = 2): string {
  if (seconds == null || !Number.isFinite(seconds)) return '--';
  return `${seconds.toFixed(digits)} s`;
}

export function formatTime(ts: number | null | undefined): string {
  if (!ts) return '--';
  const date = new Date(ts * 1000);
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  const hh = date.getHours().toString().padStart(2, '0');
  const mm = date.getMinutes().toString().padStart(2, '0');
  const ss = date.getSeconds().toString().padStart(2, '0');
  if (sameDay) return `${hh}:${mm}:${ss}`;
  return `${date.getMonth() + 1}/${date.getDate()} ${hh}:${mm}`;
}

export function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function formatRelative(ts: number | null | undefined): string {
  if (!ts) return '--';
  const diff = Date.now() / 1000 - ts;
  if (diff < 10) return '刚刚';
  if (diff < 60) return `${Math.round(diff)} 秒前`;
  if (diff < 3600) return `${Math.round(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.round(diff / 3600)} 小时前`;
  return formatTime(ts);
}

/** 预估朗读时长（与后端 textsplit.estimate_seconds 一致：4.6 字/秒） */
export function estimateSeconds(text: string, charsPerSecond = 4.6): number {
  const chars = text.replace(/\s/g, '').length;
  return chars ? Math.round((chars / charsPerSecond) * 10) / 10 : 0;
}

/** 说话人配色：按 SPKn 编号取色，取不到编号时退回次要文字色 */
export const SPEAKER_COLORS = ['#3b82f6', '#ef6c3a', '#22a06b', '#8a63d2', '#c9a227', '#2aa6c0'];

export function speakerColor(speaker?: string | null): string {
  const hit = /(\d+)/.exec(speaker || '');
  if (!hit) return 'var(--color-text-secondary)';
  const index = Number(hit[1]) - 1;
  return SPEAKER_COLORS[((index % SPEAKER_COLORS.length) + SPEAKER_COLORS.length) % SPEAKER_COLORS.length];
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export const STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  canceled: '已取消',
};

export const TYPE_LABEL: Record<string, string> = {
  tts: '语音生成',
  clone: '声音克隆',
  design: '音色设计',
  asr: '音频转文字',
};

export const GENDER_LABEL: Record<string, string> = {
  male: '男声',
  female: '女声',
};

export const AGE_LABEL: Record<string, string> = {
  young: '青年',
  middle: '中年',
  senior: '老年',
};

export const LANGUAGE_LABEL: Record<string, string> = {
  chinese: '中文',
  english: '英文',
  japanese: '日语',
  korean: '韩语',
  french: '法语',
  german: '德语',
  spanish: '西班牙语',
  italian: '意大利语',
  portuguese: '葡萄牙语',
  russian: '俄语',
  beijing_dialect: '北京话',
  sichuan_dialect: '四川话',
  Auto: '自动',
};
