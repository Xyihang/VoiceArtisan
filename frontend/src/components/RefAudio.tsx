import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Mic, Square, Scissors, UploadCloud, Volume2, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import type { Asset } from '../types';
import { formatSeconds } from '../lib/format';
import { InlineNote, Spinner } from './ui';
import { Waveform } from './Waveform';

type Props = {
  asset: Asset | null;
  onChange: (asset: Asset | null) => void;
  maxSeconds: number;
  maxUploadMb: number;
  privacyRequired: boolean;
  privacyAck: boolean;
  onPrivacyAck: (ack: boolean) => void;
  onError: (error: unknown) => void;
};

export function RefAudio({
  asset,
  onChange,
  maxSeconds,
  maxUploadMb,
  privacyRequired,
  privacyAck,
  onPrivacyAck,
  onError,
}: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordSeconds, setRecordSeconds] = useState(0);
  const [range, setRange] = useState<[number, number]>([0, 0]);
  const [previewProgress, setPreviewProgress] = useState(0);

  useEffect(() => {
    setRange([0, asset?.duration_sec || 0]);
  }, [asset?.id, asset?.duration_sec]);

  useEffect(() => {
    if (!recording) return;
    const timer = window.setInterval(() => setRecordSeconds((s) => s + 0.1), 100);
    return () => window.clearInterval(timer);
  }, [recording]);

  const uploadFile = useCallback(
    async (file: File | Blob, filename: string) => {
      setBusy(true);
      try {
        const result = await api.uploadAsset(file, filename);
        if (result.duration_sec > maxSeconds + 0.5) {
          onError(new Error(`参考音频 ${formatSeconds(result.duration_sec, 1)} 超出上限 ${maxSeconds}s，请裁剪后重试`));
        }
        onChange(result);
      } catch (error) {
        onError(error);
      } finally {
        setBusy(false);
      }
    },
    [maxSeconds, onChange, onError],
  );

  const handleFiles = useCallback(
    (files: FileList | null) => {
      const file = files?.[0];
      if (!file) return;
      if (file.size > maxUploadMb * 1024 * 1024) {
        onError(new Error(`文件超过 ${maxUploadMb}MB 上限`));
        return;
      }
      void uploadFile(file, file.name);
    },
    [maxUploadMb, onError, uploadFile],
  );

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        setRecording(false);
        setRecordSeconds(0);
        void uploadFile(blob, 'recording.webm');
      };
      recorder.start();
      recorderRef.current = recorder;
      setRecording(true);
      setRecordSeconds(0);
    } catch (error) {
      onError(new Error('无法访问麦克风，请检查浏览器权限（HTTPS 下才可用）'));
    }
  };

  const stopRecording = () => {
    recorderRef.current?.stop();
    recorderRef.current = null;
  };

  const applyTrim = async () => {
    if (!asset) return;
    setBusy(true);
    try {
      const trimmed = await api.trimAsset(asset.id, range[0], range[1]);
      onChange(trimmed);
    } catch (error) {
      onError(error);
    } finally {
      setBusy(false);
    }
  };

  const playPreview = () => {
    if (!asset) return;
    const audio = audioRef.current;
    if (!audio || !audio.src.includes(asset.id)) {
      const element = new Audio(api.assetUrl(asset.id));
      audioRef.current = element;
      element.addEventListener('timeupdate', () => {
        setPreviewProgress(element.duration ? element.currentTime / element.duration : 0);
      });
      void element.play();
      return;
    }
    if (audio.paused) void audio.play();
    else audio.pause();
  };

  return (
    <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
      <input
        ref={inputRef}
        type="file"
        accept="audio/*"
        className="visually-hidden"
        aria-label="选择参考音频文件"
        onChange={(event) => handleFiles(event.target.files)}
      />
      {!asset ? (
        <div
          className="dropzone"
          data-dragging={dragging}
          role="button"
          tabIndex={0}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click();
          }}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            handleFiles(event.dataTransfer.files);
          }}
        >
          {busy ? <Spinner size={20} /> : <UploadCloud size={22} strokeWidth={1.5} />}
          <span className="dropzone__title">拖拽音频到此处，或点击选择文件</span>
          <span className="hint">
            推荐 3-10 秒清晰人声 · 支持 wav / mp3 / flac / m4a · 上限 {maxUploadMb}MB
          </span>
          <span className="toolbar" style={{ marginTop: 'var(--space-1)' }}>
            <button
              type="button"
              className={`btn btn--sm ${recording ? 'btn--danger' : ''}`}
              onClick={(event) => {
                event.stopPropagation();
                if (recording) stopRecording();
                else void startRecording();
              }}
            >
              {recording ? <Square size={12} strokeWidth={2} /> : <Mic size={12} strokeWidth={1.75} />}
              {recording ? `停止录音 ${recordSeconds.toFixed(1)}s` : '浏览器内录音'}
            </button>
          </span>
        </div>
      ) : (
        <>
          <div className="nested" style={{ display: 'grid', gap: 'var(--space-3)' }}>
            <div className="toolbar">
              <strong style={{ fontSize: 'var(--fs-13)' }}>{asset.filename || '参考音频'}</strong>
              <span className="chip">
                <span className="num">{formatSeconds(asset.duration_sec, 2)}</span>
              </span>
              <span className="chip num">{asset.sample_rate} Hz</span>
              <div className="toolbar__spacer" />
              <button type="button" className="btn btn--ghost btn--sm" onClick={playPreview}>
                <Volume2 size={13} strokeWidth={1.75} /> 试听
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => {
                  audioRef.current?.pause();
                  onChange(null);
                  setPreviewProgress(0);
                }}
              >
                <Trash2 size={13} strokeWidth={1.75} /> 移除
              </button>
            </div>
            <Waveform
              peaks={asset.peaks}
              progress={previewProgress}
              onSeek={(ratio) => {
                if (audioRef.current) {
                  audioRef.current.currentTime = ratio * audioRef.current.duration;
                }
              }}
              height={64}
              ariaLabel="参考音频波形"
            />
            <div className="field">
              <label className="label">
                <Scissors size={13} strokeWidth={1.75} /> 选区裁剪
                <span className="label__hint">
                  {formatSeconds(range[0], 1)} → {formatSeconds(range[1], 1)}
                </span>
              </label>
              <div style={{ display: 'grid', gap: 'var(--space-1)' }}>
                <input
                  type="range"
                  min={0}
                  max={asset.duration_sec}
                  step={0.05}
                  value={range[0]}
                  onChange={(event) => setRange([Math.min(Number(event.target.value), range[1] - 0.2), range[1]])}
                  aria-label="裁剪起点"
                />
                <input
                  type="range"
                  min={0}
                  max={asset.duration_sec}
                  step={0.05}
                  value={range[1]}
                  onChange={(event) => setRange([range[0], Math.max(Number(event.target.value), range[0] + 0.2)])}
                  aria-label="裁剪终点"
                />
              </div>
              <div className="toolbar">
                <button type="button" className="btn btn--sm" onClick={applyTrim} disabled={busy}>
                  {busy ? <Spinner size={13} /> : <Scissors size={13} strokeWidth={1.75} />} 应用裁剪
                </button>
                <span className="hint">推荐最终保留 3-10 秒、无背景噪音的纯净人声。</span>
              </div>
              <span className="hint">
                未填写参考文本时使用「说话人嵌入」模式克隆；如需更高相似度，可在音色库新建音色时补充参考文本（ICL 模式）。
              </span>
            </div>
          </div>
        </>
      )}

      {privacyRequired ? (
        <InlineNote
          kind={privacyAck ? 'info' : 'warning'}
          action={
            <label className="checkbox">
              <input
                type="checkbox"
                checked={privacyAck}
                onChange={(event) => onPrivacyAck(event.target.checked)}
                aria-label="确认参考音频上云"
              />
              我已知悉并同意该参考音频上传至云端推理服务
            </label>
          }
        >
          当前为云端/混合模式：参考音频会被上传到云端 GPU 进行克隆推理，请确认不含敏感信息。
        </InlineNote>
      ) : null}
    </div>
  );
}
