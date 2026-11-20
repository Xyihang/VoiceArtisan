import React, { useEffect, useRef } from 'react';
import { Eraser, Sparkles } from 'lucide-react';
import { estimateSeconds } from '../lib/format';

const SAMPLES = [
  '欢迎使用声匠 VoiceArtisan。输入要合成的文本即可；能力取决于当前引擎，可在「设置 → 引擎与模型」切换。',
  '这是一段用于试听的中文示例：清晰、自然、有节奏感的播报，适合产品介绍与短视频配音。',
  'Hello there! This is a cross-lingual voice cloning demo — the same voice can speak both Chinese and English.',
];

type Props = {
  value: string;
  onChange: (value: string) => void;
  maxChars: number;
  disabled?: boolean;
  placeholder?: string;
  rows?: number;
  label?: React.ReactNode;
};

export function TextEditor({ value, onChange, maxChars, disabled, placeholder, label = '待合成文本' }: Props) {
  const ref = useRef<HTMLTextAreaElement | null>(null);

  const autoGrow = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(320, Math.max(132, el.scrollHeight))}px`;
  };

  useEffect(autoGrow, [value]);

  const insertSample = () => {
    const sample = SAMPLES[Math.floor(Math.random() * SAMPLES.length)];
    onChange(value ? `${value}\n${sample}` : sample);
    ref.current?.focus();
  };

  const over = value.length > maxChars;
  const ratio = Math.min(1, value.length / maxChars);
  const estimate = estimateSeconds(value);

  return (
    <div className="field">
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
        <label className="label" htmlFor="tts-text">
          {label}
        </label>
        <div className="toolbar" style={{ marginLeft: 'auto' }}>
          <button type="button" className="btn btn--ghost btn--sm" onClick={insertSample} disabled={disabled}>
            <Sparkles size={14} strokeWidth={1.75} /> 插入示例
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => onChange('')}
            disabled={disabled || !value}
          >
            <Eraser size={14} strokeWidth={1.75} /> 清空
          </button>
        </div>
      </div>
      <textarea
        id="tts-text"
        ref={ref}
        className="textarea"
        value={value}
        disabled={disabled}
        placeholder={placeholder || '输入要合成的文本，支持中英文混排；长文本会自动按句分段流式合成。'}
        onChange={(event) => {
          onChange(event.target.value);
          autoGrow();
        }}
        aria-describedby="tts-text-counter"
        spellCheck={false}
      />
      <div className="counter-row" id="tts-text-counter">
        <span>
          字数 <span className="num">{value.length}</span> / {maxChars}
        </span>
        <span>
          预估时长 <span className="num">{estimate.toFixed(1)}</span> s
        </span>
        <span style={{ flex: 1, maxWidth: 160 }}>
          <span className="progress" aria-hidden="true">
            <span
              className="progress__bar"
              style={{
                width: `${ratio * 100}%`,
                background: over ? 'var(--color-error)' : ratio > 0.9 ? 'var(--color-warning)' : undefined,
              }}
            />
          </span>
        </span>
        {over ? <span style={{ color: 'var(--color-error)' }}>已超出上限，仍可提交（后端会校验）</span> : null}
      </div>
    </div>
  );
}
