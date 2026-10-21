import React, { useEffect, useState } from 'react';
import { Download, PauseCircle, PlayCircle, RotateCcw } from 'lucide-react';
import { AudioEngine, base64ToArrayBuffer, type AudioEngineState } from '../lib/audio';
import { downloadFile } from '../lib/download';
import { formatDuration, formatMs } from '../lib/format';
import { Waveform } from './Waveform';

const EMPTY_STATE: AudioEngineState = { duration: 0, currentTime: 0, playing: false, peaks: [], chunkCount: 0 };

export function useAudioEngine() {
  const [engine] = useState(() => new AudioEngine());
  useEffect(() => () => engine.dispose(), [engine]);
  return engine;
}

export function useEngineState(engine: AudioEngine): AudioEngineState {
  const [state, setState] = useState<AudioEngineState>(EMPTY_STATE);
  useEffect(() => engine.subscribe(setState), [engine]);
  return state;
}

type Props = {
  engine: AudioEngine;
  title?: React.ReactNode;
  downloadUrl?: string | null;
  downloadName?: string | null;
  firstPacketMs?: number | null;
  backend?: string;
  model?: string;
  streaming?: boolean;
  truncated?: boolean;
  emptyText?: string;
  onSaveAsTask?: () => void;
};

export function AudioPlayer({
  engine,
  title,
  downloadUrl,
  downloadName,
  firstPacketMs,
  backend,
  model,
  streaming,
  truncated,
  emptyText = '生成后在此处播放',
  onSaveAsTask,
}: Props) {
  const state = useEngineState(engine);
  const progress = state.duration ? state.currentTime / state.duration : 0;

  return (
    <div className="player">
      <div className="player__controls">
        <button
          type="button"
          className="btn btn--primary player__play"
          onClick={() => engine.toggle()}
          disabled={!state.duration}
          aria-label={state.playing ? '暂停' : '播放'}
          title={state.playing ? '暂停' : '播放'}
        >
          {state.playing ? <PauseCircle size={22} strokeWidth={1.75} /> : <PlayCircle size={22} strokeWidth={1.75} />}
        </button>
        <div style={{ display: 'grid', gap: 2 }}>
          <span className="num" style={{ fontSize: 'var(--fs-13)' }}>
            {formatDuration(state.currentTime)} / {formatDuration(state.duration)}
          </span>
          <span className="player__time">
            {title || (streaming ? '流式生成中' : '音频结果')}
            {truncated ? <span className="player__truncated">（已截断）</span> : null}
          </span>
        </div>
        <div className="player__meta">
          {firstPacketMs != null ? (
            <span className="chip" title="首包延迟">
              首包 <span className="num">{formatMs(firstPacketMs)}</span>
            </span>
          ) : null}
          {backend ? <span className="chip">{backend === 'cloud' ? '云端' : backend === 'mock' ? '模拟' : '本地'}</span> : null}
          {streaming ? (
            <span className="stream-indicator">
              <span className="stream-bars" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              流式
            </span>
          ) : null}
          <button
            type="button"
            className="btn btn--ghost btn--icon btn--sm"
            onClick={() => engine.seek(0)}
            disabled={!state.duration}
            aria-label="从头播放"
            title="从头播放"
          >
            <RotateCcw size={14} strokeWidth={1.75} />
          </button>
          {downloadUrl ? (
            <button
              type="button"
              className="btn btn--ghost btn--icon btn--sm"
              onClick={() => {
                void downloadFile(downloadUrl, downloadName || 'result.wav').catch(() => {
                  /* 已兜底打开新窗口 */
                });
              }}
              aria-label="下载音频"
              title="下载音频"
            >
              <Download size={14} strokeWidth={1.75} />
            </button>
          ) : null}
          {onSaveAsTask ? (
            <button type="button" className="btn btn--sm" onClick={onSaveAsTask}>
              另存为任务
            </button>
          ) : null}
        </div>
      </div>
      <Waveform
        peaks={state.peaks}
        progress={progress}
        onSeek={(ratio) => engine.seek(ratio * state.duration)}
        emptyText={emptyText}
        height={76}
      />
      {model ? (
        <span className="hint" title="当前模型">
          模型：{model}
        </span>
      ) : null}
    </div>
  );
}

export { base64ToArrayBuffer };
