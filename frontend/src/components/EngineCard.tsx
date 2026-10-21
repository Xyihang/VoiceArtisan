import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Boxes, Check, Copy, Download, ListChecks, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { api, ApiError } from '../lib/api';
import { WsClient } from '../lib/ws';
import { useApp } from '../state/AppContext';
import type {
  EngineInstallPhase,
  EngineInstallStatus,
  EngineItem,
  EngineList,
  EnginePackageStatus,
  EnginePlan,
  EngineStatus,
} from '../types';
import { EmptyState, ProgressBar, SectionCard, Skeleton, Spinner } from './ui';

/* =========================== 常量 =========================== */

const STATUS_META: Record<EngineStatus, { label: string; cls: string }> = {
  ready: { label: '已就绪', cls: 'badge--success' },
  not_installed: { label: '未安装', cls: 'badge--muted' },
  package_missing: { label: '缺少依赖', cls: 'badge--warning' },
  error: { label: '异常', cls: 'badge--error' },
  experimental: { label: '实验中', cls: 'badge--info' },
};

const FALLBACK_STATUS: { label: string; cls: string } = { label: '未知', cls: 'badge--muted' };

const KIND_LABEL: Record<string, string> = {
  tts: '语音生成',
  clone: '声音克隆',
  design: '音色设计',
  instruct: '指令控制',
};

const PHASE_LABEL: Record<EngineInstallPhase, string> = {
  download: '下载中',
  pip: '安装依赖',
  verify: '校验',
  done: '已完成',
  error: '安装失败',
};

/** 体积来源：head = 后端 HEAD 实测；manifest = 清单声明值 */
const SIZE_SOURCE_LABEL: Record<string, string> = { head: '实测', manifest: '清单' };

export type InstallState = {
  phase: EngineInstallPhase;
  progress: number;
  message: string;
  done: boolean;
};

export type EngineAction = 'install' | 'remove' | 'select' | 'health';

/* =========================== 工具 =========================== */

function formatSampleRate(sampleRate: number): string {
  if (!sampleRate || sampleRate <= 0) return '采样率未知';
  const khz = sampleRate / 1000;
  return `${Number.isInteger(khz) ? khz : khz.toFixed(1)} kHz`;
}

function licenseBadge(item: EngineItem) {
  if (!item.license && item.commercial_use === undefined) return null;
  const restricted = item.commercial_use === 'requires_agreement';
  const text = restricted ? '商用需授权' : item.commercial_use === false ? '非商用' : item.license || '';
  if (!text) return null;
  return (
    <span
      title={
        restricted
          ? `许可证：${item.license || '未知'}；商用需与权利方另行签约`
          : `许可证：${item.license || '见模型卡'}${item.commercial_use === false ? '（仅限非商业用途）' : ''}`
      }
      style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 'var(--fs-11)',
        letterSpacing: '0.02em',
        padding: '0 6px',
        borderRadius: 'var(--radius-full)',
        border: '1px solid var(--color-border)',
        color: restricted ? 'var(--color-warning, #b45309)' : 'var(--color-text-muted)',
        whiteSpace: 'nowrap',
      }}
    >
      {text}
    </span>
  );
}
function formatSize(mb?: number): string {
  const v = typeof mb === 'number' && mb > 0 ? mb : 0;
  if (!v) return '未知';
  return v >= 1024 ? `${(v / 1024).toFixed(2)} GB` : `${Math.round(v)} MB`;
}
const LANG_SHORT: Record<string, string> = {
  chinese: '中',
  english: '英',
  japanese: '日',
  korean: '韩',
  french: '法',
  german: '德',
  spanish: '西',
  russian: '俄',
  portuguese: '葡',
  italian: '意',
  beijing_dialect: '京',
  sichuan_dialect: '川',
};
function formatVram(vramGb: number): string {
  if (!vramGb || vramGb <= 0) return 'CPU 可用';
  return `${vramGb} GB 显存`;
}

function formatLanguages(languages: string[]): string {
  if (!languages || languages.length === 0) return '—';
  const short = languages.slice(0, 4).map((l) => LANG_SHORT[l] || l.slice(0, 2));
  const suffix = languages.length > 4 ? ` 等 ${languages.length} 种` : '';
  return short.join(' · ') + suffix;
}

