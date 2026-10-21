import React, { useState } from 'react';
import { ChevronDown, ChevronRight, RotateCcw, SlidersHorizontal } from 'lucide-react';
import type { TaskParams } from '../types';

type Props = {
  value: TaskParams;
  defaults: TaskParams;
  onChange: (patch: Partial<TaskParams>) => void;
  languages: { id: string; label: string }[];
  outputFormats: string[];
  disabled?: boolean;
  /** 展开态可由外部控制，便于记忆到 localStorage */
  expanded?: boolean;
  onExpandedChange?: (open: boolean) => void;
};

const SLIDERS: {
  key: keyof TaskParams;
  label: string;
  min: number;
  max: number;
  step: number;
  hint: string;
  format?: (value: number) => string;
}[] = [
  { key: 'speed', label: '语速', min: 0.5, max: 2, step: 0.05, hint: '1.0 为原速，0.8-1.2 为推荐区间', format: (v) => `${v.toFixed(2)}×` },
  { key: 'pitch', label: '音高', min: -12, max: 12, step: 1, hint: '半音为单位，±3 内自然', format: (v) => `${v > 0 ? '+' : ''}${v} 半音` },
  { key: 'temperature', label: '温度', min: 0.1, max: 1.5, step: 0.05, hint: '越高越有变化，0.7-1.0 稳定', format: (v) => v.toFixed(2) },
  { key: 'top_p', label: 'Top P', min: 0.3, max: 1, step: 0.05, hint: '核采样阈值，建议 0.8', format: (v) => v.toFixed(2) },
  { key: 'top_k', label: 'Top K', min: 1, max: 100, step: 1, hint: '采样候选数，建议 40-60', format: (v) => String(v) },
  { key: 'repetition_penalty', label: '重复惩罚', min: 1, max: 1.5, step: 0.01, hint: '略大于 1 可减少重复', format: (v) => v.toFixed(2) },
  { key: 'steps', label: '采样步数', min: 256, max: 4096, step: 128, hint: '生成长度上限（token），长文本适当调大', format: (v) => String(v) },
];

