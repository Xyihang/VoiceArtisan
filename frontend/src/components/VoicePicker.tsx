import React, { useMemo, useState } from 'react';
import { Check, Mic, Pencil, Search, Trash2, Upload } from 'lucide-react';
import type { Voice } from '../types';
import { AGE_LABEL, GENDER_LABEL, LANGUAGE_LABEL } from '../lib/format';
import { EmptyState, InlineNote } from './ui';

type Props = {
  builtin: Voice[];
  custom: Voice[];
  value: string | null;
  onChange: (voice: Voice) => void;
  onRename?: (voice: Voice) => void;
  onDelete?: (voice: Voice) => void;
  onCreate?: () => void;
  languages: { id: string; label: string }[];
};

function tagsOf(voice: Voice): string[] {
  const language = voice.dialect ? voice.dialect : LANGUAGE_LABEL[voice.language] || voice.language;
  return [GENDER_LABEL[voice.gender], AGE_LABEL[voice.age], voice.accent, language].filter(
    (tag, index, arr) => !!tag && arr.indexOf(tag) === index,
  ) as string[];
}

function Spark({ peaks }: { peaks: number[] }) {
  const bars = peaks.length ? peaks.slice(0, 28) : Array.from({ length: 28 }, (_, i) => 0.25 + 0.2 * Math.sin(i));
  return (
    <svg viewBox="0 0 56 14" width="100%" height="100%" preserveAspectRatio="none" aria-hidden="true">
      {bars.map((value, index) => (
        <rect
          key={index}
          x={index * 2}
          y={7 - Math.max(1, value * 6)}
          width={1.2}
          height={Math.max(2, value * 12)}
          rx={0.6}
          fill="var(--color-border-strong)"
        />
      ))}
    </svg>
  );
}

/** 伪音色 id：表示「不使用参考音色，纯文本合成」 */
export const NO_VOICE_ID = 'none';