/**
 * 复制给用户的安装命令：**必须**指向引擎自己的 venv，并且**必须是精确版本**（锁文件）。
 * 裸 `pip install <模型包>` 会把主环境 torch 换成 CPU 版（真实事故），所以：
 * 优先用后端生成的 install_command（= `-r locks/<id>.txt` 那条）；旧后端兜底也手工拼
 * venv python + 约束文件 + 锁文件，绝不拼接裸包名。
 */
function packageCommand(item: EngineItem): string {
  if (item.install_command) return item.install_command;
  const python = item.venv_python || 'python';
  const constraints = item.venv_constraints || '';
  const parts = [`"${python}"`, '-m', 'pip', 'install', '--no-cache-dir'];
  if (constraints) parts.push('-c', `"${constraints}"`);
  if (item.lock_file) {
    parts.push('-r', `"${item.lock_file}"`);
    return parts.join(' ');
  }
  return parts.join(' ');
}

/** 隔离信息：venv 引擎显示「依赖隔离」，默认引擎显示「主环境」 */
function isolationOf(item: EngineItem): { label: string; title: string; isolated: boolean } {
  const isolation = item.isolation || (item.id === 'qwen3-tts-1.7b' ? 'inprocess' : 'venv');
  if (isolation === 'inprocess') {
    return { label: '主环境', title: '依赖与模型都在主环境就地推理（默认引擎）', isolated: false };
  }
  const exists = Boolean(item.venv_exists);
  return {
    label: exists ? '依赖隔离 · venv' : '依赖隔离 · venv 未创建',
    title: `依赖装在引擎专属 venv：${item.venv_path || '(未知)'}\n合成时用 ${item.venv_python || '<venv python>'} runner.py 子进程推理，主环境 torch 不受影响。`,
    isolated: true,
  };
}

/** 依赖探测结果：新后端给 packages_status，旧后端只有 packages/package_installed */
function packageStatusOf(item: EngineItem): EnginePackageStatus[] {
  if (item.packages_status?.length) return item.packages_status;
  return item.packages.map((name) => ({
    name,
    installed: Boolean(item.package_installed),
    version: null,
    import_name: name,
  }));
}

function missingOf(item: EngineItem, status: EnginePackageStatus[]): string[] {
  if (item.packages_missing) return item.packages_missing;
  return status.filter((entry) => !entry.installed).map((entry) => entry.name);
}

function packagesOkOf(item: EngineItem, status: EnginePackageStatus[]): boolean {
  if (typeof item.packages_ok === 'boolean') return item.packages_ok;
  return Boolean(item.package_installed) && !status.some((entry) => !entry.installed);
}

/** 缺失项排前面，方便一眼看出还差什么 */
function sortStatus(status: EnginePackageStatus[]): EnginePackageStatus[] {
  return [...status].sort((a, b) => Number(a.installed) - Number(b.installed));
}

/** 安装失败且与 torch 有关（后端 TORCH_CHANGED）→ 需要显示恢复命令 */
function torchBroken(install: InstallState | null): boolean {
  return Boolean(install && install.done && install.phase === 'error' && /torch/i.test(install.message || ''));
}

/** 后端没给 torch_repair_command 时的兜底：同样是「显式解释器 + 显式 wheel 源」的安全形式，
 *  绝不用裸 pip 安装 torch（那会装成 CPU 版）。 */
const FALLBACK_REPAIR_COMMAND =
  'python -m pip install --force-reinstall --no-cache-dir torch==2.4.1+cu121 --index-url https://download.pytorch.org/whl/cu121';

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    /* 非安全上下文（如局域网 http）下回退到 execCommand */
    try {
      const area = document.createElement('textarea');
      area.value = text;
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(area);
      return ok;
    } catch {
      return false;
    }
  }
}

/* =========================== 单张引擎卡片 =========================== */