export function ParamsPanel({
  value,
  defaults,
  onChange,
  languages,
  outputFormats,
  disabled,
  expanded,
  onExpandedChange,
}: Props) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = expanded ?? internalOpen;
  const setOpen = (next: boolean) => {
    setInternalOpen(next);
    onExpandedChange?.(next);
  };
  const streaming = value.streaming !== false;
  const chunkChars = value.chunk_chars ?? 28;

  return (
    <section className="card">
      <header className="card__head" style={{ paddingBottom: 'var(--space-4)' }}>
        <div className="card__title">
          <SlidersHorizontal size={15} strokeWidth={1.75} />
          高级参数
        </div>
        <div className="card__actions">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => onChange({ ...defaults })}
            disabled={disabled}
            title="恢复默认参数"
          >
            <RotateCcw size={13} strokeWidth={1.75} /> 恢复默认
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            aria-controls="params-body"
          >
            {open ? <ChevronDown size={14} strokeWidth={1.75} /> : <ChevronRight size={14} strokeWidth={1.75} />}
            {open ? '收起' : '展开'}
          </button>
        </div>
      </header>
      {open ? (
        <div className="card__body" id="params-body">
          {SLIDERS.map((slider) => {
            const raw = value[slider.key];
            const current = typeof raw === 'number' ? raw : Number(raw) || 0;
            return (
              <div className="field" key={String(slider.key)}>
                <label className="label" htmlFor={`param-${String(slider.key)}`}>
                  {slider.label}
                  <span className="label__hint">{slider.hint}</span>
                </label>
                <div className="range-row">
                  <input
                    id={`param-${String(slider.key)}`}
                    type="range"
                    min={slider.min}
                    max={slider.max}
                    step={slider.step}
                    value={current}
                    disabled={disabled}
                    onChange={(event) => onChange({ [slider.key]: Number(event.target.value) } as Partial<TaskParams>)}
                  />
                  <span className="num">{slider.format ? slider.format(current) : current}</span>
                </div>
              </div>
            );
          })}
          <div className="field-row">
            <div className="field" style={{ flex: '2 1 280px' }}>
              <label className="label" htmlFor="param-streaming">
                流式合成
                <span className="label__hint">开：按句分块，首包即播；关：整段一次生成，语气更连贯</span>
              </label>
              <select
                id="param-streaming"
                className="select"
                value={streaming ? 'true' : 'false'}
                disabled={disabled}
                onChange={(event) => onChange({ streaming: event.target.value === 'true' })}
              >
                <option value="true">开启（首包快，可能有拼接感）</option>
                <option value="false">关闭（整段连贯，首包慢）</option>
              </select>
            </div>
            {streaming ? (
              <div className="field">
                <label className="label" htmlFor="param-chunk">
                  分块字数
                  <span className="label__hint">越小首包越快、拼接越多</span>
                </label>
                <div className="range-row">
                  <input
                    id="param-chunk"
                    type="range"
                    min={12}
                    max={80}
                    step={4}
                    value={chunkChars}
                    disabled={disabled}
                    onChange={(event) => onChange({ chunk_chars: Number(event.target.value) })}
                  />
                  <span className="num">{chunkChars}</span>
                </div>
              </div>
            ) : null}
          </div>
          <div className="field-row">
            <div className="field">
              <label className="label" htmlFor="param-num-step">
                采样步数 num_step
              </label>
              <input
                id="param-num-step"
                className="input"
                type="number"
                min={1}
                max={4096}
                placeholder="引擎默认"
                disabled={disabled}
                value={value.num_step ?? ''}
                onChange={(event) =>
                  onChange({ num_step: event.target.value === '' ? null : Number(event.target.value) })
                }
              />
            </div>
            <div className="field">
              <label className="label" htmlFor="param-guidance">
                引导强度 guidance_scale
              </label>
              <input
                id="param-guidance"
                className="input"
                type="number"
                step={0.1}
                min={0}
                max={20}
                placeholder="引擎默认"
                disabled={disabled}
                value={value.guidance_scale ?? ''}
                onChange={(event) =>
                  onChange({ guidance_scale: event.target.value === '' ? null : Number(event.target.value) })
                }
              />
            </div>
            <div className="field">
              <label className="label" htmlFor="param-denoise">
                降噪 denoise
              </label>
              <select
                id="param-denoise"
                className="select"
                disabled={disabled}
                value={value.denoise === null || value.denoise === undefined ? 'default' : String(value.denoise)}
                onChange={(event) =>
                  onChange({ denoise: event.target.value === 'default' ? null : event.target.value === 'true' })
                }
              >
                <option value="default">引擎默认</option>
                <option value="true">开启</option>
                <option value="false">关闭</option>
              </select>
            </div>
            <div className="field">
              <label className="label" htmlFor="param-target-duration">
                目标时长 target_duration_s
              </label>
              <input
                id="param-target-duration"
                className="input"
                type="number"
                step={0.5}
                min={0}
                placeholder="不控制"
                title="秒；优先级高于语速，生成后按目标时长做保音高伸缩 / 截断 / 补静音"
                disabled={disabled}
                value={value.target_duration_s ?? ''}
                onChange={(event) =>
                  onChange({ target_duration_s: event.target.value === '' ? null : Number(event.target.value) })
                }
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label className="label" htmlFor="param-export-tier">
                导出档位
              </label>
              <select
                id="param-export-tier"
                className="select"
                disabled={disabled}
                value={value.export_tier || 'inherit'}
                title="Original 保留模型原生采样率；Standard 转 44.1k MP3 192k；For editing 转 48k 24bit WAV"
                onChange={(event) =>
                  onChange({ export_tier: event.target.value === 'inherit' ? null : event.target.value })
                }
              >
                <option value="inherit">跟随设置</option>
                <option value="original">Original（模型原生 WAV）</option>
                <option value="standard">Standard（44.1k MP3 192k）</option>
                <option value="editing">For editing（48k 24bit WAV）</option>
              </select>
            </div>
            <div className="field">
              <label className="label" htmlFor="param-format">
                输出格式
              </label>
              <select
                id="param-format"
                className="select"
                value={value.output_format}
                disabled={disabled}
                onChange={(event) => onChange({ output_format: event.target.value })}
              >
                {outputFormats.map((fmt) => (
                  <option key={fmt} value={fmt}>
                    {fmt.toUpperCase()}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="label" htmlFor="param-language">
                语言
              </label>
              <select
                id="param-language"
                className="select"
                value={value.language}
                disabled={disabled}
                onChange={(event) => onChange({ language: event.target.value })}
              >
                {languages.map((lang) => (
                  <option key={lang.id} value={lang.id}>
                    {lang.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
