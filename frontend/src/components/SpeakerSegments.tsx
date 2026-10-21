import React, { useMemo, useState } from 'react';
import { Check, Pencil, RotateCcw } from 'lucide-react';
import { api } from '../lib/api';
import { formatSeconds, speakerColor } from '../lib/format';
import type { TaskSegment } from '../types';

/**
 * 说话人分段列表 + 改名（SPK1 → 张三）。
 * 没有 speaker 的片段不渲染任何东西，调用处不用再判空。
 */
export function SpeakerSegments({
  taskId,
  segments,
  onRenamed,
}: {
  taskId: string;
  segments: TaskSegment[];
  onRenamed?: (segments: TaskSegment[]) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
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
    setDraft(Object.fromEntries(speakers.map((s) => [s.id, s.name === s.id ? '' : s.name])));
    setError('');
    setEditing(true);
  };

  const save = async (mapping: Record<string, string>) => {
    setBusy(true);
    setError('');
    try {
      const res = await api.renameSpeakers(taskId, mapping);
      onRenamed?.(res.segments as TaskSegment[]);
      setEditing(false);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
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
        ) : (
          <button type="button" className="btn btn--sm" onClick={startEdit} title="把 SPK1 改成真实姓名">
            <Pencil size={13} strokeWidth={1.75} /> 改名
          </button>
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
