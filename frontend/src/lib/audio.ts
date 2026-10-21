/**
 * 流式音频播放引擎：
 * - 分块 WAV 到达即解码并排队播放（首包即播）
 * - 波形随音频增长实时扩展
 * - 支持播放/暂停/跳转/停止；取消后保留已生成片段
 */
export type AudioEngineState = {
  duration: number;
  currentTime: number;
  playing: boolean;
  peaks: number[];
  chunkCount: number;
};

const PEAKS_PER_SECOND = 48;

export class AudioEngine {
  private ctx: AudioContext | null = null;
  private buffers: AudioBuffer[] = [];
  private offsets: number[] = [];
  private sources: AudioBufferSourceNode[] = [];
  private nextScheduledAt = 0;
  private startedAt = 0;
  private startOffset = 0;
  private peakBuckets: number[] = [];
  private raf = 0;
  private listeners = new Set<(state: AudioEngineState) => void>();
  private playing = false;
  private pausedAt = 0;

  private ensureCtx(): AudioContext {
    if (!this.ctx) {
      const Ctor = window.AudioContext || (window as any).webkitAudioContext;
      this.ctx = new Ctor();
    }
    return this.ctx;
  }

  private emit() {
    const state = this.snapshot();
    this.listeners.forEach((h) => h(state));
  }

  subscribe(handler: (state: AudioEngineState) => void): () => void {
    this.listeners.add(handler);
    handler(this.snapshot());
    return () => this.listeners.delete(handler);
  }

  snapshot(): AudioEngineState {
    return {
      duration: this.duration,
      currentTime: this.currentTime,
      playing: this.playing,
      peaks: this.peakBuckets.slice(),
      chunkCount: this.buffers.length,
    };
  }

  get duration(): number {
    return this.buffers.reduce((acc, b) => acc + b.duration, 0);
  }

  get currentTime(): number {
    if (!this.ctx) return this.pausedAt;
    if (!this.playing) return Math.min(this.pausedAt, this.duration);
    return Math.min(this.duration, this.ctx.currentTime - this.startedAt + this.startOffset);
  }

  /** 追加一段 wav（ArrayBuffer），返回该段时长 */
  async appendWav(data: ArrayBuffer): Promise<number> {
    const ctx = this.ensureCtx();
    const buffer = await ctx.decodeAudioData(data.slice(0));
    const start = this.offsets.length
      ? this.offsets[this.offsets.length - 1] + this.buffers[this.buffers.length - 1].duration
      : 0;
    this.buffers.push(buffer);
    this.offsets.push(start);
    this.extendPeaks(buffer);
    if (this.playing) {
      this.scheduleBuffer(buffer, Math.max(this.nextScheduledAt, ctx.currentTime + 0.02));
    }
    this.emit();
    return buffer.duration;
  }

  private extendPeaks(buffer: AudioBuffer) {
    const channel = buffer.getChannelData(0);
    const buckets = Math.max(8, Math.round(buffer.duration * PEAKS_PER_SECOND));
    const step = Math.max(1, Math.floor(channel.length / buckets));
    let max = 0.0001;
    const local: number[] = [];
    for (let i = 0; i < buckets; i += 1) {
      let peak = 0;
      const start = i * step;
      const end = Math.min(channel.length, start + step);
      for (let j = start; j < end; j += 4) {
        const v = Math.abs(channel[j]);
        if (v > peak) peak = v;
      }
      local.push(peak);
      if (peak > max) max = peak;
    }
    this.peakBuckets.push(...local.map((v) => v / max));
  }

  private scheduleBuffer(buffer: AudioBuffer, when: number) {
    const ctx = this.ensureCtx();
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    source.start(when);
    source.onended = () => {
      this.sources = this.sources.filter((s) => s !== source);
    };
    this.sources.push(source);
    this.nextScheduledAt = when + buffer.duration;
  }

  private startLoop() {
    cancelAnimationFrame(this.raf);
    const tick = () => {
      this.emit();
      if (this.playing) this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  async play(from?: number) {
    const ctx = this.ensureCtx();
    if (ctx.state === 'suspended') await ctx.resume();
    const offset = from ?? this.pausedAt;
    this.stopSources();
    this.playing = true;
    this.startOffset = Math.max(0, Math.min(offset, this.duration));
    this.startedAt = ctx.currentTime;
    this.nextScheduledAt = ctx.currentTime + 0.02;
    for (let i = 0; i < this.buffers.length; i += 1) {
      const buffer = this.buffers[i];
      const start = this.offsets[i];
      const end = start + buffer.duration;
      if (end <= this.startOffset) continue;
      if (start >= this.startOffset) {
        this.scheduleBuffer(buffer, this.nextScheduledAt);
      } else {
        const skip = this.startOffset - start;
        const part = buffer;
        const source = ctx.createBufferSource();
        source.buffer = part;
        source.connect(ctx.destination);
        source.start(this.nextScheduledAt, skip);
        this.sources.push(source);
        this.nextScheduledAt += part.duration - skip;
      }
    }
    this.startLoop();
    this.emit();
  }

  pause() {
    this.pausedAt = this.currentTime;
    this.playing = false;
    this.stopSources();
    cancelAnimationFrame(this.raf);
    this.emit();
  }

  toggle() {
    if (this.playing) {
      this.pause();
    } else {
      if (this.pausedAt >= this.duration - 0.05) this.pausedAt = 0;
      void this.play(this.pausedAt);
    }
  }

  seek(time: number) {
    const target = Math.max(0, Math.min(time, this.duration));
    if (this.playing) {
      void this.play(target);
    } else {
      this.pausedAt = target;
      this.emit();
    }
  }

  private stopSources() {
    this.sources.forEach((s) => {
      try {
        s.stop();
      } catch {
        /* 已停止 */
      }
    });
    this.sources = [];
  }

  /** 载入完整音频（任务结果回放） */
  async loadUrl(url: string) {
    const response = await fetch(url);
    if (!response.ok) throw new Error('音频加载失败');
    const data = await response.arrayBuffer();
    this.reset();
    await this.appendWav(data);
    this.pausedAt = 0;
    this.emit();
  }

  reset() {
    this.stopSources();
    cancelAnimationFrame(this.raf);
    this.buffers = [];
    this.offsets = [];
    this.peakBuckets = [];
    this.playing = false;
    this.pausedAt = 0;
    this.startOffset = 0;
    this.emit();
  }

  dispose() {
    this.reset();
    this.listeners.clear();
    void this.ctx?.close();
    this.ctx = null;
  }
}

export function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}
