import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Mic,
  Pencil,
  Play,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import { api } from '../lib/api';
import { useApp } from '../state/AppContext';
import type { Asset, Voice } from '../types';
import { AGE_LABEL, GENDER_LABEL, LANGUAGE_LABEL, formatSeconds } from '../lib/format';
import { hasMod, modLabel } from '../lib/shortcuts';
import { ConfirmDialog, EmptyState, InlineNote, Modal, SectionCard, Skeleton } from '../components/ui';
import { RefAudio } from '../components/RefAudio';

type SortKey = 'recent' | 'name' | 'builtin';

const SORT_LABEL: Record<SortKey, string> = { recent: '最近使用', name: '名称', builtin: '内置优先' };
const SORTS: SortKey[] = ['recent', 'name', 'builtin'];
const DEFAULT_SORT: SortKey = 'recent';
const RECENT_KEY = 'qwentts.recentVoices';

/** 筛选/排序写进 URL：`#/voices?q=小明&sort=name`，刷新与前进后退都不丢 */
function readParams(): { q: string; sort: SortKey } {
  const raw = window.location.hash.replace(/^#\/?/, '');
  const index = raw.indexOf('?');
  const params = new URLSearchParams(index >= 0 ? raw.slice(index + 1) : '');
  const sort = params.get('sort') as SortKey | null;
  return {
    q: params.get('q') || '',
    sort: sort && SORTS.includes(sort) ? sort : DEFAULT_SORT,
  };
}

function writeParams(q: string, sort: SortKey): void {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (sort !== DEFAULT_SORT) params.set('sort', sort);
  const query = params.toString();
  const next = `#/voices${query ? `?${query}` : ''}`;
  if (window.location.hash === next) return;
  window.history.replaceState(null, '', next);
}

function readRecent(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : [];
  } catch {
    return [];
  }
}

function markRecent(id: string): string[] {
  const next = [id, ...readRecent().filter((item) => item !== id)].slice(0, 24);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* 隐私模式下忽略 */
  }
  return next;
}

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