export function EngineCard({
  item,
  active,
  install,
  busy,
  detail,
  planOpen,
  onTogglePlan,
  onInstall,
  onRemove,
  onSelect,
  onHealth,
  onCopy,
}: {
  item: EngineItem;
  active: boolean;
  install: InstallState | null;
  busy: EngineAction | null;
  detail?: string;
  /** 是否展开安装预览（由父级保证同一时间只有一张卡展开） */
  planOpen: boolean;
  onTogglePlan: () => void;
  onInstall: () => void;
  onRemove: () => void;
  onSelect: () => void;
  onHealth: () => void;
  onCopy: (command?: string) => void;
}) {
  const status = STATUS_META[item.status] ?? FALLBACK_STATUS;
  const installing = Boolean(install && !install.done);
  const failed = Boolean(install && install.done && install.phase === 'error');
  const canInstall = !item.installed || item.status === 'package_missing';
  const pkgStatus = useMemo(() => sortStatus(packageStatusOf(item)), [item]);
  const missing = useMemo(() => missingOf(item, pkgStatus), [item, pkgStatus]);
  const packagesOk = packagesOkOf(item, pkgStatus);
  const hasPackages = pkgStatus.length > 0;
  const needPackages = hasPackages && !packagesOk;
  const isolation = isolationOf(item);
  const torchError = torchBroken(install);
  const repairCommand = item.torch_repair_command || FALLBACK_REPAIR_COMMAND;
  /** 锁文件版本校验结果：mismatch 非空 = 实际版本被改过；versions_ok = 与锁文件完全一致 */
  const versionMismatch = item.versions_mismatch ?? [];
  const versionsOk = Boolean(item.versions_ok);
  /** 修复命令就是后端生成的「-r locks/<id>.txt」那条，复制即可用 */
  const fixCommand = item.install_command || packageCommand(item);
  const [depsOpen, setDepsOpen] = useState(false);
  const detailText = detail || item.detail || (item.installed ? '已安装，可点击「检测」确认运行状态' : '尚未安装到本机');
  const disabled = busy !== null || installing;

  /* ---- 安装预览：只读探测结果，独立于 install 状态机，不进 busy ---- */
  const [plan, setPlan] = useState<EnginePlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState('');
  const [planAttempt, setPlanAttempt] = useState(0);

  useEffect(() => {
    if (!planOpen || plan) return;
    let cancelled = false;
    setPlanLoading(true);
    setPlanError('');
    api
      .enginePlan(item.id)
      .then((res) => {
        if (!cancelled) setPlan(res);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const status = error instanceof ApiError ? error.status : 0;
        // 404/405：后端还没这个接口（未升级）；其余照实显示，不 toast
        setPlanError(
          status === 404 || status === 405 || status === 501
            ? '后端未提供安装预览接口（请升级后端）'
            : error instanceof ApiError
              ? error.message || '预览失败'
              : '预览失败',
        );
      })
      .finally(() => {
        if (!cancelled) setPlanLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [planOpen, plan, item.id, planAttempt]);

  const planDownloads = plan?.downloads ?? [];
  const planTotalGb = ((plan?.total_mb ?? 0) / 1024).toFixed(2);
  const planEnvKeys = plan?.requires_env ? Object.keys(plan.requires_env) : [];

  return (
    <article
      aria-pressed={active}
      data-engine-id={item.id}
      style={{
        display: 'grid',
        gap: 'var(--space-2)',
        alignContent: 'start',
        minWidth: 0,
        padding: 'var(--space-3)',
        background: active ? 'var(--color-primary-soft)' : 'var(--color-surface)',
        border: `var(--border-width) solid ${active ? 'var(--color-primary)' : 'var(--color-border)'}`,
        borderRadius: 'var(--radius-md)',
        boxShadow: active ? 'inset 2px 0 0 var(--color-primary)' : 'none',
        transition: 'border-color var(--duration-fast) var(--ease-out)',
      }}
    >
      <header style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-2)', minWidth: 0 }}>
        <div style={{ minWidth: 0, display: 'grid', gap: 2 }}>
          <strong className="engine-card__name" title={item.name}>
            {item.name}
          </strong>
          <span className="hint" style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
            {item.params ? <span className="num">{item.params}</span> : null}
            <span>{item.vendor}</span>
          </span>
        </div>
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 'var(--space-1)', flexShrink: 0 }}>
          {licenseBadge(item)}
          {active ? <span className="badge badge--info">使用中</span> : null}
          <span className={`badge ${status.cls}`}>{status.label}</span>
        </span>
      </header>

      <div className="voice-card__tags">
        {item.kind_tags.length ? (
          item.kind_tags.map((tag) => (
            <span key={tag} className="chip" style={{ height: 22, fontSize: 'var(--fs-11)' }}>
              {KIND_LABEL[tag] || tag}
            </span>
          ))
        ) : (
          <span className="hint">未标注能力</span>
        )}
        <span
          className="chip"
          title={isolation.title}
          style={{ height: 22, fontSize: 'var(--fs-11)', borderStyle: isolation.isolated ? 'dashed' : 'solid' }}
        >
          {isolation.label}
        </span>
      </div>

      <dl className="engine-specs">
        <div>
          <dt>显存</dt>
          <dd className="num">{item.vram_gb > 0 ? `${item.vram_gb} GB` : 'CPU'}</dd>
        </div>
        <div>
          <dt>采样率</dt>
          <dd className="num">{item.sample_rate ? `${(item.sample_rate / 1000).toFixed(1)} kHz` : '—'}</dd>
        </div>
        <div>
          <dt>{item.installed ? '已占用' : '需下载'}</dt>
          <dd className="num">{formatSize(item.installed ? item.size_mb : item.total_mb ?? item.size_mb)}</dd>
        </div>
        <div>
          <dt>语言</dt>
          <dd>{formatLanguages(item.languages)}</dd>
        </div>
      </dl>

      <p className="engine-detail" title={detailText}>
        {detailText}
      </p>

      {torchError ? (
        <div className="inline-note inline-note--error" style={{ display: 'grid', gap: 'var(--space-1)' }}>
          <span style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'flex-start' }}>
            <span className="inline-note__icon">
              <AlertTriangle size={14} strokeWidth={1.75} />
            </span>
            <span style={{ color: 'var(--color-error)', fontWeight: 'var(--fw-medium)' }}>
              安装已中止：torch 被替换，CUDA 可能已不可用
            </span>
          </span>
          <span
            className="engine-detail"
            title={install?.message || ''}
            style={{ margin: 0, WebkitLineClamp: 2, color: 'var(--color-text-muted)' }}
          >
            {install?.message || 'pip 的依赖解析改动了 torch 版本'}
          </span>
          <code
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 'var(--fs-11)',
              color: 'var(--color-text)',
              display: '-webkit-box',
              WebkitBoxOrient: 'vertical',
              WebkitLineClamp: 2,
              overflow: 'hidden',
              wordBreak: 'break-all',
            }}
            title={repairCommand}
          >
            {repairCommand}
          </code>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => onCopy(repairCommand)}
            title={repairCommand}
            style={{ justifySelf: 'start' }}
          >
            <Copy size={12} strokeWidth={1.75} /> 复制恢复命令
          </button>
        </div>
      ) : null}

      {!torchError && versionMismatch.length > 0 ? (
        <div
          className="inline-note inline-note--error"
          style={{ display: 'grid', gap: 'var(--space-1)' }}
          title={`锁文件：${item.lock_file || '(未知)'}`}
        >
          <span style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'flex-start' }}>
            <span className="inline-note__icon">
              <AlertTriangle size={14} strokeWidth={1.75} />
            </span>
            <span style={{ color: 'var(--color-error)', fontWeight: 'var(--fw-medium)' }}>
              版本不一致：
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-11)' }}>
                {versionMismatch.slice(0, 3).join('、')}
                {versionMismatch.length > 3 ? ` 等 ${versionMismatch.length} 个` : ''}
              </span>
            </span>
          </span>
          <code
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 'var(--fs-11)',
              color: 'var(--color-text)',
              display: '-webkit-box',
              WebkitBoxOrient: 'vertical',
              WebkitLineClamp: 2,
              overflow: 'hidden',
              wordBreak: 'break-all',
            }}
            title={fixCommand}
          >
            {fixCommand}
          </code>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => onCopy(fixCommand)}
            title={fixCommand}
            style={{ justifySelf: 'start' }}
          >
            <Copy size={12} strokeWidth={1.75} /> 复制修复命令
          </button>
        </div>
      ) : null}

      {!torchError && needPackages ? (
        <div className="inline-note">
          <span className="inline-note__icon">
            <AlertTriangle size={14} strokeWidth={1.75} />
          </span>
          <div style={{ display: 'grid', gap: 'var(--space-1)', minWidth: 0 }}>
            <span>
              需要安装依赖：
              <span className="num" style={{ color: 'var(--color-text)' }}>
                {missing.slice(0, 3).join('、')}
                {missing.length > 3 ? ` 等 ${missing.length} 个` : ''}
              </span>
            </span>
            {isolation.isolated ? (
              <span
                className="hint"
                title={isolation.title}
                style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-11)', wordBreak: 'break-all' }}
              >
                装进该引擎的 venv：{item.venv_path || '（尚未创建）'}
              </span>
            ) : null}
            <span style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => onCopy()}
                title={packageCommand(item)}
              >
                <Copy size={12} strokeWidth={1.75} /> 复制安装命令
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setDepsOpen((open) => !open)}
                aria-expanded={depsOpen}
                title="查看每个依赖的检测结果"
              >
                {depsOpen ? '收起' : `查看 ${pkgStatus.length} 个依赖`}
              </button>
            </span>
            {depsOpen ? (
              <ul
                style={{
                  display: 'grid',
                  gap: 2,
                  margin: 0,
                  padding: 'var(--space-1) 0 0',
                  listStyle: 'none',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 'var(--fs-11)',
                  maxHeight: 132,
                  overflowY: 'auto',
                }}
              >
                {pkgStatus.map((entry) => (
                  <li
                    key={entry.name}
                    title={`${entry.import_name}${entry.version ? ` · ${entry.version}` : ''}`}
                    style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'baseline', minWidth: 0 }}
                  >
                    <span
                      style={{
                        flex: '1 1 auto',
                        minWidth: 0,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        color: entry.installed ? 'var(--color-text-muted)' : 'var(--color-warning)',
                      }}
                    >
                      {entry.name}
                    </span>
                    <span style={{ flex: '0 0 auto', color: 'var(--color-text-muted)' }}>{entry.version ?? '—'}</span>
                    <span
                      style={{
                        flex: '0 0 auto',
                        width: 10,
                        textAlign: 'center',
                        color: entry.installed ? 'var(--color-success)' : 'var(--color-error)',
                      }}
                    >
                      {entry.installed ? '✓' : '✗'}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </div>
      ) : null}

      {!torchError && hasPackages && packagesOk ? (
        <span
          className="hint"
          style={{ color: 'var(--color-success)', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-11)' }}
          title={pkgStatus
            .map((entry) => `${entry.name} ${entry.version ?? '—'} ${entry.installed ? '✓' : '✗'}`)
            .join('\n')}
        >
          依赖已就绪 · {pkgStatus.length} 个
          {versionsOk ? ` · 版本已锁定（${item.lock_packages?.length ?? 0} 个包 ==）` : ''}
        </span>
      ) : null}

      {installing ? (
        <div style={{ display: 'grid', gap: 'var(--space-1)' }}>
          <ProgressBar value={install ? install.progress : 0} indeterminate={!install || install.progress <= 0} />
          <span className="hint">
            {PHASE_LABEL[install!.phase] || install!.phase}
            {install!.progress > 0 ? ` · ${Math.round(install!.progress * 100)}%` : ''}
            {install!.message ? ` · ${install!.message}` : ''}
          </span>
        </div>
      ) : null}

      {failed && !torchError ? <span className="hint">上次安装失败：{install?.message || '请稍后重试'}</span> : null}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginTop: 'auto' }}>
        {canInstall ? (
          <button type="button" className="btn btn--primary btn--sm" onClick={onInstall} disabled={disabled}>
            {busy === 'install' || installing ? (
              <Spinner size={13} />
            ) : (
              <Download size={13} strokeWidth={1.75} />
            )}
            {installing ? '安装中' : item.installed ? '补装依赖' : '安装'}
          </button>
        ) : null}
        {item.installed && !active ? (
          <>
            <button type="button" className="btn btn--sm" onClick={onSelect} disabled={disabled}>
              {busy === 'select' ? <Spinner size={13} /> : <Check size={13} strokeWidth={1.75} />} 使用
            </button>
            <button type="button" className="btn btn--sm" onClick={onRemove} disabled={disabled}>
              {busy === 'remove' ? <Spinner size={13} /> : <Trash2 size={13} strokeWidth={1.75} />} 删除
            </button>
          </>
        ) : null}
        <button type="button" className="btn btn--ghost btn--sm" onClick={onHealth} disabled={disabled}>
          {busy === 'health' ? <Spinner size={13} /> : <ShieldCheck size={13} strokeWidth={1.75} />} 检测
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onTogglePlan}
          disabled={disabled}
          aria-expanded={planOpen}
          title="查看将下载的文件与体积（不下载）"
        >
          {planLoading ? <Spinner size={13} /> : <ListChecks size={13} strokeWidth={1.75} />} 预览
        </button>
      </div>

      {planOpen ? (
        <div className="engine-plan" data-engine-plan={item.id}>
          <div className="engine-plan__head">
            <span className="hint">预览只探测体积，不会下载</span>
            <button type="button" className="btn btn--ghost btn--sm" onClick={onTogglePlan} title="收起安装预览">
              收起
            </button>
          </div>

          {planLoading && !plan ? (
            <span className="engine-plan__status">
              <Spinner size={13} /> 正在探测文件体积…
            </span>
          ) : planError && !plan ? (
            <div className="inline-note inline-note--error" style={{ alignItems: 'center' }}>
              <span className="inline-note__icon">
                <AlertTriangle size={14} strokeWidth={1.75} />
              </span>
              <div style={{ display: 'grid', gap: 'var(--space-1)', minWidth: 0 }}>
                <span title={planError}>{planError}</span>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => {
                    setPlanError('');
                    setPlanAttempt((n) => n + 1);
                  }}
                  style={{ justifySelf: 'start' }}
                >
                  <RefreshCw size={12} strokeWidth={1.75} /> 重试
                </button>
              </div>
            </div>
          ) : plan ? (
            <>
              <div className="engine-plan__total">
                共 <span className="num">{planDownloads.length}</span> 个文件 ·{' '}
                <span className="num">{planTotalGb} GB</span>
              </div>

              <div className="engine-plan__table">
                {planDownloads.map((row) => (
                  <div className="engine-plan__row" key={row.dest}>
                    <span className="engine-plan__dest" title={`${row.dest}\n${row.url}`}>
                      {row.dest}
                    </span>
                    <span className="engine-plan__size num">{row.size_mb.toFixed(1)} MB</span>
                    <span className="engine-plan__src" title={row.size_source === 'head' ? 'HEAD 实测体积' : '清单声明体积'}>
                      {SIZE_SOURCE_LABEL[row.size_source ?? ''] ?? '—'}
                    </span>
                  </div>
                ))}
              </div>

              {plan.code_repo?.length ? (
                <div className="engine-plan__block">
                  <span className="engine-plan__label">需要代码仓库（按顺序回退，任一成功即停止）</span>
                  <ul className="engine-plan__list">
                    {plan.code_repo.slice(0, 3).map((entry, index) => (
                      <li key={`${entry.url ?? index}`} title={entry.url}>
                        {entry.url}
                        {entry.label ? ` · ${entry.label}` : ''}
                      </li>
                    ))}
                    {plan.code_repo.length > 3 ? <li>等 {plan.code_repo.length} 个</li> : null}
                  </ul>
                </div>
              ) : null}

              {plan.package?.length ? (
                <span className="engine-plan__label">
                  pip 依赖：<span style={{ fontFamily: 'var(--font-mono)' }}>{plan.package.slice(0, 3).join('、')}</span>
                  {plan.package.length > 3 ? ` 等 ${plan.package.length} 个` : ''}
                </span>
              ) : null}

              {planEnvKeys.length ? (
                <div className="engine-plan__block">
                  <span className="engine-plan__label">需要环境变量</span>
                  <ul className="engine-plan__list">
                    {planEnvKeys.map((key) => (
                      <li key={key} title={`${key}=${plan.requires_env?.[key] ?? ''}`}>
                        {key}={plan.requires_env?.[key] ?? ''}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

/* =========================== 引擎与模型卡片区 =========================== */

export function EngineSection({ id }: { id?: string } = {}) {
  const { pushToast, describeError } = useApp();
  const [data, setData] = useState<EngineList | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [installs, setInstalls] = useState<Record<string, InstallState>>({});
  const [busy, setBusy] = useState<Record<string, EngineAction | null>>({});
  const [details, setDetails] = useState<Record<string, string>>({});
  /** 同一时间只允许一张卡展开安装预览 */
  const [planId, setPlanId] = useState<string | null>(null);

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        const res = await api.listEngines();
        setData(res);
        setUnavailable(false);
      } catch (error) {
        const status = error instanceof ApiError ? error.status : 0;
        // 404 / 网络不可达：后端还没提供引擎接口，降级为空态而不是整页报错
        if (status === 404 || status === 405 || status === 501 || status === 0) {
          setUnavailable(true);
          setData(null);
        } else {
          pushToast({ level: 'error', ...describeError(error) });
        }
      } finally {
        setLoading(false);
      }
    },
    [describeError, pushToast],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const finishInstall = useCallback(
    (payload: EngineInstallStatus) => {
      setInstalls((prev) => ({ ...prev, [payload.engine_id]: { ...payload } }));
      setBusy((prev) => ({ ...prev, [payload.engine_id]: null }));
    },
    [],
  );

  // 全局事件：复用 ws.ts 订阅 engine.install（不轮询 install/status）
  useEffect(() => {
    const client = new WsClient('/ws/events');
    const off = client.onMessage((event) => {
      if (event.event !== 'engine.install') return;
      const payload = event.data as EngineInstallStatus;
      if (!payload?.engine_id) return;
      finishInstall(payload);
      if (!payload.done) return;
      if (payload.phase === 'error') {
        pushToast({ level: 'error', title: '引擎安装失败', description: payload.message || '请查看后端日志' });
      } else {
        pushToast({ level: 'success', title: '引擎安装完成', description: payload.message || '可以点击「使用」切换' });
      }
      // 后端引擎信息有 30s 缓存：立刻刷一次，1.5s 后再刷一次，避免仍显示“未安装”
      void load(true);
      window.setTimeout(() => void load(true), 1500);
    });
    return () => {
      off();
      client.close();
    };
  }, [finishInstall, load, pushToast]);

  const setAction = (id: string, action: EngineAction | null) =>
    setBusy((prev) => ({ ...prev, [id]: action }));

  const runAction = async (item: EngineItem, action: EngineAction, fn: () => Promise<void>) => {
    setAction(item.id, action);
    try {
      await fn();
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    } finally {
      setAction(item.id, null);
    }
  };

  const handleInstall = (item: EngineItem) =>
    runAction(item, 'install', async () => {
      const res = await api.installEngine(item.id);
      setInstalls((prev) => ({
        ...prev,
        [item.id]: { phase: 'download', progress: 0, message: `任务 ${res.job_id} 已排队`, done: false },
      }));
      pushToast({ level: 'info', title: `开始安装 ${item.name}`, description: '进度通过实时事件推送' });
    });

  const handleRemove = (item: EngineItem) => {
    const ok = window.confirm(`确定删除「${item.name}」？将释放约 ${item.size_mb} MB 磁盘空间，之后需要重新安装。`);
    if (!ok) return;
    void runAction(item, 'remove', async () => {
      const res = await api.removeEngine(item.id);
      pushToast({ level: 'success', title: `已删除 ${item.name}`, description: `释放 ${res.freed_mb} MB` });
      setInstalls((prev) => {
        const next = { ...prev };
        delete next[item.id];
        return next;
      });
      setDetails((prev) => {
        const next = { ...prev };
        delete next[item.id];
        return next;
      });
      setPlanId((prev) => (prev === item.id ? null : prev));
      await load(true);
    });
  };

  const handleSelect = (item: EngineItem) =>
    runAction(item, 'select', async () => {
      try {
        await api.selectEngine(item.id);
      } catch (error) {
        const status = error instanceof ApiError ? error.status : 0;
        if (status === 409 || status === 422 || status === 400) {
          const msg = error instanceof ApiError ? error.message : '引擎未就绪';
          pushToast({
            level: 'error',
            title: `无法切换到 ${item.name}`,
            description: msg || '引擎能力缺失（模型或依赖未就绪），请先安装或补装依赖',
          });
          return;
        }
        throw error;
      }
      pushToast({ level: 'success', title: `已切换到 ${item.name}`, description: '后续任务将使用该引擎' });
      await load(true);
    });

  const handleHealth = (item: EngineItem) =>
    runAction(item, 'health', async () => {
      const res = await api.engineHealth(item.id);
      const text = `${res.ready ? '运行正常' : '未就绪'}：${res.detail || '无详情'}${
        res.vram_allocated_mb ? `（显存 ${res.vram_allocated_mb} MB）` : ''
      }`;
      setDetails((prev) => ({ ...prev, [item.id]: text }));
      pushToast({
        level: res.ready ? 'success' : 'warning',
        title: `${item.name} 检测结果`,
        description: text.slice(0, 140),
      });
    });

  const handleCopy = async (item: EngineItem, command?: string) => {
    const text = command || packageCommand(item);
    const ok = await copyText(text);
    pushToast(
      ok
        ? { level: 'success', title: '已复制命令', description: text }
        : { level: 'warning', title: '复制失败', description: '请手动选择命令复制' },
    );
  };

  const activeItem = useMemo(() => data?.items.find((item) => item.id === data.active) ?? null, [data]);
  const [kindTab, setKindTab] = useState<'tts' | 'asr'>('tts');
  const kindOf = (item: any) => item.kind ?? 'tts';
  // 「语音识别」页签同时包含转写引擎（asr）与说话人模型（speaker），两者各自独立选择
  const inTab = (kind: string, tab: string) => kind === tab || (tab === 'asr' && kind === 'speaker');
  const visibleItems = useMemo(
    () => (data?.items ?? []).filter((item) => inTab(kindOf(item as any), kindTab)),
    [data, kindTab],
  );

  const body = (() => {
    if (loading && !data) {
      return (
        <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
          <Skeleton height={132} radius={12} />
          <Skeleton height={132} radius={12} />
          <p className="hint" role="status" aria-live="polite">
            <Spinner size={12} /> 正在读取引擎信息（要统计模型与 venv 体积，首次可能需要几十秒）…
          </p>
        </div>
      );
    }
    if (unavailable) {
      return (
        <EmptyState
          icon={<Boxes size={28} strokeWidth={1.5} />}
          title="后端未提供引擎接口（请升级后端）"
          description="当前后端版本没有 /api/engines 接口，引擎的安装、切换与检测暂不可用；其余设置不受影响。"
          action={
            <button type="button" className="btn btn--sm" onClick={() => void load()}>
              <RefreshCw size={13} strokeWidth={1.75} /> 重新检测
            </button>
          }
        />
      );
    }
    if (!data || !data.items.length) {
      return <EmptyState title="暂无可用引擎" description="后端未上报任何引擎条目。" />;
    }
    return (
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
          gap: 'var(--space-3)',
        }}
      >
        <div className="probe-bar" style={{ marginBottom: 10 }}>
          {(['tts', 'asr'] as const).map((kind) => {
            const count = (data?.items ?? []).filter((i: any) => inTab(i.kind ?? 'tts', kind)).length;
            return (
              <button
                key={kind}
                type="button"
                className="btn btn--sm"
                aria-pressed={kindTab === kind}
                style={{ background: kindTab === kind ? 'var(--color-primary-soft)' : undefined }}
                onClick={() => setKindTab(kind)}
              >
                {kind === 'asr' ? `语音识别（${count}）` : `语音合成（${count}）`}
              </button>
            );
          })}
        </div>
        {visibleItems.map((item) => (
          <EngineCard
            key={item.id}
            item={item}
            active={
              item.id ===
              ((item as any).kind === 'speaker'
                ? (data as any).active_speaker
                : kindTab === 'asr'
                  ? (data as any).active_asr
                  : data.active)
            }
            install={installs[item.id] ?? null}
            busy={busy[item.id] ?? null}
            detail={details[item.id]}
            planOpen={planId === item.id}
            onTogglePlan={() => setPlanId((prev) => (prev === item.id ? null : item.id))}
            onInstall={() => handleInstall(item)}
            onRemove={() => handleRemove(item)}
            onSelect={() => handleSelect(item)}
            onHealth={() => handleHealth(item)}
            onCopy={(command) => void handleCopy(item, command)}
          />
        ))}
      </div>
    );
  })();

  return (
    <SectionCard
      id={id}
      title={<span style={{ whiteSpace: 'nowrap' }}>引擎与模型</span>}
      hint="安装、切换与检测本地 TTS 引擎"
      icon={<Boxes size={15} strokeWidth={1.75} />}
      actions={
        <>
          {activeItem ? <span className="hint">当前使用：{activeItem.name}</span> : null}
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => void load()} disabled={loading}>
            {loading ? <Spinner size={13} /> : <RefreshCw size={13} strokeWidth={1.75} />} 刷新
          </button>
        </>
      }
    >
      {body}
    </SectionCard>
  );
}

export default EngineSection;
