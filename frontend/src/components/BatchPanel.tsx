import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, ListChecks } from 'lucide-react';
import { api, ApiError } from '../lib/api';
import type { BatchItemPayload, TaskType, Voice } from '../types';
import { Modal, ProgressBar, Skeleton, Spinner } from './ui';
import { VoicePicker } from './VoicePicker';

type BatchItem = {
  index: number;
  text: string;
  task_id: string;
  status: string;
  error_code?: string | null;
  error_message?: string;
};

type BatchProgress = {
  batch_id: string;
  total: number;
  counts: Record<string, number>;
  progress: number;
  items: BatchItem[];
};

/** 单行覆盖：留空 = 跟随整批默认 */
type RowOverride = {
  voice_id: string;
  speed: string;
  language: string;
  instruct: string;
};

const EMPTY_ROW: RowOverride = { voice_id: '', speed: '', language: '', instruct: '' };

const STORE_BATCH = 'qwentts.batch.current';
const STATUS_TEXT: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  canceled: '已取消',
};

const BATCH_TYPES: { id: TaskType; label: string }[] = [
  { id: 'tts', label: '语音生成' },
  { id: 'clone', label: '声音克隆' },
  { id: 'design', label: '音色设计' },
];

function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return `${error.message}${error.hint ? ` ${error.hint}` : ''}`;
  return error instanceof Error ? error.message : fallback;
}

