import React, { useMemo, useState } from 'react';
import { Check, Merge, Pencil, RotateCcw } from 'lucide-react';
import { api, ApiError } from '../lib/api';
import { formatSeconds, speakerColor } from '../lib/format';
import type { TaskSegment } from '../types';

function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return `${error.message}${error.hint ? ` ${error.hint}` : ''}`;
  return error instanceof Error ? error.message : fallback;
}

/**
 * 说话人分段列表 + 改名（SPK1 → 张三）+ 合并（把多个说话人并进目标）。
 * 没有 speaker 的片段不渲染任何东西，调用处不用再判空。
 */
export function SpeakerSegments({
  taskId,
  segments,
  onRenamed,
  onMerged,
}: {
  taskId: string;
  segments: TaskSegment[];
  onRenamed?: (segments: TaskSegment[]) => void;
  onMerged?: (segments: TaskSegment[]) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [merging, setMerging] = useState(false);
  const [mergeTarget, setMergeTarget] = useState('');
  const [mergeSources, setMergeSources] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const speakers = useMemo(() => {
    const seen = new Map<string, string>();
    for (const seg of segments) {
      const sid = (seg.speaker_id || seg.speaker || '').trim();
      if (!sid) continue;
      const display = (seg.speaker || sid).trim();
      if (!seen.has(sid)) seen.set(sid, display);
      else if (seen.get(sid) === sid && display !== sid) seen.set(sid, display);
    }
    return [...seen.entries()].map(([id, name]) => ({ id, name }));
  }, [segments]);

  if (!speakers.length) return null;

  const startEdit = () => {
    setMerging(false);
    setDraft(Object.fromEntries(speakers.map((s) => [s.id, s.name === s.id ? '' : s.name])));
    setError('');
    setEditing(true);
  };

  const startMerge = () => {
    setEditing(false);
    setError('');
    setMergeTarget(speakers[0].id);
    setMergeSources(speakers.slice(1).map((s) => s.id));
    setMerging(true);
  };

  const save = async (mapping: Record<string, string>) => {
    setBusy(true);
    setError('');
    try {
      const res = await api.renameSpeakers(taskId, mapping);
      onRenamed?.(res.segments);
      setEditing(false);
    } catch (exc) {
      setError(errorText(exc, '改名失败'));
    } finally {
      setBusy(false);
    }
  };

  const merge = async () => {
    if (!mergeTarget || !mergeSources.length) {
      setError('请选择要合并的来源说话人和目标说话人');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await api.mergeSpeakers(taskId, mergeSources, mergeTarget);
      onMerged?.(res.segments);
      setMerging(false);
    } catch (exc) {
      setError(errorText(exc, '合并失败'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="toolbar" style={{ marginBottom: 'var(--space-2)' }}>
        <h3 style={{ margin: 0 }}>说话人分段</h3>
        <div className="toolbar__spacer" />
        {editing ? (
          <>
            <button
              type="button"
              className="btn btn--sm btn--primary"
              disabled={busy}
              onClick={() => void save(draft)}
            >
              <Check size={13} strokeWidth={1.75} /> 保存名称
            </button>
            <button
              type="button"
              className="btn btn--sm"
              disabled={busy}
              onClick={() => void save(Object.fromEntries(speakers.map((s) => [s.id, ''])))}
              title="还原成 SPK1 / SPK2"
            >
              <RotateCcw size={13} strokeWidth={1.75} /> 还原
            </button>
            <button type="button" className="btn btn--sm btn--ghost" onClick={() => setEditing(false)}>
              取消
            </button>
          </>
        ) : merging ? (
          <>
            <button type="button" className="btn btn--sm btn--primary" disabled={busy} onClick={() => void merge()}>
              <Merge size={13} strokeWidth={1.75} /> 合并
            </button>
            <button type="button" className="btn btn--sm btn--ghost" onClick={() => setMerging(false)}>
              取消
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn btn--sm" onClick={startEdit} title="把 SPK1 改成真实姓名">
              <Pencil size={13} strokeWidth={1.75} /> 改名
            </button>
            {speakers.length > 1 ? (
              <button
                type="button"
                className="btn btn--sm"
                onClick={startMerge}
                title="把误判出来的说话人并进同一个（时间轴不变）"
              >
                <Merge size={13} strokeWidth={1.75} /> 合并到…
              </button>
            ) : null}
          </>
        )}
      </div>

      {editing ? (
        <div className="toolbar" style={{ marginBottom: 'var(--space-2)', flexWrap: 'wrap' }}>
          {speakers.map((sp) => (
            <label key={sp.id} className="field" style={{ minWidth: 160 }}>
              <span className="label">{sp.id}</span>
              <input
                className="input"
                value={draft[sp.id] ?? ''}
                placeholder={sp.id}
                onChange={(e) => setDraft((prev) => ({ ...prev, [sp.id]: e.target.value }))}
              />
            </label>
          ))}
        </div>
      ) : null}

      {merging ? (
        <div className="nested" style={{ display: 'grid', gap: 'var(--space-2)', marginBottom: 'var(--space-2)' }}>
          <div className="field-row">
            <div className="field">
              <label className="label" htmlFor={`merge-target-${taskId}`}>
                合并到（目标）
              </label>
              <select
                id={`merge-target-${taskId}`}
                className="select"
                value={mergeTarget}
                onChange={(event) => {
                  const next = event.target.value;
                  setMergeTarget(next);
                  setMergeSources((prev) => prev.filter((id) => id !== next));
                }}
              >
                {speakers.map((sp) => (
                  <option key={sp.id} value={sp.id}>
                    {sp.id}
                    {sp.name !== sp.id ? `（${sp.name}）` : ''}
                  </option>
                ))}
              </select>
            </div>
            <div className="field" style={{ flex: '2 1 260px' }}>
              <span className="label">把哪些说话人并进来</span>
              <div className="toolbar" style={{ flexWrap: 'wrap' }}>
                {speakers
                  .filter((sp) => sp.id !== mergeTarget)
                  .map((sp) => {
                    const checked = mergeSources.includes(sp.id);
                    return (
                      <label key={sp.id} className="checkbox">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(event) =>
                            setMergeSources((prev) =>
                              event.target.checked ? [...prev, sp.id] : prev.filter((id) => id !== sp.id),
                            )
                          }
                          aria-label={`把 ${sp.id} 合并到 ${mergeTarget}`}
                        />
                        {sp.id}
                      </label>
                    );
                  })}
              </div>
            </div>
          </div>
          <p className="hint" style={{ margin: 0 }}>
            合并只改说话人归属，分段起止时间与文本都不变；合并后可继续改名。
          </p>
        </div>
      ) : null}

      {error ? <p className="field-error">{error}</p> : null}

      <div style={{ display: 'grid', gap: 'var(--space-1)' }}>
        {segments.map((seg, index) => {
          const color = speakerColor(seg.speaker);
          return (
            <div
              key={`${index}-${seg.start_s}`}
              style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'baseline' }}
            >
              <span className="chip" style={{ flex: '0 0 auto', color, borderColor: color }}>
                {seg.speaker || 'SPK?'}
              </span>
              <span className="hint num" style={{ flex: '0 0 auto' }}>
                {formatSeconds(seg.start_s, 1)}
              </span>
              <span style={{ fontSize: 'var(--fs-13)' }}>{seg.text}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default SpeakerSegments;