export function VoicePicker({ builtin, custom, value, onChange, onRename, onDelete, onCreate, languages }: Props) {
  const [query, setQuery] = useState('');
  const [gender, setGender] = useState<string>('all');
  const [language, setLanguage] = useState<string>('all');

  const filter = (voice: Voice) => {
    if (gender !== 'all' && voice.gender !== gender) return false;
    if (language !== 'all' && voice.language !== language) return false;
    if (!query) return true;
    const q = query.toLowerCase();
    return (
      voice.name.toLowerCase().includes(q) ||
      voice.description.toLowerCase().includes(q) ||
      (voice.dialect || '').toLowerCase().includes(q)
    );
  };

  const shownBuiltin = useMemo(() => builtin.filter(filter), [builtin, query, gender, language]);
  const shownCustom = useMemo(() => custom.filter(filter), [custom, query, gender, language]);
  const usedLanguages = useMemo(() => {
    const ids = new Set(builtin.map((v) => v.language).filter(Boolean));
    return languages.filter((l) => ids.has(l.id));
  }, [builtin, languages]);

  const renderCard = (voice: Voice) => {
    const selected = voice.id === value;
    const unavailable = voice.available === false;
    return (
      <div
        key={voice.id}
        role="button"
        tabIndex={0}
        className="voice-card"
        aria-pressed={selected}
        onClick={() => !unavailable && onChange(voice)}
        onKeyDown={(event) => {
          if ((event.key === 'Enter' || event.key === ' ') && !unavailable) {
            event.preventDefault();
            onChange(voice);
          }
        }}
        style={unavailable ? { opacity: 0.55, cursor: 'not-allowed' } : undefined}
      >
        <div className="voice-card__name">{voice.name}</div>
        <div className="voice-card__spark">
          <Spark peaks={voice.peaks} />
        </div>
        <div className="voice-card__desc">{voice.description || (voice.kind === 'custom' ? '自定义克隆音色 · 未填写描述' : '—')}</div>
        <div className="voice-card__tags">
          {tagsOf(voice).map((tag) => (
            <span className="chip" key={tag} style={{ height: 20, fontSize: 11 }}>
              {tag}
            </span>
          ))}
          {voice.kind === 'custom' ? (
            <span className="badge badge--info" style={{ height: 20, fontSize: 11 }}>
              <Mic size={10} strokeWidth={2} /> 克隆
            </span>
          ) : null}
          {selected ? (
            <span className="badge badge--success" style={{ height: 20, fontSize: 11 }}>
              <Check size={10} strokeWidth={2.5} /> 已选
            </span>
          ) : null}
        </div>
        {voice.kind === 'custom' && (onRename || onDelete) ? (
          <div className="toolbar" style={{ gap: 'var(--space-1)' }}>
            {onRename ? (
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={(event) => {
                  event.stopPropagation();
                  onRename(voice);
                }}
                aria-label={`重命名 ${voice.name}`}
              >
                <Pencil size={12} strokeWidth={1.75} /> 重命名
              </button>
            ) : null}
            {onDelete ? (
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={(event) => {
                  event.stopPropagation();
                  onDelete(voice);
                }}
                aria-label={`删除 ${voice.name}`}
              >
                <Trash2 size={12} strokeWidth={1.75} /> 删除
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  };

  return (
    <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
      <div className="toolbar">
        <div style={{ position: 'relative', flex: '1 1 200px', minWidth: 160 }}>
          <Search
            size={14}
            strokeWidth={1.75}
            style={{
              position: 'absolute',
              left: 'var(--space-3)',
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--color-text-muted)',
            }}
          />
          <input
            className="input"
            style={{ paddingLeft: 'var(--space-6)' }}
            placeholder="搜索音色名称、方言、描述"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="搜索音色"
          />
        </div>
        <select className="select" style={{ width: 'auto' }} value={gender} onChange={(e) => setGender(e.target.value)} aria-label="按性别筛选">
          <option value="all">全部性别</option>
          <option value="female">女声</option>
          <option value="male">男声</option>
        </select>
        <select className="select" style={{ width: 'auto' }} value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="按语言筛选">
          <option value="all">全部语言</option>
          {usedLanguages.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
          {usedLanguages.length === 0 ? <option value="chinese">中文</option> : null}
        </select>
        {onCreate ? (
          <button type="button" className="btn btn--sm" onClick={onCreate}>
            <Upload size={14} strokeWidth={1.75} /> 新建自定义音色
          </button>
        ) : null}
      </div>

      {builtin.length === 0 && custom.length === 0 ? (
        <EmptyState title="没有匹配的音色" description="换个关键词，或先创建一个自定义音色。" />
      ) : (
        <>
          <div>
            <h3 style={{ marginBottom: 'var(--space-2)' }}>内置音色</h3>
            {builtin.length > 0 && builtin.every((v) => v.available === false) ? (
              <button
                type="button"
                className="voice-plain"
                aria-pressed={value === NO_VOICE_ID}
                onClick={() =>
                  onChange({
                    id: NO_VOICE_ID,
                    name: '纯文本合成（不使用音色）',
                    kind: 'custom',
                    speaker: null,
                    gender: '',
                    age: '',
                    language: 'Auto',
                    dialect: '',
                    accent: '',
                    description: '不提供参考音色，直接把文本合成为语音（多数引擎支持）',
                    ref_text: '',
                    has_audio: false,
                    peaks: [],
                    duration_sec: null,
                  } as unknown as Voice)
                }
                style={{ marginBottom: 'var(--space-2)', textAlign: 'left', width: '100%' }}
              >
                <strong>纯文本合成（不使用音色）</strong>
                <div className="voice-card__desc">
                  当前引擎没有可用内置音色时，可直接用文本合成；音色由模型自行决定。
                </div>
              </button>
            ) : null}
            {shownBuiltin.length ? (
              <div className="voice-grid">{shownBuiltin.map(renderCard)}</div>
            ) : (
              <p className="hint">没有匹配的内置音色</p>
            )}
          </div>
          <div>
            <h3 style={{ marginBottom: 'var(--space-2)' }}>自定义音色</h3>
            {shownCustom.length ? (
              <div className="voice-grid">{shownCustom.map(renderCard)}</div>
            ) : (
              <div className="nested">
                <p className="hint" style={{ marginBottom: 'var(--space-2)' }}>
                  还没有自定义音色：上传 3-10 秒参考音频即可零样本克隆。
                </p>
                {onCreate ? (
                  <button type="button" className="btn btn--sm" onClick={onCreate}>
                    <Upload size={14} strokeWidth={1.75} /> 上传参考音频
                  </button>
                ) : null}
              </div>
            )}
          </div>
        </>
      )}
      {builtin.some((v) => v.available === false) ? (
        <InlineNote kind="info">
          部分内置音色未在当前模型说话人列表中，选择后可能回退到默认音色。加载 CustomVoice 1.7B 模型后可用列表会自动更新。
        </InlineNote>
      ) : null}
    </div>
  );
}