/** 批量提交：一行文本 = 一条线索；整批共用音色/参数，单行可展开覆盖；进度/取消/重试失败项都在这里。 */
export function BatchPanel({ onDone }: { onDone?: () => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [type, setType] = useState<TaskType>('tts');
  const [speed, setSpeed] = useState('1.0');
  const [language, setLanguage] = useState('');
  const [instruct, setInstruct] = useState('');
  const [voiceId, setVoiceId] = useState('');
  const [voices, setVoices] = useState<{ builtin: Voice[]; custom: Voice[]; languages: { id: string; label: string }[] }>({
    builtin: [],
    custom: [],
    languages: [],
  });
  const [overrides, setOverrides] = useState<Record<number, RowOverride>>({});
  const [expandedRow, setExpandedRow] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [batchId, setBatchId] = useState<string | null>(() => localStorage.getItem(STORE_BATCH));
  const [progress, setProgress] = useState<BatchProgress | null>(null);

  const lines = useMemo(() => text.split('\n').map((l) => l.trim()).filter(Boolean), [text]);
  const needInstruct = type === 'clone' || type === 'design';

  const remember = (id: string | null) => {
    setBatchId(id);
    if (id) localStorage.setItem(STORE_BATCH, id);
    else localStorage.removeItem(STORE_BATCH);
  };

  // 打开面板时拉一次音色（逐行覆盖要用）
  useEffect(() => {
    if (!open || voices.builtin.length || voices.custom.length) return;
    let alive = true;
    void api
      .voices()
      .then((data) => {
        if (alive) setVoices({ builtin: data.builtin, custom: data.custom, languages: data.languages });
      })
      .catch(() => {
        /* 音色列表拿不到时只是不能逐行改音色，不阻塞批量提交 */
      });
    return () => {
      alive = false;
    };
  }, [open, voices.builtin.length, voices.custom.length]);

  const voiceName = useCallback(
    (id: string) => [...voices.builtin, ...voices.custom].find((voice) => voice.id === id)?.name || id,
    [voices],
  );

  const loadProgress = useCallback(async (id: string) => {
    try {
      setProgress((await api.batchProgress(id)) as BatchProgress);
    } catch {
      /* 进度是辅助信息，失败静默 */
    }
  }, []);

  // 有批次就轮询进度；全部结束（progress=1）也继续低频刷新，方便看失败原因
  useEffect(() => {
    if (!open || !batchId) return;
    void loadProgress(batchId);
    const timer = window.setInterval(() => void loadProgress(batchId), 2500);
    return () => window.clearInterval(timer);
  }, [open, batchId, loadProgress]);

  const patchRow = (index: number, patch: Partial<RowOverride>) => {
    setOverrides((prev) => ({ ...prev, [index]: { ...EMPTY_ROW, ...prev[index], ...patch } }));
  };

  const submit = async () => {
    if (!lines.length) {
      setError('请至少输入一行文本');
      return;
    }
    const parsed = Number(speed);
    const batchSpeed = Number.isFinite(parsed) && parsed > 0 ? parsed : 1.0;
    setBusy(true);
    setError('');
    try {
      const items: BatchItemPayload[] = lines.map((line, index) => {
        const row = overrides[index];
        const item: BatchItemPayload = { text: line };
        const picked = row?.voice_id || voiceId;
        if (picked) item.voice_id = picked;
        const lang = (row?.language || language || '').trim();
        if (lang) item.language = lang;
        const instruction = (row?.instruct || '').trim() || instruct.trim();
        if (instruction && needInstruct) item.instruct = instruction;
        const rowSpeed = row?.speed?.trim() ? Number(row.speed) : Number.NaN;
        item.params = { speed: Number.isFinite(rowSpeed) ? rowSpeed : batchSpeed };
        return item;
      });
      const data = await api.batchCreate({ type, items, defaults: { speed: batchSpeed } });
      remember(data.batch_id);
      setProgress(null);
      onDone?.();
    } catch (exc) {
      setError(errorText(exc, '批量提交失败'));
    } finally {
      setBusy(false);
    }
  };

  const cancelBatch = async () => {
    if (!batchId) return;
    setBusy(true);
    try {
      await api.batchCancel(batchId);
      await loadProgress(batchId);
      onDone?.();
    } catch (exc) {
      setError(errorText(exc, '取消失败'));
    } finally {
      setBusy(false);
    }
  };

  const retryFailed = async () => {
    if (!batchId) return;
    setBusy(true);
    setError('');
    try {
      const data = await api.batchRetryFailed(batchId);
      remember(data.batch_id);
      await loadProgress(data.batch_id);
      onDone?.();
    } catch (exc) {
      setError(errorText(exc, '重试失败'));
    } finally {
      setBusy(false);
    }
  };

  const counts = progress?.counts || {};
  const finished = progress ? progress.progress >= 1 : false;
  const overrideCount = Object.values(overrides).filter(
    (row) => row.voice_id || row.speed.trim() || row.language || row.instruct.trim(),
  ).length;

  return (
    <>
      <button type="button" className="btn btn--sm" onClick={() => setOpen(true)}>
        <ListChecks size={14} strokeWidth={1.75} /> 批量提交
      </button>

      {open ? (
        <Modal title="批量提交" onClose={() => setOpen(false)}>
          <div className="field-row">
            <div className="field" style={{ flex: '1 1 100%' }}>
              <label className="label" htmlFor="batch-text">
                每行一条线索（最多 50 条，合计 5 万字）
              </label>
              <textarea
                id="batch-text"
                className="input"
                rows={8}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={'第一条文本\n第二条文本\n第三条文本'}
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label className="label" htmlFor="batch-type">
                批量类型
              </label>
              <select
                id="batch-type"
                className="select"
                value={type}
                onChange={(e) => setType(e.target.value as TaskType)}
              >
                {BATCH_TYPES.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="label" htmlFor="batch-voice">
                整批音色
                <span className="label__hint">留空 = 跟随引擎默认；单行可展开覆盖</span>
              </label>
              <select id="batch-voice" className="select" value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
                <option value="">跟随默认</option>
                {voices.builtin.length ? (
                  <optgroup label="内置音色">
                    {voices.builtin.map((voice) => (
                      <option key={voice.id} value={voice.id}>
                        {voice.name}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
                {voices.custom.length ? (
                  <optgroup label="自定义音色">
                    {voices.custom.map((voice) => (
                      <option key={voice.id} value={voice.id}>
                        {voice.name}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
              </select>
            </div>
            <div className="field">
              <label className="label" htmlFor="batch-speed">
                整批语速
              </label>
              <input
                id="batch-speed"
                className="input"
                type="number"
                step={0.05}
                min={0.5}
                max={2}
                value={speed}
                onChange={(e) => setSpeed(e.target.value)}
              />
            </div>
            <div className="field">
              <label className="label" htmlFor="batch-language">
                整批语言
                <span className="label__hint">留空 = 跟随各任务默认</span>
              </label>
              <select
                id="batch-language"
                className="select"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
              >
                <option value="">跟随默认</option>
                {voices.languages.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {needInstruct ? (
            <div className="field-row">
              <div className="field" style={{ flex: '1 1 100%' }}>
                <label className="label" htmlFor="batch-instruct">
                  整批指令（instruct）
                  <span className="label__hint">克隆 / 设计时可用，单行可覆盖</span>
                </label>
                <input
                  id="batch-instruct"
                  className="input"
                  value={instruct}
                  placeholder="例如：语气更温柔一些，语速稍慢"
                  onChange={(e) => setInstruct(e.target.value)}
                />
              </div>
            </div>
          ) : null}

          <div className="toolbar" style={{ marginTop: 'var(--space-2)' }}>
            <span className="hint">
              共 {lines.length} 条 · 已单独覆盖 <span className="num">{overrideCount}</span> 条
            </span>
          </div>

          {lines.length ? (
            <div className="batch-rows" aria-label="逐行覆盖">
              {lines.map((line, index) => {
                const row = overrides[index] || EMPTY_ROW;
                const openRow = expandedRow === index;
                const changed = !!(row.voice_id || row.speed.trim() || row.language || row.instruct.trim());
                return (
                  <div className="batch-row" key={`${index}-${line.slice(0, 8)}`}>
                    <div className="batch-row__head">
                      <span className="hint num" style={{ flex: '0 0 auto' }}>
                        #{index + 1}
                      </span>
                      <span className="batch-row__text" title={line}>
                        {line}
                      </span>
                      {changed ? <span className="badge badge--info">已覆盖</span> : null}
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        aria-expanded={openRow}
                        aria-controls={`batch-row-${index}`}
                        aria-label={`${openRow ? '收起' : '展开'}第 ${index + 1} 行的覆盖设置`}
                        onClick={() => setExpandedRow(openRow ? null : index)}
                      >
                        {openRow ? <ChevronDown size={13} strokeWidth={1.75} /> : <ChevronRight size={13} strokeWidth={1.75} />}
                        {openRow ? '收起' : '覆盖'}
                      </button>
                    </div>
                    {openRow ? (
                      <div className="nested" id={`batch-row-${index}`} style={{ display: 'grid', gap: 'var(--space-2)' }}>
                        <div className="field-row">
                          <div className="field">
                            <label className="label" htmlFor={`batch-row-speed-${index}`}>
                              语速
                            </label>
                            <input
                              id={`batch-row-speed-${index}`}
                              className="input"
                              type="number"
                              step={0.05}
                              min={0.5}
                              max={2}
                              value={row.speed}
                              placeholder={speed}
                              onChange={(e) => patchRow(index, { speed: e.target.value })}
                            />
                          </div>
                          <div className="field">
                            <label className="label" htmlFor={`batch-row-language-${index}`}>
                              语言
                            </label>
                            <select
                              id={`batch-row-language-${index}`}
                              className="select"
                              value={row.language}
                              onChange={(e) => patchRow(index, { language: e.target.value })}
                            >
                              <option value="">跟随整批</option>
                              {voices.languages.map((item) => (
                                <option key={item.id} value={item.id}>
                                  {item.label}
                                </option>
                              ))}
                            </select>
                          </div>
                          {needInstruct ? (
                            <div className="field" style={{ flex: '2 1 240px' }}>
                              <label className="label" htmlFor={`batch-row-instruct-${index}`}>
                                指令（instruct）
                              </label>
                              <input
                                id={`batch-row-instruct-${index}`}
                                className="input"
                                value={row.instruct}
                                placeholder={instruct || '跟随整批'}
                                onChange={(e) => patchRow(index, { instruct: e.target.value })}
                              />
                            </div>
                          ) : null}
                        </div>
                        <div className="field">
                          <span className="label">
                            音色
                            <span className="label__hint">
                              当前：{row.voice_id ? voiceName(row.voice_id) : voiceId ? voiceName(voiceId) : '跟随默认'}
                            </span>
                          </span>
                          <VoicePicker
                            builtin={voices.builtin}
                            custom={voices.custom}
                            languages={voices.languages}
                            value={row.voice_id || voiceId || null}
                            onChange={(voice) => patchRow(index, { voice_id: voice.id })}
                          />
                        </div>
                        <div className="toolbar">
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            onClick={() => {
                              setOverrides((prev) => {
                                const next = { ...prev };
                                delete next[index];
                                return next;
                              });
                            }}
                          >
                            清除本行覆盖
                          </button>
                        </div>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : null}

          {error ? <p className="field-error">{error}</p> : null}

          <div className="probe-bar">
            <span className="hint">共 {lines.length} 条</span>
            <button type="button" className="btn btn--sm btn--primary" onClick={() => void submit()} disabled={busy}>
              {busy ? <Spinner size={12} /> : null}
              {busy ? '提交中…' : '开始批量生成'}
            </button>
          </div>

          {batchId ? (
            <div className="nested" style={{ display: 'grid', gap: 'var(--space-2)', marginTop: 'var(--space-3)' }}>
              <div className="toolbar">
                <strong style={{ fontSize: 'var(--fs-13)' }}>批次 {batchId}</strong>
                <div className="toolbar__spacer" />
                {progress ? (
                  <span className="hint">
                    进行中 {(counts.running || 0) + (counts.queued || 0)} · 完成 {counts.completed || 0} · 失败{' '}
                    {counts.failed || 0} · 取消 {counts.canceled || 0}
                  </span>
                ) : (
                  <span className="hint">读取进度…</span>
                )}
              </div>
              {progress ? <ProgressBar value={progress.progress} /> : <Skeleton height={8} radius={4} />}
              <div className="toolbar">
                <a className="btn btn--sm" href={`/api/tasks/batch/${batchId}/zip`}>
                  下载打包产出
                </a>
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={() => void retryFailed()}
                  disabled={busy || !counts.failed}
                  title={counts.failed ? '把失败项复制成一个新批次重跑' : '没有失败项'}
                >
                  重试失败项
                </button>
                <button
                  type="button"
                  className="btn btn--sm btn--danger"
                  onClick={() => void cancelBatch()}
                  disabled={busy || finished}
                  title={finished ? '批次已结束' : '取消批次里所有未结束的任务'}
                >
                  取消批次
                </button>
                <button
                  type="button"
                  className="btn btn--sm btn--ghost"
                  onClick={() => {
                    remember(null);
                    setProgress(null);
                  }}
                >
                  清空当前批次
                </button>
              </div>
              {progress ? (
                <div style={{ display: 'grid', gap: 'var(--space-1)', maxHeight: 220, overflowY: 'auto' }}>
                  {progress.items.map((item) => (
                    <div key={item.index} style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'baseline' }}>
                      <span className="hint num" style={{ flex: '0 0 auto' }}>
                        #{item.index + 1}
                      </span>
                      <span className="hint" style={{ flex: '0 0 auto', minWidth: 56 }}>
                        {STATUS_TEXT[item.status] || item.status}
                      </span>
                      <span style={{ fontSize: 'var(--fs-13)', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {item.error_message || item.text}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </Modal>
      ) : null}
    </>
  );
}

export default BatchPanel;
