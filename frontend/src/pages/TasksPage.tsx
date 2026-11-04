import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  FileText,
  PackageOpen,
  RefreshCw,
  RotateCcw,
  Scissors,
  Trash2,
  Users,
  X,
} from 'lucide-react';
import { api } from '../lib/api';
import { downloadFile } from '../lib/download';
import { useApp } from '../state/AppContext';
import type { ExportTier, SpeakerSplitItem, Task, TaskSegment } from '../types';
import { ERROR_HINT, TYPE_LABEL, formatBytes, formatMs, formatSeconds, formatTime } from '../lib/format';
import { isEditableTarget } from '../lib/shortcuts';
import { BatchPanel } from '../components/BatchPanel';
import { SpeakerSegments } from '../components/SpeakerSegments';
import { SectionCard, Skeleton, Spinner, StatusBadge, EmptyState } from '../components/ui';

const EXPORT_TIERS: { id: ExportTier; label: string }[] = [
  { id: 'original', label: 'Original（模型原生 WAV）' },
  { id: 'standard', label: 'Standard（44.1k MP3 192k）' },
  { id: 'editing', label: 'For editing（48k 24bit WAV）' },
];

const PAGE_SIZE = 12;

type Filters = {
  status: string;
  type: string;
  q: string;
  order: 'desc' | 'asc';
  page: number;
};

const DEFAULT_FILTERS: Filters = { status: 'all', type: 'all', q: '', order: 'desc', page: 1 };

