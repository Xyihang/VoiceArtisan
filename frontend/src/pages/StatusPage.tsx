import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  ClipboardCopy,
  Cpu,
  FolderOpen,
  Gauge,
  ListChecks,
  Radio,
  RefreshCw,
  ScrollText,
  ShieldCheck,
  Waypoints,
} from 'lucide-react';
import { api } from '../lib/api';
import { useApp } from '../state/AppContext';
import { formatMs, formatRelative, formatSeconds, formatTime } from '../lib/format';
import { EmptyState, InlineNote, KV, Meter, SectionCard, Skeleton, StatusDot } from '../components/ui';

type LogItem = { ts: number; level: string; logger: string; message: string };
type CheckItem = { name: string; status: string; message: string; fix: string };

const LEVELS = ['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR'] as const;
type Level = (typeof LEVELS)[number];

const LOG_FILE_HINT = 'data/logs/app.log';
const AUTO_REFRESH_MS = 5000;
const LOG_LIMIT = 200;
const COPY_LIMIT = 500;

/** 日志级别筛选写进 URL：`#/status?level=ERROR`（路由仍由 `#/status` 决定） */
function readLevelFromHash(): Level {
  const raw = window.location.hash.replace(/^#\/?/, '');
  const index = raw.indexOf('?');
  if (index < 0) return 'ALL';
  const level = (new URLSearchParams(raw.slice(index + 1)).get('level') || '').toUpperCase();
  return (LEVELS as readonly string[]).includes(level) ? (level as Level) : 'ALL';
}

function writeLevelToHash(level: Level): void {
  const next = `#/status${level === 'ALL' ? '' : `?level=${level}`}`;
  if (window.location.hash === next) return;
  window.history.replaceState(null, '', next);
}

function toneOf(status: string): 'ok' | 'warn' | 'danger' {
  if (status === 'ok') return 'ok';
  if (status === 'warn') return 'warn';
  return 'danger';
}

const MODE_LABEL: Record<string, string> = {
  local: '仅本地',
  cloud: '仅云端',
  hybrid: '混合',
  mock: '模拟引擎',
};

function goToSettings(section: string): void {
  window.location.hash = `#/settings?s=${section}`;
}

export function StatusPage() {
  const { health, describeError, pushToast } = useApp();
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [level, setLevel] = useState<Level>(readLevelFromHash);
  const [selfcheck, setSelfcheck] = useState<{ items: CheckItem[]; errors: number; warnings: number } | null>(null);
  const [status, setStatus] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<number | null>(null);
  const levelRef = useRef(level);
  levelRef.current = level;

  /** 拉日志：永远取「全部级别」，级别筛选与计数都在前端做（这样 chips 才能显示各档数量） */
  const loadLogs = useCallback(async () => {
    try {
      const data = await api.logs(LOG_LIMIT);
      setLogs(data.items);
      setLastRefresh(Date.now());
      return data.items;
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
      return null;
    }
  }, [describeError, pushToast]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [sc, st] = await Promise.all([api.selfcheck(), api.status()]);
      setSelfcheck(sc);
      setStatus(st);
      await loadLogs();
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    } finally {
      setLoading(false);
    }
  }, [describeError, loadLogs, pushToast]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // 自动刷新：默认开、5s 一次；关掉立即停止（effect 清理定时器）
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => {
      void loadLogs();
      void api
        .status()
        .then(setStatus)
        .catch(() => undefined);
    }, AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [autoRefresh, loadLogs]);

  // 前进/后退：以 URL 为准
  useEffect(() => {
    const onHash = () => {
      const next = readLevelFromHash();
      if (next !== levelRef.current) setLevel(next);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const changeLevel = (next: Level) => {
    levelRef.current = next;
    setLevel(next);
    writeLevelToHash(next);
  };

  const counts = useMemo(() => {
    const result: Record<string, number> = { ALL: logs.length, DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0 };
    logs.forEach((item) => {
      const key = (item.level || '').toUpperCase();
      if (key in result) result[key] += 1;
    });
    return result;
  }, [logs]);

  const shown = useMemo(() => {
    const list = level === 'ALL' ? logs : logs.filter((item) => (item.level || '').toUpperCase() === level);
    // 最新在最上
    return list.slice().reverse();
  }, [level, logs]);

  const copyLogs = async () => {
    if (!shown.length) return;
    const lines = shown
      .slice(0, COPY_LIMIT)
      .map((item) => `[${formatTime(item.ts)}] ${String(item.level).toUpperCase().padEnd(7)} ${item.logger} ${item.message}`);
    try {
      await navigator.clipboard.writeText(lines.join('\n'));
      pushToast({ level: 'success', title: `已复制 ${lines.length} 行日志`, description: level === 'ALL' ? '全部级别' : `级别 ${level}` });
    } catch {
      pushToast({ level: 'error', title: '复制失败', description: '浏览器拒绝了剪贴板写入，请手动选中日志复制。' });
    }
  };

  const copyLogPath = async () => {
    try {
      await navigator.clipboard.writeText(LOG_FILE_HINT);
      pushToast({ level: 'success', title: '日志路径已复制', description: LOG_FILE_HINT });
    } catch {
      pushToast({ level: 'error', title: '复制失败', description: '请手动复制路径。' });
    }
  };

  const vramTotal = health?.local?.vram_total_gb || 0;
  const vramUsed = health?.local?.vram_allocated_mb ? health.local.vram_allocated_mb / 1024 : 0;
  const vramRatio = vramTotal ? vramUsed / vramTotal : 0;
  const vramTone = vramRatio > 0.92 ? 'danger' : vramRatio > 0.8 ? 'warn' : 'ok';
  const loadedCount = Object.keys(health?.local?.loaded_models || {}).length;
  const queueActive = health?.queue?.active || null;
  const queuePending = health?.queue?.pending ?? 0;
  const engineReady = health?.local?.ready === true;
  const tunnelEnabled = health?.tunnel?.enabled === true;
  const tunnelRunning = health?.tunnel?.running === true;
  const cloudBad = health?.cloud?.reachable === false && health?.mode !== 'local';

  return (
    <div className="page">
      <div className="page__head">
        <div className="toolbar">
          <button
            type="button"
            className="btn btn--sm"
            onClick={() => void loadAll()}
            disabled={loading}
            title="立即刷新健康、自检与日志"
          >
            <RefreshCw size={14} strokeWidth={1.75} /> 刷新
          </button>
        </div>
      </div>

      <SectionCard
        title="后端健康"
        icon={<Activity size={15} strokeWidth={1.75} />}
        actions={
          <div className="toolbar" style={{ gap: 'var(--space-2)' }}>
            <label className="auto-refresh">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(event) => setAutoRefresh(event.target.checked)}
                aria-label="自动刷新"
              />
              自动刷新
              <span className="hint num">{AUTO_REFRESH_MS / 1000}s</span>
            </label>
            <span className="hint" aria-live="polite">
              上次刷新 <span className="num">{lastRefresh ? formatTime(lastRefresh / 1000) : '—'}</span>
            </span>
          </div>
        }
      >
        <div className="health-grid">
          <div className="health-card" data-tone={vramTone}>
            <div className="health-card__head">
              <Cpu size={13} strokeWidth={1.75} aria-hidden="true" />
              显存
              <StatusDot tone={vramTone} label={`显存 ${vramTone === 'ok' ? '正常' : vramTone === 'warn' ? '偏高' : '告急'}`} />
            </div>
            <div className="health-card__value">
              {vramUsed.toFixed(2)} <span className="unit">/</span> {vramTotal.toFixed(1)} <span className="unit">GB</span>
            </div>
            <div className="health-card__meta">
              空闲 {health?.local?.vram_free_gb?.toFixed(1) ?? '—'} GB · 已加载 {loadedCount} 个模型
            </div>
            <div className="health-card__foot">
              <Meter ratio={vramRatio} tone={vramRatio > 0.92 ? 'error' : vramRatio > 0.8 ? 'warn' : 'normal'} />
            </div>
            {vramTone !== 'ok' ? (
              <button type="button" className="link-btn" onClick={() => goToSettings('local')}>
                去设置调整显存策略
              </button>
            ) : null}
          </div>

          <div className="health-card" data-tone={queuePending ? 'warn' : 'ok'}>
            <div className="health-card__head">
              <ListChecks size={13} strokeWidth={1.75} aria-hidden="true" />
              队列
              <StatusDot
                tone={queuePending ? 'warn' : 'ok'}
                label={queuePending ? `排队 ${queuePending} 个` : '队列空闲'}
              />
            </div>
            <div className="health-card__value">
              {queuePending} <span className="unit">个排队</span>
            </div>
            <div className="health-card__meta">
              执行中：<span className="num">{queueActive || '空闲'}</span>
            </div>
            {queuePending ? (
              <button type="button" className="link-btn" onClick={() => (window.location.hash = '#/tasks?status=queued')}>
                去任务中心查看
              </button>
            ) : null}
          </div>

          <div className="health-card" data-tone={engineReady ? 'ok' : 'danger'}>
            <div className="health-card__head">
              <Radio size={13} strokeWidth={1.75} aria-hidden="true" />
              引擎
              <StatusDot tone={engineReady ? 'ok' : 'danger'} label={engineReady ? '引擎就绪' : '引擎不可用'} />
            </div>
            <div className="health-card__value health-card__value--text">
              {health?.active_backend === 'cloud' ? '云端' : health?.active_backend === 'local' ? '本地' : health?.active_backend === 'mock' ? '模拟' : health?.active_backend || '—'}
            </div>
            <div className="health-card__meta">
              设备 {health?.local?.device_label || health?.local?.device || '—'} · 模型 {health?.model || '—'}
            </div>
            {!engineReady ? (
              <button type="button" className="link-btn" onClick={() => goToSettings('engines')}>
                去设置安装引擎
              </button>
            ) : null}
            {health?.local?.last_error ? (
              <div className="health-card__error" title={health.local.last_error}>
                {health.local.last_error}
              </div>
            ) : null}
          </div>

          <div className="health-card" data-tone={tunnelEnabled ? (tunnelRunning ? 'ok' : 'danger') : 'idle'}>
            <div className="health-card__head">
              <Waypoints size={13} strokeWidth={1.75} aria-hidden="true" />
              隧道
              <StatusDot
                tone={tunnelEnabled ? (tunnelRunning ? 'ok' : 'danger') : 'idle'}
                label={tunnelEnabled ? (tunnelRunning ? '隧道运行中' : '隧道未运行') : '隧道未启用'}
              />
            </div>
            <div className="health-card__value health-card__value--text">
              {tunnelEnabled ? (tunnelRunning ? '运行中' : '未运行') : '未启用'}
            </div>
            <div className="health-card__meta">
              本地端口 <span className="num">{health?.tunnel?.local_port ?? '—'}</span>
              {health?.tunnel?.error ? ` · ${health.tunnel.error}` : ''}
            </div>
            {!tunnelRunning ? (
              <button type="button" className="link-btn" onClick={() => goToSettings('tunnel')}>
                去设置检查隧道
              </button>
            ) : null}
          </div>
        </div>

        <div className="kv">
          <KV
            label="活动后端"
            value={health?.active_backend === 'cloud' ? '云端' : health?.active_backend === 'local' ? '本地' : health?.active_backend === 'mock' ? '模拟' : health?.active_backend || '—'}
          />
          <KV label="运行模式" value={MODE_LABEL[health?.mode || ''] || health?.mode || '—'} />
          <KV label="云端延迟" value={health?.cloud?.latency_ms != null ? Math.round(health.cloud.latency_ms) : '—'} unit="ms" />
          <KV label="运行时长" value={health ? formatSeconds(health.uptime_s, 0) : '—'} />
          <KV label="累计任务" value={status?.stats?.total_tasks ?? '—'} unit="个" />
          <KV label="空闲停机" value={health?.idle?.enabled ? `${Math.round((health.idle.remaining_seconds ?? 0) / 60)} 分钟后` : '已禁用'} />
        </div>

        {cloudBad ? (
          <InlineNote
            kind="warning"
            action={
              <button type="button" className="btn btn--sm" onClick={() => goToSettings('cloud')}>
                去设置检查云端连接
              </button>
            }
          >
            云端不可达：{health?.cloud?.error || '请检查地址、密钥与隧道状态'}。可切换到仅本地模式继续使用。
          </InlineNote>
        ) : null}
      </SectionCard>

      <SectionCard title="运行环境自检" icon={<ShieldCheck size={15} strokeWidth={1.75} />}>
        {loading && !selfcheck ? (
          <div className="check-list" aria-hidden="true">
            {[0, 1, 2, 3].map((i) => (
              <div className="check-row" key={i}>
                <Skeleton height={14} width={14} radius={999} />
                <Skeleton height={12} width={96} />
                <Skeleton height={12} width="46%" />
              </div>
            ))}
          </div>
        ) : selfcheck ? (
          <div className="check-list">
            {selfcheck.items.map((item) => (
              <div key={item.name} className="check-row">
                <StatusDot tone={toneOf(item.status)} label={item.status === 'ok' ? '通过' : item.status === 'warn' ? '警告' : '异常'} />
                <strong className="check-row__name">{item.name}</strong>
                <span className="check-row__msg" title={item.message}>
                  {item.message}
                </span>
                {item.status !== 'ok' ? (
                  <button type="button" className="link-btn check-row__fix" onClick={() => goToSettings('engines')}>
                    去设置
                  </button>
                ) : null}
                {item.fix ? <span className="check-row__hint">修复建议：{item.fix}</span> : null}
              </div>
            ))}
            <p className="hint">
              共 <span className="num">{selfcheck.items.length}</span> 项检查，
              <span className="num">{selfcheck.errors}</span> 个错误、
              <span className="num">{selfcheck.warnings}</span> 个警告。
            </p>
          </div>
        ) : (
          <EmptyState
            title="自检结果不可用"
            description="后端未返回自检数据。"
            action={
              <button type="button" className="btn btn--sm" onClick={() => void loadAll()}>
                <RefreshCw size={13} strokeWidth={1.75} /> 重新自检
              </button>
            }
          />
        )}
      </SectionCard>

      <SectionCard
        title="最近日志"
        icon={<ScrollText size={15} strokeWidth={1.75} />}
        hint={`最近 ${LOG_LIMIT} 条 · 显示 ${shown.length} 条`}
        actions={
          <div className="toolbar" style={{ gap: 'var(--space-2)' }}>
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => void copyLogs()}
              disabled={!shown.length}
              title={shown.length ? `复制当前筛选的 ${Math.min(shown.length, COPY_LIMIT)} 行日志` : '当前筛选没有日志可复制'}
            >
              <ClipboardCopy size={13} strokeWidth={1.75} /> 复制（<span className="num">{Math.min(shown.length, COPY_LIMIT)}</span>）
            </button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => void copyLogPath()} title="复制日志文件路径">
              <FolderOpen size={13} strokeWidth={1.75} /> 日志目录
            </button>
          </div>
        }
      >
        <div className="toolbar">
          <div className="chip-row" style={{ marginTop: 0 }} role="group" aria-label="日志级别筛选">
            {LEVELS.map((item) => (
              <button
                key={item}
                type="button"
                className="filter-chip"
                aria-pressed={level === item}
                onClick={() => changeLevel(item)}
                title={`只看 ${item === 'ALL' ? '全部级别' : item}（${counts[item] ?? 0} 条）`}
              >
                {item === 'ALL' ? '全部' : item}
                <span className="filter-chip__count num">{counts[item] ?? 0}</span>
              </button>
            ))}
          </div>
          <div className="toolbar__spacer" />
          <span className="hint">
            日志文件 <code className="num">{LOG_FILE_HINT}</code>（按 8MB 轮转，保留 5 份）；筛选已写入地址栏
          </span>
        </div>

        {loading && !logs.length ? (
          <div className="log-list" aria-hidden="true">
            {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
              <div className="log-line" key={i}>
                <Skeleton height={11} width={52} />
                <Skeleton height={11} width={44} />
                <Skeleton height={11} width={`${46 + ((i * 7) % 40)}%`} />
              </div>
            ))}
          </div>
        ) : shown.length ? (
          <div className="log-list" style={{ maxHeight: 420 }}>
            {shown.map((log, index) => (
              <div className="log-line" key={`${log.ts}-${index}`}>
                <span className="num">{formatRelative(log.ts)}</span>
                <span className={`log-line__level log-line__level--${log.level.toLowerCase()}`}>{log.level}</span>
                <span>
                  <span style={{ color: 'var(--color-text-muted)' }}>{log.logger}</span> {log.message}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<ScrollText size={28} strokeWidth={1.5} />}
            title={level === 'ALL' ? '暂无日志' : `没有 ${level} 级别的日志`}
            description={level === 'ALL' ? '生成一次任务后即可看到实时日志。' : '切回「全部」可看到其它级别的记录。'}
            action={
              level === 'ALL' ? (
                <button type="button" className="btn btn--sm" onClick={() => void loadLogs()}>
                  <RefreshCw size={13} strokeWidth={1.75} /> 立即刷新
                </button>
              ) : (
                <button type="button" className="btn btn--sm" onClick={() => changeLevel('ALL')}>
                  查看全部级别
                </button>
              )
            }
          />
        )}
      </SectionCard>

      <SectionCard title="性能指标" hint="首包延迟与实时率说明" icon={<Gauge size={15} strokeWidth={1.75} />}>
        <ul className="hint metric-list">
          <li>
            <strong>首包延迟</strong>：从提交任务到第一段音频到达的时间；目标 ≤ 1.5 秒（云端含网络）。
          </li>
          <li>
            <strong>实时率 RTF</strong>：音频时长 / 处理耗时，&gt; 1 表示快于实时。
          </li>
          <li>
            当前平均首包：<span className="num">{formatMs(status?.stats?.avg_first_packet_ms)}</span>，平均 RTF：
            <span className="num">{status?.stats?.avg_rtf != null ? status.stats.avg_rtf.toFixed(2) : '—'}</span>
          </li>
        </ul>
        {status?.stats?.avg_first_packet_ms == null ? (
          <p className="hint">
            还没有可统计的任务。<button type="button" className="link-btn" onClick={() => (window.location.hash = '#/workbench')}>去工作台生成一次</button>即可看到指标。
          </p>
        ) : null}
      </SectionCard>
    </div>
  );
}

export default StatusPage;