export function VoicesPage() {
  const { pushToast, describeError, meta, settings } = useApp();
  const [builtin, setBuiltin] = useState<Voice[]>([]);
  const [custom, setCustom] = useState<Voice[]>([]);
  const [loading, setLoading] = useState(true);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<Voice | null>(null);
  const [newName, setNewName] = useState('');
  const [deleting, setDeleting] = useState<Voice | null>(null);
  const [busy, setBusy] = useState(false);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [recent, setRecent] = useState<string[]>(readRecent);
  const initial = useRef(readParams());
  const [query, setQuery] = useState(initial.current.q);
  const [sort, setSort] = useState<SortKey>(initial.current.sort);
  // 一屏克隆：参考音频 → 命名 → 保存
  const [refAsset, setRefAsset] = useState<Asset | null>(null);
  const [voiceName, setVoiceName] = useState('');
  const [refText, setRefText] = useState('');
  const [privacyAck, setPrivacyAck] = useState(false);
  const [creating, setCreating] = useState(false);
  const nameRef = useRef<HTMLInputElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const cloudMode = settings?.run.mode === 'cloud' || settings?.run.mode === 'hybrid';

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.voices();
      setBuiltin(data.builtin);
      setCustom(data.custom);
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    } finally {
      setLoading(false);
    }
  }, [describeError, pushToast]);

  useEffect(() => {
    void load();
  }, [load]);

  // 前进/后退：以 URL 为准
  useEffect(() => {
    const onHash = () => {
      const next = readParams();
      setQuery(next.q);
      setSort(next.sort);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const updateQuery = (next: string) => {
    setQuery(next);
    writeParams(next, sort);
  };

  const updateSort = (next: SortKey) => {
    setSort(next);
    writeParams(query, next);
  };

  const all = useMemo(() => [...builtin, ...custom], [builtin, custom]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? all.filter((voice) =>
          [voice.name, voice.description, voice.dialect, LANGUAGE_LABEL[voice.language] || voice.language]
            .filter(Boolean)
            .some((text) => String(text).toLowerCase().includes(q)),
        )
      : all.slice();
    const byName = (a: Voice, b: Voice) => a.name.localeCompare(b.name, 'zh-Hans-CN');
    if (sort === 'name') return filtered.sort(byName);
    if (sort === 'builtin') {
      return filtered.sort((a, b) => (a.kind === b.kind ? byName(a, b) : a.kind === 'builtin' ? -1 : 1));
    }
    // 最近使用：本机记录过的音色排前，其余按创建时间倒序
    const rank = new Map(recent.map((id, index) => [id, index]));
    return filtered.sort((a, b) => {
      const ra = rank.has(a.id) ? (rank.get(a.id) as number) : Number.MAX_SAFE_INTEGER;
      const rb = rank.has(b.id) ? (rank.get(b.id) as number) : Number.MAX_SAFE_INTEGER;
      if (ra !== rb) return ra - rb;
      return (b.created_at || 0) - (a.created_at || 0);
    });
  }, [all, query, recent, sort]);

  /* --------------------------- 试听 / 重命名 / 删除 --------------------------- */
  const preview = (voice: Voice) => {
    setRecent(markRecent(voice.id));
    if (!voice.has_audio) {
      pushToast({ level: 'info', title: '内置音色没有参考音频', description: '可在工作台用该音色生成一段试听。' });
      return;
    }
    audioRef.current?.pause();
    setPreviewId(voice.id);
    const audio = new Audio(api.voiceAudioUrl(voice.id));
    audioRef.current = audio;
    audio.addEventListener('ended', () => setPreviewId((prev) => (prev === voice.id ? null : prev)));
    void audio.play().catch(() => {
      setPreviewId(null);
      pushToast({ level: 'error', title: '试听失败', description: '音频文件可能已被移动或删除。' });
    });
  };

  const doRename = async () => {
    if (!renaming || !newName.trim()) return;
    setBusy(true);
    try {
      await api.renameVoice(renaming.id, newName.trim());
      setRenaming(null);
      await load();
      pushToast({ level: 'success', title: '已重命名' });
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.deleteVoice(deleting.id);
      setDeleting(null);
      await load();
      pushToast({ level: 'success', title: '音色已删除', description: deleting.name });
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    } finally {
      setBusy(false);
    }
  };

  /* --------------------------- 一屏克隆 --------------------------- */
  const canCreate = !!refAsset && !!voiceName.trim() && !creating;

  const createVoice = async () => {
    if (!refAsset || !voiceName.trim() || creating) return;
    setCreating(true);
    try {
      const response = await fetch(api.assetUrl(refAsset.id));
      if (!response.ok) throw new Error('无法读取已上传的参考音频，请重新上传');
      const blob = await response.blob();
      const created = await api.createVoice(blob, voiceName.trim(), refText, {}, refAsset.filename || 'ref.wav');
      setHighlightId(created.id);
      setRecent(markRecent(created.id));
      setRefAsset(null);
      setVoiceName('');
      setRefText('');
      setPrivacyAck(false);
      await load();
      setSort('recent');
      writeParams(query, 'recent');
      pushToast({ level: 'success', title: '音色已创建', description: `${voiceName.trim()} · 已高亮显示` });
      window.setTimeout(() => {
        document.getElementById(`voice-${created.id}`)?.scrollIntoView({
          block: 'center',
          behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        });
      }, 120);
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    } finally {
      setCreating(false);
    }
  };

  useEffect(() => {
    if (!highlightId) return;
    const timer = window.setTimeout(() => setHighlightId(null), 2800);
    return () => window.clearTimeout(timer);
  }, [highlightId]);

  const chips: { key: 'q' | 'sort'; label: string }[] = [];
  if (query.trim()) chips.push({ key: 'q', label: `搜索：${query.trim()}` });
  if (sort !== DEFAULT_SORT) chips.push({ key: 'sort', label: `排序：${SORT_LABEL[sort]}` });

  const renderCard = (voice: Voice) => {
    const isNew = voice.id === highlightId;
    const playing = voice.id === previewId;
    const builtinVoice = voice.kind === 'builtin';
    return (
      <div
        key={voice.id}
        id={`voice-${voice.id}`}
        className={isNew ? 'voice-card voice-card--new' : 'voice-card'}
        data-kind={voice.kind}
        onClick={(event) => {
          if ((event.target as HTMLElement).closest('button')) return;
          preview(voice);
        }}
      >
        <div className="voice-card__head">
          <span className="voice-card__name" title={voice.name}>
            {voice.name}
          </span>
          {builtinVoice ? <span className="voice-card__kind">内置</span> : <span className="voice-card__kind voice-card__kind--custom">克隆</span>}
        </div>
        <div className="voice-card__spark">
          <Spark peaks={voice.peaks} />
        </div>
        <div className="voice-card__desc">{voice.description || '—'}</div>
        <div className="voice-card__tags">
          {tagsOf(voice).map((tag) => (
            <span className="voice-card__tag" key={tag}>
              {tag}
            </span>
          ))}
          <span className="voice-card__tag num" title="参考音频时长">
            {voice.duration_sec ? formatSeconds(voice.duration_sec, 1) : '无音频'}
          </span>
          {playing ? <span className="voice-card__playing">试听中</span> : null}
        </div>
        <div className="voice-card__actions">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => preview(voice)}
            disabled={!voice.has_audio}
            title={voice.has_audio ? `试听 ${voice.name}` : '内置音色没有参考音频；可在工作台用该音色生成一段试听'}
            aria-label={`试听 ${voice.name}`}
          >
            {playing ? <Play size={12} strokeWidth={2} /> : <Mic size={12} strokeWidth={1.75} />} 试听
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => {
              setRenaming(voice);
              setNewName(voice.name);
            }}
            title="重命名"
            aria-label={`重命名 ${voice.name}`}
          >
            <Pencil size={12} strokeWidth={1.75} />
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm btn--danger-ghost"
            onClick={() => setDeleting(voice)}
            disabled={builtinVoice}
            title={builtinVoice ? '内置音色不可删除：它来自模型内置说话人列表，删除会破坏音色映射' : `删除 ${voice.name}（不可撤销）`}
            aria-label={builtinVoice ? `内置音色 ${voice.name} 不可删除` : `删除 ${voice.name}`}
          >
            <Trash2 size={12} strokeWidth={1.75} />
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="page">
      <div className="page__head">
        <div>
          <h1>音色库</h1>
          <p className="page__subtitle">
            内置 <span className="num">{builtin.length}</span> 个精选音色 + 自定义 <span className="num">{custom.length}</span> 个克隆音色，
            覆盖不同性别、年龄、语言与方言
          </p>
        </div>
        <div className="toolbar">
          <button type="button" className="btn btn--sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={14} strokeWidth={1.75} /> 刷新
          </button>
          <button
            type="button"
            className="btn btn--primary btn--sm"
            onClick={() => nameRef.current?.focus()}
            title="跳到下方「克隆新音色」的命名输入框"
          >
            <Upload size={14} strokeWidth={1.75} /> 新建自定义音色
          </button>
        </div>
      </div>

      <SectionCard
        title="克隆新音色"
        hint="上传参考音频 → 命名 → 保存，一屏完成"
        icon={<Mic size={15} strokeWidth={1.75} />}
        actions={<span className="hint">快捷键 <kbd className="kbd">{modLabel()}+Enter</kbd> 直接保存</span>}
      >
        <div
          className="clone-flow"
          onKeyDown={(event) => {
            if (hasMod(event) && event.key === 'Enter' && canCreate) {
              event.preventDefault();
              void createVoice();
            }
          }}
        >
          <div className="clone-step">
            <span className="clone-step__label">
              <span className="clone-step__no num">1</span> 上传参考音频
            </span>
            <RefAudio
              asset={refAsset}
              onChange={(next) => {
                setRefAsset(next);
                if (next && !voiceName) setVoiceName(next.filename.replace(/\.[^.]+$/, ''));
              }}
              maxSeconds={meta?.limits.max_ref_audio_seconds ?? 30}
              maxUploadMb={meta?.limits.max_upload_mb ?? 50}
              privacyRequired={cloudMode}
              privacyAck={privacyAck}
              onPrivacyAck={setPrivacyAck}
              onError={(error) => pushToast({ level: 'error', ...describeError(error) })}
            />
          </div>
          <div className="clone-step">
            <label className="clone-step__label" htmlFor="clone-name">
              <span className="clone-step__no num">2</span> 命名
            </label>
            <input
              id="clone-name"
              ref={nameRef}
              className="input"
              value={voiceName}
              onChange={(event) => setVoiceName(event.target.value)}
              placeholder="例如：深夜电台男声"
              aria-label="新音色名称"
            />
            <label className="clone-step__label" htmlFor="clone-ref-text">
              参考文本
              <span className="label__hint">可选，填写后克隆相似度更高</span>
            </label>
            <textarea
              id="clone-ref-text"
              className="textarea textarea--compact"
              value={refText}
              onChange={(event) => setRefText(event.target.value)}
              placeholder="参考音频里说的那句话"
              aria-label="参考文本"
            />
          </div>
          <div className="clone-step clone-step--save">
            <span className="clone-step__label">
              <span className="clone-step__no num">3</span> 保存
            </span>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => void createVoice()}
              disabled={!canCreate}
              title={
                !refAsset
                  ? '请先上传或录制参考音频'
                  : !voiceName.trim()
                    ? '请给音色起个名字'
                    : creating
                      ? '正在保存…'
                      : '保存为自定义音色'
              }
            >
              {creating ? '保存中…' : '创建音色'}
            </button>
            <span className="hint clone-step__hint">
              {!refAsset
                ? '① 还没上传音频'
                : !voiceName.trim()
                  ? '② 还没命名'
                  : `就绪：${formatSeconds(refAsset.duration_sec, 1)} 参考音频`}
            </span>
          </div>
        </div>
        <InlineNote kind="info">
          自定义音色来自你上传的参考音频，仅保存在本机数据目录；切换到云端模式合成时，参考音频会上传至云端推理服务（生成前会二次确认）。
        </InlineNote>
      </SectionCard>

      <SectionCard
        title="全部音色"
        hint={`显示 ${visible.length} / ${all.length} 个`}
        actions={
          <div className="toolbar">
            <div className="voice-search">
              <Search size={14} strokeWidth={1.75} aria-hidden="true" />
              <input
                className="input"
                value={query}
                onChange={(event) => updateQuery(event.target.value)}
                placeholder="搜索名称、方言、描述"
                aria-label="搜索音色"
              />
              {query ? (
                <button type="button" className="voice-search__clear" onClick={() => updateQuery('')} aria-label="清除搜索" title="清除搜索">
                  <X size={13} strokeWidth={2} />
                </button>
              ) : null}
            </div>
            <select
              className="select"
              style={{ width: 'auto' }}
              value={sort}
              onChange={(event) => updateSort(event.target.value as SortKey)}
              aria-label="排序方式"
            >
              {SORTS.map((key) => (
                <option key={key} value={key}>
                  {SORT_LABEL[key]}
                </option>
              ))}
            </select>
          </div>
        }
      >
        {chips.length ? (
          <div className="chip-row" style={{ marginTop: 0 }} aria-label="当前筛选">
            {chips.map((chip) => (
              <button
                key={chip.key}
                type="button"
                className="filter-chip"
                title={`移除筛选：${chip.label}`}
                aria-label={`移除筛选 ${chip.label}`}
                onClick={() => {
                  if (chip.key === 'q') updateQuery('');
                  else updateSort(DEFAULT_SORT);
                }}
              >
                {chip.label}
                <X size={12} strokeWidth={2} aria-hidden="true" />
              </button>
            ))}
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => {
                updateQuery('');
                updateSort(DEFAULT_SORT);
              }}
            >
              清除全部
            </button>
          </div>
        ) : null}

        {loading ? (
          <div className="voice-grid">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} height={148} radius={12} />
            ))}
          </div>
        ) : visible.length ? (
          <div className="voice-grid">{visible.map(renderCard)}</div>
        ) : all.length ? (
          <EmptyState
            icon={<Search size={28} strokeWidth={1.5} />}
            title="没有匹配的音色"
            description={`「${query.trim()}」没有命中任何音色，换个关键词或清除筛选。`}
            action={
              <button type="button" className="btn btn--sm" onClick={() => updateQuery('')}>
                清除搜索
              </button>
            }
          />
        ) : (
          <EmptyState
            icon={<Mic size={28} strokeWidth={1.5} />}
            title="还没有音色"
            description="上传 3-10 秒清晰人声，即可零样本克隆一个自己的音色。"
            action={
              <button type="button" className="btn btn--sm btn--primary" onClick={() => nameRef.current?.focus()}>
                <Upload size={13} strokeWidth={1.75} /> 上传参考音频
              </button>
            }
          />
        )}
      </SectionCard>

      {renaming ? (
        <Modal
          title="重命名音色"
          onClose={() => setRenaming(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setRenaming(null)}>
                取消
              </button>
              <button type="button" className="btn btn--primary" onClick={doRename} disabled={!newName.trim() || busy}>
                保存
              </button>
            </>
          }
        >
          <div className="field">
            <label className="label" htmlFor="rename-input">
              新名称
            </label>
            <input
              id="rename-input"
              className="input"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && newName.trim() && !busy) void doRename();
              }}
            />
          </div>
        </Modal>
      ) : null}

      {deleting ? (
        <ConfirmDialog
          title="确认删除这个音色？"
          confirmLabel="删除音色"
          busy={busy}
          onCancel={() => setDeleting(null)}
          onConfirm={() => void doDelete()}
        >
          <p>
            将删除自定义音色「<strong>{deleting.name}</strong>」及其参考音频文件。
          </p>
          <ul>
            <li>已用该音色生成的任务与音频不受影响（结果文件保留）。</li>
            <li>删除后无法恢复，需要重新上传参考音频再克隆。</li>
          </ul>
        </ConfirmDialog>
      ) : null}
    </div>
  );
}

export default VoicesPage;