/** 从 `#/tasks?status=failed&type=tts&q=xxx&page=2` 还原筛选条件 */
function readFilters(): Filters {
  const raw = window.location.hash.replace(/^#\/?/, '');
  const index = raw.indexOf('?');
  const params = new URLSearchParams(index >= 0 ? raw.slice(index + 1) : '');
  const page = Number(params.get('page') ?? '1');
  return {
    status: params.get('status') || DEFAULT_FILTERS.status,
    type: params.get('type') || DEFAULT_FILTERS.type,
    q: params.get('q') || '',
    order: params.get('order') === 'asc' ? 'asc' : 'desc',
    page: Number.isFinite(page) && page >= 1 ? Math.floor(page) : 1,
  };
}

function filtersKey(filters: Filters): string {
  return `${filters.status}|${filters.type}|${filters.q}|${filters.order}|${filters.page}`;
}

function writeFilters(filters: Filters, replace = false): void {
  const params = new URLSearchParams();
  if (filters.status !== DEFAULT_FILTERS.status) params.set('status', filters.status);
  if (filters.type !== DEFAULT_FILTERS.type) params.set('type', filters.type);
  if (filters.q) params.set('q', filters.q);
  if (filters.order !== DEFAULT_FILTERS.order) params.set('order', filters.order);
  if (filters.page !== 1) params.set('page', String(filters.page));
  const query = params.toString();
  const next = `#/tasks${query ? `?${query}` : ''}`;
  if (window.location.hash === next) return;
  if (replace) {
    window.history.replaceState(null, '', next);
    return;
  }
  window.location.hash = next;
}

const STATUS_TABS: [string, string][] = [
  ['all', '全部'],
  ['queued', '排队中'],
  ['running', '运行中'],
  ['completed', '已完成'],
  ['failed', '失败'],
  ['canceled', '已取消'],
];

const STATUS_TEXT: Record<string, string> = {
  all: '全部状态',
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  canceled: '已取消',
};

export function TasksPage() {
  const { tasks: liveTasks, pushToast, describeError, removeTasks } = useApp();
  const [filters, setFilters] = useState<Filters>(readFilters);
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  const [items, setItems] = useState<Task[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState<Task | null>(null);
  const [media, setMedia] = useState<Record<string, {
    sample_rate: number | null;
    channels: number | null;
    bits: number | null;
    codec: string | null;
    bytes: number;
    tier: string | null;
    tier_label: string | null;
  }>>({});
  const [subtitleFormat, setSubtitleFormat] = useState<'srt' | 'vtt' | 'ass'>('srt');
  const [subtitleSpeakers, setSubtitleSpeakers] = useState(true);
  /** 重新导出：三档任选 */
  const [exportTier, setExportTier] = useState<ExportTier>('standard');
  const [exporting, setExporting] = useState<string | null>(null);
  /** 按说话人拆分：任务 id → 分轨清单 */
  const [splits, setSplits] = useState<Record<string, SpeakerSplitItem[]>>({});
  const [splitBusy, setSplitBusy] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const pendingIndex = useRef<number | null>(null);

  const { status, type, q, order, page } = filters;

  /** 筛选变更：先同步 URL（旧值 → 新值），再更新状态 */
  const update = useCallback((patch: Partial<Filters>, replace = false) => {
    const next = { ...filtersRef.current, ...patch };
    filtersRef.current = next;
    writeFilters(next, replace);
    setFilters(next);
  }, []);

  // 刷新 / 前进后退：以 URL 为准
  useEffect(() => {
    const onHash = () => {
      const next = readFilters();
      if (filtersKey(next) === filtersKey(filtersRef.current)) return;
      filtersRef.current = next;
      setFilters(next);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listTasks({ status, type, page, page_size: PAGE_SIZE, order, q });
      setItems(data.items);
      setTotal(data.total);
      setPages(data.pages);
      if (pendingIndex.current !== null) {
        setActive(pendingIndex.current === -1 ? Math.max(0, data.items.length - 1) : pendingIndex.current);
        pendingIndex.current = null;
      }
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    } finally {
      setLoading(false);
    }
  }, [describeError, order, page, pushToast, q, status, type]);

  useEffect(() => {
    void load();
  }, [load]);

  // 实时合并推送来的任务状态（不轮询）
  const merged = useMemo(
    () =>
      items.map((item) => {
        const live = liveTasks[item.id];
        return live ? { ...item, ...live } : item;
      }),
    [items, liveTasks],
  );

  useEffect(() => {
    if (Object.keys(liveTasks).length === 0) return;
    const known = new Set(items.map((i) => i.id));
    const unknown = Object.keys(liveTasks).filter((id) => !known.has(id));
    if (unknown.length && page === 1 && (status === 'all' || status === 'queued' || status === 'running')) {
      void load();
    }
  }, [liveTasks, items, page, status, load]);

  useEffect(() => {
    setSelected([]);
    setActive(0);
  }, [filtersKey(filters)]);

  const toggleExpand = useCallback(
    async (task: Task) => {
      if (expanded === task.id) {
        setExpanded(null);
        setDetail(null);
        return;
      }
      setExpanded(task.id);
      try {
        const full = await api.getTask(task.id);
        setDetail(full);
        // 成品真实规格（读文件头）：拿不到不报错，详情里少一行而已
        if (full.output_file) {
          void api
            .mediaInfo(task.id)
            .then((info) => setMedia(prev => ({ ...prev, [task.id]: info })))
            .catch(() => undefined);
        }
      } catch (error) {
        pushToast({ level: 'error', ...describeError(error) });
      }
    },
    [describeError, expanded, pushToast],
  );

  const retry = useCallback(
    async (task: Task, forceCloud = false) => {
      try {
        const created = await api.retryTask(task.id, forceCloud ? 'cloud' : undefined);
        pushToast({
          level: 'success',
          title: `已创建重试任务 #${created.id}`,
          description: forceCloud ? '本次将在云端执行' : '使用原路由策略',
        });
        await load();
      } catch (error) {
        pushToast({ level: 'error', ...describeError(error) });
      }
    },
    [describeError, load, pushToast],
  );

  const cancel = useCallback(
    async (task: Task) => {
      try {
        await api.cancelTask(task.id);
        pushToast({ level: 'info', title: `已请求取消 #${task.id}`, description: '已生成的片段会保留并标注「已截断」。' });
      } catch (error) {
        pushToast({ level: 'error', ...describeError(error) });
      }
    },
    [describeError, pushToast],
  );

  const remove = useCallback(
    async (ids: string[]) => {
      if (!ids.length) return;
      if (!window.confirm(`确定删除 ${ids.length} 个任务及其结果文件？删除后不可恢复。`)) return;
      try {
        if (ids.length === 1) await api.deleteTask(ids[0]);
        else await api.batchDelete(ids);
        removeTasks(ids);
        setSelected((prev) => prev.filter((id) => !ids.includes(id)));
        pushToast({ level: 'success', title: `已删除 ${ids.length} 个任务` });
        await load();
      } catch (error) {
        pushToast({ level: 'error', ...describeError(error) });
      }
    },
    [describeError, load, pushToast, removeTasks],
  );

  const retryMany = useCallback(
    async (ids: string[]) => {
      if (!ids.length) return;
      let ok = 0;
      for (const id of ids) {
        try {
          await api.retryTask(id);
          ok += 1;
        } catch {
          /* 单个失败不阻断其余任务 */
        }
      }
      pushToast({
        level: ok === ids.length ? 'success' : 'warning',
        title: `已重试 ${ok} / ${ids.length} 个失败任务`,
        description: ok === ids.length ? '重试任务已进入队列' : '部分任务无法重试，可在详情查看原因',
      });
      await load();
    },
    [load, pushToast],
  );

  const download = useCallback(
    (task: Task) => {
      if (!task.output_file) return;
      const isTxt = task.output_format === 'txt';
      const url = isTxt ? api.fileUrl(task.id, task.output_file) : api.downloadUrl(task.id);
      void downloadFile(url, task.output_file).catch(() => {
        /* 已兜底打开新窗口 */
      });
    },
    [],
  );

  const downloadSubtitle = useCallback(
    async (task: Task) => {
      try {
        await downloadFile(api.subtitleUrl(task.id, subtitleFormat, subtitleSpeakers), `${task.id}.${subtitleFormat}`);
        pushToast({ level: 'success', title: `已导出 ${subtitleFormat.toUpperCase()} 字幕` });
      } catch (error) {
        pushToast({
          level: 'error',
          title: '字幕导出失败',
          description: error instanceof Error ? error.message : '该任务可能没有时间轴（旧任务）',
        });
      }
    },
    [pushToast, subtitleFormat, subtitleSpeakers],
  );

  const copyError = useCallback(
    async (task: Task) => {
      const text = `${task.error_code || 'ERROR'}: ${task.error_message || '任务失败'}`;
      try {
        await navigator.clipboard.writeText(text);
        pushToast({ level: 'success', title: '已复制错误信息' });
      } catch {
        pushToast({ level: 'error', title: '复制失败', description: '浏览器拒绝了剪贴板访问' });
      }
    },
    [pushToast],
  );

  /** 详情重新拉一次（改名 / 合并 / 重新导出后分段与派生文本都会变） */
  const refreshDetail = useCallback(async (id: string) => {
    try {
      const full = await api.getTask(id);
      setDetail((prev) => (prev && prev.id === id ? full : prev));
      return full;
    } catch {
      return null;
    }
  }, []);

  const applySegments = useCallback(
    (taskId: string, segments: TaskSegment[]) => {
      setDetail((prev) => (prev && prev.id === taskId ? { ...prev, segments } : prev));
      void refreshDetail(taskId);
      void load();
    },
    [load, refreshDetail],
  );

  /** 重新导出：从无损原件按档位重编码，成功后刷新规格行 */
  const reexport = useCallback(
    async (task: Task, tier: ExportTier) => {
      setExporting(task.id);
      try {
        const res = await api.exportTask(task.id, tier);
        const info = res.export || res.media;
        const label = res.tier_label || info?.tier_label || tier;
        pushToast({
          level: 'success',
          title: `已重新导出 #${task.id}`,
          description: `档位：${label}${info?.bytes ? ` · ${formatBytes(info.bytes)}` : ''}`,
        });
        setMedia((prev) => {
          const next = { ...prev };
          delete next[task.id];
          return next;
        });
        void api
          .mediaInfo(task.id)
          .then((meta) => setMedia((prev) => ({ ...prev, [task.id]: meta })))
          .catch(() => undefined);
        await load();
        if (detail?.id === task.id) await refreshDetail(task.id);
      } catch (error) {
        pushToast({ level: 'error', ...describeError(error) });
      } finally {
        setExporting(null);
      }
    },
    [describeError, detail?.id, load, pushToast, refreshDetail],
  );

  /** 按说话人拆分（结果直接返回清单，文件落在任务目录 speakers/ 下） */
  const splitBySpeaker = useCallback(
    async (task: Task) => {
      setSplitBusy(task.id);
      try {
        const items = await api.splitBySpeaker(task.id);
        setSplits((prev) => ({ ...prev, [task.id]: items }));
        pushToast({
          level: 'success',
          title: `已拆出 ${items.length} 条分轨`,
          description: '可单独下载，或一次性打包成 zip。',
        });
      } catch (error) {
        pushToast({ level: 'error', ...describeError(error) });
      } finally {
        setSplitBusy(null);
      }
    },
    [describeError, pushToast],
  );

  const downloadSplitFile = useCallback(
    async (task: Task, item: SpeakerSplitItem) => {
      try {
        await downloadFile(api.speakerFileUrl(task.id, item.file), `${task.id}-${item.speaker}.wav`);
      } catch (error) {
        pushToast({
          level: 'error',
          title: '单轨下载失败',
          description: (error as Error)?.message || '请改用「打包下载 zip」',
        });
      }
    },
    [pushToast],
  );

  const downloadSpeakersZip = useCallback(
    async (task: Task) => {
      try {
        await downloadFile(api.speakersZipUrl(task.id), `speakers-${task.id}.zip`);
      } catch (error) {
        pushToast({
          level: 'error',
          title: '打包下载失败',
          description: (error as Error)?.message || '请先按说话人拆分',
        });
      }
    },
    [pushToast],
  );

  /* ------------------------------ 键盘导航 ------------------------------ */
  const goPage = useCallback(
    (nextPage: number, index: number) => {
      if (nextPage < 1 || nextPage > pages) return;
      pendingIndex.current = index;
      update({ page: nextPage });
    },
    [pages, update],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isEditableTarget(event.target)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const rows = merged;
      if (!rows.length) return;
      const current = rows[Math.min(active, rows.length - 1)];

      if (event.key === 'j' || event.key === 'ArrowDown') {
        event.preventDefault();
        if (active < rows.length - 1) setActive(active + 1);
        else goPage(page + 1, 0);
        return;
      }
      if (event.key === 'k' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (active > 0) setActive(active - 1);
        else goPage(page - 1, -1);
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        void toggleExpand(current);
        return;
      }
      if (event.key === 'r') {
        event.preventDefault();
        void retry(current, current.error_code === 'VRAM_INSUFFICIENT');
        return;
      }
      if (event.key === 'd') {
        event.preventDefault();
        download(current);
        return;
      }
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        void remove([current.id]);
        return;
      }
      if (event.key === 'Escape') {
        setExpanded(null);
        setDetail(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [active, download, goPage, merged, page, remove, retry, toggleExpand]);

  /* ------------------------------ 批量操作 ------------------------------ */
  const allChecked = merged.length > 0 && merged.every((task) => selected.includes(task.id));
  const failedSelected = selected.filter((id) => merged.find((task) => task.id === id)?.status === 'failed');

  const chips = useMemo(() => {
    const list: { key: keyof Filters; label: string }[] = [];
    if (status !== 'all') list.push({ key: 'status', label: `状态：${STATUS_TEXT[status] || status}` });
    if (type !== 'all') list.push({ key: 'type', label: `类型：${TYPE_LABEL[type] || type}` });
    if (q) list.push({ key: 'q', label: `搜索：${q}` });
    if (order !== 'desc') list.push({ key: 'order', label: '排序：最早优先' });
    return list;
  }, [order, q, status, type]);

  const activeTask = merged[Math.min(active, Math.max(0, merged.length - 1))];

  return (
    <div className="page page--fill">
      <div className="page__head">
        <div className="toolbar">
          <BatchPanel onDone={() => void load()} />
          <button type="button" className="btn btn--sm" onClick={() => void load()} title="重新拉取任务列表">
            <RefreshCw size={14} strokeWidth={1.75} /> 刷新
          </button>
        </div>
      </div>

      <SectionCard
        title="筛选"
        actions={
          <span className="card__hint">
            共 <span className="num">{total}</span> 个任务
          </span>
        }
      >
        <div className="toolbar">
          <div className="segmented" role="tablist" aria-label="状态筛选">
            {STATUS_TABS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={status === value}
                className="segmented__item"
                onClick={() => update({ status: value, page: 1 })}
              >
                {label}
              </button>
            ))}
          </div>
          <select
            className="select"
            style={{ width: 'auto' }}
            value={type}
            onChange={(event) => update({ type: event.target.value, page: 1 })}
            aria-label="按类型筛选"
          >
            <option value="all">全部类型</option>
            <option value="tts">语音生成</option>
            <option value="clone">声音克隆</option>
            <option value="design">音色设计</option>
            <option value="asr">音频转文字</option>
          </select>
          <select
            className="select"
            style={{ width: 'auto' }}
            value={order}
            onChange={(event) => update({ order: event.target.value as 'desc' | 'asc', page: 1 })}
            aria-label="排序"
          >
            <option value="desc">最新优先</option>
            <option value="asc">最早优先</option>
          </select>
          <input
            className="input"
            style={{ maxWidth: 240 }}
            placeholder="按文本内容搜索"
            value={q}
            onChange={(event) => update({ q: event.target.value, page: 1 }, true)}
            aria-label="搜索任务文本"
          />
        </div>
        {chips.length ? (
          <div className="chip-row" aria-label="当前筛选">
            {chips.map((chip) => (
              <button
                key={chip.key}
                type="button"
                className="filter-chip"
                onClick={() =>
                  update({
                    [chip.key]: chip.key === 'order' ? 'desc' : chip.key === 'status' || chip.key === 'type' ? 'all' : '',
                    page: 1,
                  } as Partial<Filters>)
                }
                title={`移除筛选：${chip.label}`}
                aria-label={`移除筛选 ${chip.label}`}
              >
                {chip.label}
                <X size={12} strokeWidth={2} aria-hidden="true" />
              </button>
            ))}
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => update({ ...DEFAULT_FILTERS })}>
              清除全部
            </button>
          </div>
        ) : null}
      </SectionCard>

      {selected.length ? (
        <div className="bulk-bar" role="region" aria-label="批量操作">
          <span>
            已选 <strong className="num">{selected.length}</strong> 个
          </span>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setSelected([])}>
            清空
          </button>
          <div className="toolbar__spacer" />
          <button
            type="button"
            className="btn btn--sm"
            disabled={!failedSelected.length}
            title={failedSelected.length ? `重试 ${failedSelected.length} 个失败任务` : '所选任务中没有失败任务'}
            onClick={() => void retryMany(failedSelected)}
          >
            <RotateCcw size={14} strokeWidth={1.75} /> 重试失败（<span className="num">{failedSelected.length}</span>）
          </button>
          <button
            type="button"
            className="btn btn--sm btn--danger"
            onClick={() => void remove(selected)}
            title={`删除 ${selected.length} 个任务`}
          >
            <Trash2 size={14} strokeWidth={1.75} /> 删除（<span className="num">{selected.length}</span>）
          </button>
        </div>
      ) : null}

      <SectionCard flush className="card--fill">
        <div className="table-wrap">
          <table className="table table--tasks">
            <thead>
              <tr>
                <th style={{ width: 40 }}>
                  <input
                    type="checkbox"
                    checked={allChecked}
                    aria-label="全选当前页"
                    title="全选当前页"
                    onChange={(event) => setSelected(event.target.checked ? merged.map((t) => t.id) : [])}
                  />
                </th>
                <th style={{ width: 100 }}>ID</th>
                <th style={{ width: 92 }}>类型</th>
                <th>文本摘要</th>
                <th style={{ width: 108 }}>音色</th>
                <th style={{ width: 168 }}>状态</th>
                <th style={{ width: 80 }}>首包</th>
                <th style={{ width: 80 }}>耗时</th>
                <th style={{ width: 116 }}>创建时间</th>
                <th className="table__actions" style={{ width: 156 }}>
                  操作
                </th>
              </tr>
            </thead>
            <tbody>
              {loading
                ? [0, 1, 2, 3, 4, 5].map((i) => (
                    <tr key={`sk-${i}`} className="table__skeleton" aria-hidden="true">
                      <td>
                        <Skeleton height={14} width={14} radius={4} />
                      </td>
                      <td>
                        <Skeleton height={12} width={56} />
                      </td>
                      <td>
                        <Skeleton height={12} width={64} />
                      </td>
                      <td>
                        <Skeleton height={12} width="82%" />
                      </td>
                      <td>
                        <Skeleton height={12} width={64} />
                      </td>
                      <td>
                        <Skeleton height={14} width={72} radius={999} />
                      </td>
                      <td>
                        <Skeleton height={12} width={46} />
                      </td>
                      <td>
                        <Skeleton height={12} width={46} />
                      </td>
                      <td>
                        <Skeleton height={12} width={72} />
                      </td>
                      <td>
                        <Skeleton height={12} width={96} />
                      </td>
                    </tr>
                  ))
                : merged.map((task, index) => {
                    const isActive = index === active;
                    const segs = (detail?.id === task.id ? detail.segments : task.segments) ?? [];
                    const bySpeakerText =
                      detail?.id === task.id ? detail.transcript_by_speaker : task.transcript_by_speaker;
                    const splitItems = splits[task.id] || [];
                    const diarizeBackend = task.params?.diarize_backend;
                    return (
                      <React.Fragment key={task.id}>
                        <tr
                          data-expanded={expanded === task.id}
                          data-status={task.status}
                          data-active={isActive}
                          onClick={() => setActive(index)}
                        >
                          <td>
                            <input
                              type="checkbox"
                              checked={selected.includes(task.id)}
                              aria-label={`选择任务 ${task.id}`}
                              onChange={(event) =>
                                setSelected((prev) =>
                                  event.target.checked ? [...prev, task.id] : prev.filter((id) => id !== task.id),
                                )
                              }
                            />
                          </td>
                          <td className="num">{task.id}</td>
                          <td>{TYPE_LABEL[task.type] || task.type}</td>
                          <td>
                            <div className="table__text" title={task.text || task.transcript || undefined}>
                              {task.text || task.transcript || '—'}
                            </div>
                          </td>
                          <td className="table__ellipsis" title={task.voice_name || undefined}>
                            {task.voice_name || '—'}
                          </td>
                          <td>
                            <div className="table__status">
                              <StatusBadge status={task.status} />
                              {task.status === 'failed' && task.error_code ? (
                                <span className="error-code" title={task.error_message || task.error_code}>
                                  {task.error_code}
                                </span>
                              ) : null}
                            </div>
                          </td>
                          <td className="num">
                            {task.first_packet_ms != null ? `${task.first_packet_ms}ms` : '—'}
                          </td>
                          <td className="num">
                            {task.elapsed_ms != null
                              ? formatMs(task.elapsed_ms)
                              : task.status === 'running' && task.started_at
                                ? `${Math.round(Date.now() / 1000 - task.started_at)}s…`
                                : '—'}
                          </td>
                          <td className="num">{formatTime(task.created_at)}</td>
                          <td className="table__actions">
                            <div className="row-actions">
                              <button
                                type="button"
                                className="table__row-btn"
                                onClick={() => void toggleExpand(task)}
                                aria-label={expanded === task.id ? '收起详情' : '展开详情'}
                                title={expanded === task.id ? '收起详情（Enter）' : '展开详情（Enter）'}
                                aria-expanded={expanded === task.id}
                              >
                                <ChevronDown
                                  size={14}
                                  strokeWidth={1.75}
                                  style={{ transform: expanded === task.id ? 'rotate(180deg)' : undefined }}
                                />
                              </button>
                              <button
                                type="button"
                                className="table__row-btn"
                                onClick={() => void retry(task, task.error_code === 'VRAM_INSUFFICIENT')}
                                aria-label={`重试任务 ${task.id}`}
                                title={task.error_code === 'VRAM_INSUFFICIENT' ? '一键切换到云端重试（R）' : '重试（R）'}
                              >
                                <RotateCcw size={14} strokeWidth={1.75} />
                              </button>
                              {task.status === 'running' || task.status === 'queued' ? (
                                <button
                                  type="button"
                                  className="table__row-btn"
                                  onClick={() => void cancel(task)}
                                  aria-label={`取消任务 ${task.id}`}
                                  title="取消任务"
                                >
                                  <X size={14} strokeWidth={2} />
                                </button>
                              ) : null}
                              {task.output_file ? (
                                <button
                                  type="button"
                                  className="table__row-btn"
                                  onClick={() => download(task)}
                                  aria-label={`下载任务 ${task.id} 的结果`}
                                  title="下载结果（D）"
                                >
                                  <Download size={14} strokeWidth={1.75} />
                                </button>
                              ) : null}
                              {task.status === 'completed' ? (
                                <button
                                  type="button"
                                  className="table__row-btn"
                                  onClick={() => void downloadSubtitle(task)}
                                  aria-label={`导出任务 ${task.id} 的字幕`}
                                  title={`导出 ${subtitleFormat.toUpperCase()} 字幕`}
                                >
                                  <FileText size={14} strokeWidth={1.75} />
                                </button>
                              ) : null}
                              <button
                                type="button"
                                className="table__row-btn table__row-btn--danger"
                                onClick={() => void remove([task.id])}
                                aria-label={`删除任务 ${task.id}`}
                                title="删除任务（Delete）"
                              >
                                <Trash2 size={14} strokeWidth={1.75} />
                              </button>
                            </div>
                          </td>
                        </tr>
                        {expanded === task.id ? (
                          <tr className="table__expand-row">
                            <td className="table__expand" colSpan={10}>
                              <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
                                <div className="kv">
                                  <div className="kv__item">
                                    <span className="kv__label">后端 / 模型</span>
                                    <span className="kv__value">
                                      {task.backend === 'cloud' ? '云端' : task.backend === 'mock' ? '模拟' : '本地'} ·{' '}
                                      {task.model || '—'}
                                    </span>
                                  </div>
                                  <div className="kv__item">
                                    <span className="kv__label">音频时长</span>
                                    <span className="kv__value num">
                                      {task.duration_sec != null ? formatSeconds(task.duration_sec, 2) : '—'}
                                    </span>
                                  </div>
                                  <div className="kv__item">
                                    <span className="kv__label">实时率 RTF</span>
                                    <span className="kv__value num">
                                      {task.rtf != null ? task.rtf.toFixed(2) : '—'}
                                    </span>
                                  </div>
                                  <div className="kv__item">
                                    <span className="kv__label">显存</span>
                                    <span className="kv__value num">
                                      {task.vram_mb != null ? `${task.vram_mb} MB` : '—'}
                                    </span>
                                  </div>
                                </div>
                                {diarizeBackend ? (
                                  <div className="toolbar">
                                    <span className="badge badge--info">
                                      <Users size={11} strokeWidth={2} aria-hidden="true" />
                                      说话人：{diarizeBackend === 'cloud' ? '云端识别' : '本地识别'}
                                    </span>
                                  </div>
                                ) : null}
                                {media[task.id] ? (
                                  <p className="hint" style={{ marginBottom: 'var(--space-2)' }}>
                                    实际规格：
                                    <span className="num">
                                      {media[task.id].sample_rate
                                        ? `${(media[task.id].sample_rate! / 1000).toFixed(1)} kHz`
                                        : '—'}
                                    </span>
                                    {' · '}
                                    {media[task.id].channels === 1 ? '单声道' : media[task.id].channels ? `${media[task.id].channels} 声道` : '—'}
                                    {' · '}
                                    {media[task.id].bits ? `${media[task.id].bits} bit` : '—'}
                                    {' · '}
                                    {(media[task.id].codec || '').toUpperCase() || '—'}
                                    {' · '}
                                    <span className="num">{formatBytes(media[task.id].bytes)}</span>
                                    {media[task.id].tier_label ? `　档位：${media[task.id].tier_label}` : ''}
                                  </p>
                                ) : null}
                                {task.output_file ? (
                                  <div className="toolbar" style={{ alignItems: 'center', gap: 'var(--space-2)' }}>
                                    <span className="kv__label">重新导出</span>
                                    <select
                                      className="select"
                                      style={{ width: 'auto' }}
                                      value={exportTier}
                                      onChange={(event) => setExportTier(event.target.value as ExportTier)}
                                      aria-label="重新导出的档位"
                                      title="从无损原件按档位重编码，原件不会被覆盖"
                                    >
                                      {EXPORT_TIERS.map((item) => (
                                        <option key={item.id} value={item.id}>
                                          {item.label}
                                        </option>
                                      ))}
                                    </select>
                                    <button
                                      type="button"
                                      className="btn btn--sm"
                                      disabled={exporting === task.id}
                                      onClick={() => void reexport(task, exportTier)}
                                      aria-label={`按 ${exportTier} 档位重新导出任务 ${task.id}`}
                                      title="按所选档位重新编码成品"
                                    >
                                      {exporting === task.id ? (
                                        <Spinner size={13} />
                                      ) : (
                                        <RotateCcw size={14} strokeWidth={1.75} />
                                      )}
                                      重新导出
                                    </button>
                                  </div>
                                ) : null}
                                <div className="toolbar" style={{ alignItems: 'center', gap: 'var(--space-2)' }}>
                                  <span className="kv__label">字幕 / 时间戳</span>                                  <select
                                    className="select"
                                    style={{ width: 'auto' }}
                                    value={subtitleFormat}
                                    onChange={(event) =>
                                      setSubtitleFormat(event.target.value as 'srt' | 'vtt' | 'ass')
                                    }
                                    aria-label="字幕格式"
                                  >
                                    <option value="srt">SRT</option>
                                    <option value="vtt">VTT</option>
                                    <option value="ass">ASS</option>
                                  </select>
                                  <label className="checkbox" title="每句前是否加 [SPKn] 说话人前缀">
                                    <input
                                      type="checkbox"
                                      checked={subtitleSpeakers}
                                      onChange={(event) => setSubtitleSpeakers(event.target.checked)}
                                    />
                                    带说话人
                                  </label>
                                  <button
                                    type="button"
                                    className="btn btn--sm"
                                    onClick={() => void downloadSubtitle(task)}
                                    title="按分块时间轴导出字幕"
                                  >
                                    <FileText size={14} strokeWidth={1.75} /> 下载字幕
                                  </button>
                                </div>
                                {task.transcript ? (
                                  <div>
                                    <h3 style={{ marginBottom: 'var(--space-2)' }}>转写文本</h3>
                                    <p style={{ whiteSpace: 'pre-wrap', fontSize: 'var(--fs-13)' }}>
                                      {task.transcript}
                                    </p>
                                  </div>
                                ) : null}
                                {bySpeakerText ? (
                                  <details className="nested">
                                    <summary>按说话人渲染的文本（只读）</summary>
                                    <p style={{ whiteSpace: 'pre-wrap', fontSize: 'var(--fs-13)', marginTop: 'var(--space-2)' }}>
                                      {bySpeakerText}
                                    </p>
                                  </details>
                                ) : null}
                                <SpeakerSegments
                                  taskId={task.id}
                                  segments={segs}
                                  onRenamed={(updated) => applySegments(task.id, updated)}
                                  onMerged={(updated) => applySegments(task.id, updated)}
                                />
                                {segs.some((seg) => seg.speaker) ? (
                                  <div>
                                    <div className="toolbar" style={{ marginBottom: 'var(--space-2)' }}>
                                      <h3 style={{ margin: 0 }}>按说话人拆分</h3>
                                      <div className="toolbar__spacer" />
                                      <button
                                        type="button"
                                        className="btn btn--sm"
                                        disabled={splitBusy === task.id}
                                        onClick={() => void splitBySpeaker(task)}
                                        aria-label={`按说话人拆分任务 ${task.id} 的音频`}
                                        title="按分段自身时间轴切原音频，每个说话人一条 wav"
                                      >
                                        {splitBusy === task.id ? (
                                          <Spinner size={13} />
                                        ) : (
                                          <Scissors size={14} strokeWidth={1.75} />
                                        )}
                                        拆分
                                      </button>
                                      <button
                                        type="button"
                                        className="btn btn--sm"
                                        disabled={!splitItems.length}
                                        onClick={() => void downloadSpeakersZip(task)}
                                        aria-label={`打包下载任务 ${task.id} 的分轨音频`}
                                        title={splitItems.length ? '下载分轨 zip' : '先拆分再打包下载'}
                                      >
                                        <PackageOpen size={14} strokeWidth={1.75} /> 打包下载 zip
                                      </button>
                                    </div>
                                    {splitItems.length ? (
                                      <div style={{ display: 'grid', gap: 'var(--space-1)' }}>
                                        {splitItems.map((item) => (
                                          <div key={item.file} className="toolbar" style={{ alignItems: 'baseline' }}>
                                            <span className="chip" style={{ flex: '0 0 auto' }}>
                                              {item.speaker}
                                            </span>
                                            <span className="hint num">{formatSeconds(item.duration_s, 1)}</span>
                                            <span className="hint num">{formatBytes(item.bytes)}</span>
                                            <span className="hint">{item.segments} 段</span>
                                            <div className="toolbar__spacer" />
                                            <button
                                              type="button"
                                              className="btn btn--ghost btn--sm"
                                              onClick={() => void downloadSplitFile(task, item)}
                                              aria-label={`下载 ${item.speaker} 的分轨文件`}
                                              title={`下载 ${item.file}`}
                                            >
                                              <Download size={13} strokeWidth={1.75} /> 下载
                                            </button>
                                          </div>
                                        ))}
                                      </div>
                                    ) : (
                                      <p className="hint" style={{ margin: 0 }}>
                                        拆分后每个说话人一条 wav，可单独下载或打包；新任务才有原件，旧任务会提示缺少原件。
                                      </p>
                                    )}
                                  </div>
                                ) : null}
                                {task.error_message ? (
                                  <div className="inline-note inline-note--error">
                                    <div>
                                      <span className="error-code">{task.error_code}</span> {task.error_message}
                                      {task.error_code && ERROR_HINT[task.error_code] ? (
                                        <p className="hint" style={{ margin: 'var(--space-1) 0 0' }}>
                                          {ERROR_HINT[task.error_code]}
                                        </p>
                                      ) : null}
                                    </div>
                                    <div style={{ marginTop: 'var(--space-2)' }}>
                                      <button
                                        type="button"
                                        className="btn btn--sm"
                                        onClick={() => void copyError(task)}
                                        title="复制错误码与错误消息"
                                      >
                                        <Copy size={13} strokeWidth={1.75} /> 复制错误
                                      </button>
                                    </div>
                                  </div>
                                ) : null}
                                <div>
                                  <h3 style={{ marginBottom: 'var(--space-2)' }}>任务日志</h3>
                                  <div className="log-list">
                                    {(detail?.id === task.id ? detail.logs : task.logs)?.length ? (
                                      (detail?.id === task.id ? detail.logs : task.logs)!.map((line, i) => (
                                        <div className="log-line" key={i}>
                                          <span>{formatTime(line.ts)}</span>
                                          <span className="log-line__level log-line__level--info">
                                            {task.status === 'failed' && i === 0 ? 'error' : 'info'}
                                          </span>
                                          <span>{line.message}</span>
                                        </div>
                                      ))
                                    ) : (
                                      <span>暂无日志</span>
                                    )}
                                  </div>
                                </div>
                              </div>
                            </td>
                          </tr>
                        ) : null}
                      </React.Fragment>
                    );
                  })}
            </tbody>
          </table>
        </div>
        {!loading && !merged.length ? (
          <EmptyState
            title="没有符合条件的任务"
            description={
              chips.length
                ? '当前筛选没有命中任务，点上方 chips 的 × 可移除筛选。'
                : '在工作台按 Ctrl + Enter 创建一次生成，任务会出现在这里。'
            }
          />
        ) : null}
        <div className="pagination">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => goPage(page - 1, -1)}
            disabled={page <= 1}
            title="上一页（K 到首行再按一次）"
          >
            <ChevronLeft size={14} strokeWidth={1.75} /> 上一页
          </button>
          <span className="pagination__text">
            Page <span className="num">{page}</span> of <span className="num">{Math.max(1, pages)}</span>
          </span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => goPage(page + 1, 0)}
            disabled={page >= pages}
            title="下一页（J 到末行再按一次）"
          >
            下一页 <ChevronRight size={14} strokeWidth={1.75} />
          </button>
          {activeTask && !loading ? (
            <span className="pagination__hint">
              当前行 <span className="num">{activeTask.id}</span>
            </span>
          ) : null}
        </div>
      </SectionCard>
    </div>
  );
}

export default TasksPage;
