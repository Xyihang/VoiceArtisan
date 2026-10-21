import React, { useCallback, useEffect, useState } from 'react';
import { ListChecks } from 'lucide-react';
import { Modal, ProgressBar, Skeleton, Spinner } from './ui';

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

const STORE_BATCH = 'qwentts.batch.current';
const STATUS_TEXT: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  canceled: '已取消',
};

/** 批量提交：一行文本 = 一条线索；速度整批统一，进度/取消/重试失败项都在这里。 */
export function BatchPanel({ onDone }: { onDone?: () => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [speed, setSpeed] = useState('1.0');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [batchId, setBatchId] = useState<string | null>(() => localStorage.getItem(STORE_BATCH));
  const [progress, setProgress] = useState<BatchProgress | null>(null);

  const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);

  const remember = (id: string | null) => {
    setBatchId(id);
    if (id) localStorage.setItem(STORE_BATCH, id);
    else localStorage.removeItem(STORE_BATCH);
  };

  const loadProgress = useCallback(async (id: string) => {
    try {
      const res = await fetch(`/api/tasks/batch/${id}`);
      if (!res.ok) return;
      setProgress((await res.json()) as BatchProgress);
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

  const submit = async () => {
    if (!lines.length) {
      setError('请至少输入一行文本');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await fetch('/api/tasks/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'tts',
          items: lines.map((t) => ({ text: t })),
          defaults: { speed: Number(speed) || 1.0 },
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        const d = data?.detail;
        throw new Error(typeof d === 'string' ? d : d?.detail?.message || d?.message || '批量提交失败');
      }
      remember(data.batch_id);
      setProgress(null);
      onDone?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const cancelBatch = async () => {
    if (!batchId) return;
    setBusy(true);
    try {
      const res = await fetch(`/api/tasks/batch/${batchId}/cancel`, { method: 'POST' });
      if (!res.ok) throw new Error('取消失败');
      await loadProgress(batchId);
      onDone?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const retryFailed = async () => {
    if (!batchId) return;
    setBusy(true);
    setError('');
    try {
      const res = await fetch(`/api/tasks/batch/${batchId}/retry-failed`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        const d = data?.detail;
        throw new Error(typeof d === 'string' ? d : d?.detail?.message || d?.message || '重试失败');
      }
      remember(data.batch_id);
      await loadProgress(data.batch_id);
      onDone?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const counts = progress?.counts || {};
  const finished = progress ? progress.progress >= 1 : false;

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
          </div>

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
