import React, { useEffect, useRef } from 'react';

type Props = {
  peaks: number[];
  progress?: number;
  onSeek?: (ratio: number) => void;
  height?: number;
  emptyText?: string;
  ariaLabel?: string;
};

/** 波形可视化：随流式分块增长，点击定位 */
export function Waveform({ peaks, progress = 0, onSeek, height = 72, emptyText, ariaLabel = '音频波形' }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const dpr = window.devicePixelRatio || 1;
    const width = wrap.clientWidth;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const styles = getComputedStyle(document.documentElement);
    const primary = styles.getPropertyValue('--color-primary').trim() || '#1f6feb';
    const muted = styles.getPropertyValue('--color-border-strong').trim() || '#cfd6df';
    const barWidth = 2;
    const gap = 2;
    const step = barWidth + gap;
    const count = Math.max(1, Math.floor(width / step));
    const mid = height / 2;

    if (!peaks.length) return;

    // 采样到画布宽度
    const sampled: number[] = [];
    for (let i = 0; i < count; i += 1) {
      const start = Math.floor((i / count) * peaks.length);
      const end = Math.max(start + 1, Math.floor(((i + 1) / count) * peaks.length));
      let max = 0;
      for (let j = start; j < end && j < peaks.length; j += 1) max = Math.max(max, peaks[j]);
      sampled.push(max);
    }

    sampled.forEach((value, index) => {
      const x = index * step;
      const barHeight = Math.max(2, value * (height - 8));
      ctx.fillStyle = index / count <= progress ? primary : muted;
      const radius = barWidth / 2;
      const top = mid - barHeight / 2;
      ctx.beginPath();
      if (typeof ctx.roundRect === 'function') {
        ctx.roundRect(x, top, barWidth, barHeight, radius);
        ctx.fill();
      } else {
        ctx.fillRect(x, top, barWidth, barHeight);
      }
    });
  }, [peaks, progress, height]);

  const handleClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!onSeek) return;
    const rect = event.currentTarget.getBoundingClientRect();
    onSeek(Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)));
  };

  return (
    <div
      className="waveform"
      ref={wrapRef}
      style={{ height }}
      onClick={handleClick}
      role={onSeek ? 'slider' : 'img'}
      aria-label={ariaLabel}
      aria-valuenow={onSeek ? Math.round(progress * 100) : undefined}
      aria-valuemin={onSeek ? 0 : undefined}
      aria-valuemax={onSeek ? 100 : undefined}
      tabIndex={onSeek ? 0 : undefined}
      onKeyDown={(event) => {
        if (!onSeek) return;
        if (event.key === 'ArrowRight') onSeek(Math.min(1, progress + 0.05));
        if (event.key === 'ArrowLeft') onSeek(Math.max(0, progress - 0.05));
      }}
    >
      <canvas ref={canvasRef} />
      {!peaks.length && emptyText ? <span className="waveform__hint">{emptyText}</span> : null}
    </div>
  );
}
