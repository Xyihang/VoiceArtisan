import React from 'react';
import ReactDOM from 'react-dom';
import { AlertTriangle, Inbox, Loader2, X } from 'lucide-react';
import { STATUS_LABEL } from '../lib/format';

export function Icon({ as: Component, size = 16, ...rest }: any) {
  return <Component size={size} strokeWidth={1.75} {...rest} />;
}

export function SectionCard({
  title,
  hint,
  actions,
  children,
  flush,
  icon,
  id,
  style,
  className,
}: {
  title?: React.ReactNode;
  hint?: React.ReactNode;
  actions?: React.ReactNode;
  children?: React.ReactNode;
  flush?: boolean;
  icon?: React.ReactNode;
  /** 锚点 id（设置页左侧导航用） */
  id?: string;
  style?: React.CSSProperties;
  /** 追加的额外类名（如 card--fill） */
  className?: string;
}) {
  return (
    <section id={id} className={[flush ? 'card card--flush' : 'card', className].filter(Boolean).join(' ')} style={style}>
      {(title || actions) && (
        <header className="card__head">
          <div className="card__title">
            {icon}
            {title}
            {hint ? <span className="card__hint">{hint}</span> : null}
          </div>
          {actions ? <div className="card__actions">{actions}</div> : null}
        </header>
      )}
      <div className="card__body">{children}</div>
    </section>
  );
}

export function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: React.ReactNode;
  hint?: React.ReactNode;
  htmlFor?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="field">
      <label className="label" htmlFor={htmlFor}>
        {label}
        {hint ? <span className="label__hint">{hint}</span> : null}
      </label>
      {children}
    </div>
  );
}

const STATUS_STYLE: Record<string, { cls: string; dot: string }> = {
  queued: { cls: 'badge--muted', dot: 'status-dot' },
  running: { cls: 'badge--info', dot: 'status-dot status-dot--busy' },
  completed: { cls: 'badge--success', dot: 'status-dot status-dot--ok' },
  failed: { cls: 'badge--error', dot: 'status-dot status-dot--error' },
  canceled: { cls: 'badge--warning', dot: 'status-dot status-dot--warn' },
};

export function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLE[status] || STATUS_STYLE.queued;
  return (
    <span className={`badge ${style.cls}`}>
      <i className={style.dot} aria-hidden="true" />
      {STATUS_LABEL[status] || status}
    </span>
  );
}

export function ProgressBar({ value, indeterminate }: { value: number; indeterminate?: boolean }) {
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
      <div
        className={indeterminate ? 'progress__bar progress__bar--indeterminate' : 'progress__bar'}
        style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }}
      />
    </div>
  );
}

export function Meter({ ratio, tone = 'normal' }: { ratio: number; tone?: 'normal' | 'warn' | 'error' }) {
  const cls =
    tone === 'error' ? 'meter__fill meter__fill--error' : tone === 'warn' ? 'meter__fill meter__fill--warn' : 'meter__fill';
  return (
    <div className="meter">
      <div className={cls} style={{ width: `${Math.max(0, Math.min(100, ratio * 100))}%` }} />
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-state__icon">{icon || <Inbox size={28} strokeWidth={1.5} />}</div>
      <div className="empty-state__title">{title}</div>
      {description ? <p className="empty-state__desc">{description}</p> : null}
      {action}
    </div>
  );
}

export function Skeleton({ height = 16, width = '100%', radius }: { height?: number; width?: number | string; radius?: number }) {
  return <div className="skeleton" style={{ height, width, borderRadius: radius }} aria-hidden="true" />;
}

export function InlineNote({
  kind = 'warning',
  children,
  action,
}: {
  kind?: 'warning' | 'info' | 'error';
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  const cls = kind === 'info' ? 'inline-note inline-note--info' : kind === 'error' ? 'inline-note inline-note--error' : 'inline-note';
  return (
    <div className={cls} role={kind === 'error' ? 'alert' : 'status'}>
      <span className="inline-note__icon">
        <AlertTriangle size={14} strokeWidth={1.75} />
      </span>
      <div>
        {children}
        {action ? <div style={{ marginTop: 'var(--space-2)' }}>{action}</div> : null}
      </div>
    </div>
  );
}

export function Spinner({ size = 16 }: { size?: number }) {
  return <Loader2 size={size} strokeWidth={2} className="spin" aria-hidden="true" />;
}

export function Modal({
  title,
  children,
  footer,
  onClose,
}: {
  title: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  onClose: () => void;
}) {
  // 挂到 body：避免祖先的 transform / overflow 让 fixed 失效（弹窗随页面滚动的根因）
  const portalTarget = typeof document === 'undefined' ? null : document.body;

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);

    // 锁背景滚动，并补偿滚动条宽度避免页面跳动
    const body = document.body;
    const prevOverflow = body.style.overflow;
    const prevPaddingRight = body.style.paddingRight;
    const scrollbar = window.innerWidth - document.documentElement.clientWidth;
    body.style.overflow = 'hidden';
    if (scrollbar > 0) body.style.paddingRight = `${scrollbar}px`;

    return () => {
      window.removeEventListener('keydown', onKey);
      body.style.overflow = prevOverflow;
      body.style.paddingRight = prevPaddingRight;
    };
  }, [onClose]);

  const node = (
    <div className="modal-layer">
      <div className="overlay" onClick={onClose} />
      <div className="modal" role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : '对话框'}>
        <header className="modal__head">
          <h2>{title}</h2>
          <button type="button" className="btn btn--ghost btn--icon" onClick={onClose} aria-label="关闭">
            <X size={16} strokeWidth={1.75} />
          </button>
        </header>
        <div className="modal__body">{children}</div>
        {footer ? <footer className="modal__foot">{footer}</footer> : null}
      </div>
    </div>
  );

  if (!portalTarget) return node;
  return ReactDOM.createPortal(node, portalTarget);
}

/** 状态点：语义色只落在小点上，底色保持中性（不堆彩色 pill） */
export function StatusDot({
  tone = 'ok',
  label,
}: {
  tone?: 'ok' | 'warn' | 'danger' | 'idle';
  label?: string;
}) {
  const cls =
    tone === 'danger'
      ? 'status-dot status-dot--error'
      : tone === 'warn'
        ? 'status-dot status-dot--warn'
        : tone === 'idle'
          ? 'status-dot'
          : 'status-dot status-dot--ok';
  return <i className={cls} role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true} />;
}

/** 危险操作二次确认：写清影响面 + 明确动作按钮（Esc / 取消均可退出） */
export function ConfirmDialog({
  title,
  children,
  confirmLabel = '确认',
  busy = false,
  onConfirm,
  onCancel,
  secondaryLabel,
  onSecondary,
}: {
  title: string;
  children: React.ReactNode;
  confirmLabel?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  /** 可选的次要动作（例如「仅停止服务（不关机）」），排在危险按钮左侧 */
  secondaryLabel?: string;
  onSecondary?: () => void;
}) {
  return (
    <Modal
      title={title}
      onClose={() => {
        if (!busy) onCancel();
      }}
      footer={
        <>
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            取消
          </button>
          {secondaryLabel && onSecondary ? (
            <button type="button" className="btn" onClick={onSecondary} disabled={busy}>
              {secondaryLabel}
            </button>
          ) : null}
          <button type="button" className="btn btn--danger" onClick={onConfirm} disabled={busy}>
            {busy ? '执行中…' : confirmLabel}
          </button>
        </>
      }
    >
      <div className="confirm-body">{children}</div>
    </Modal>
  );
}

export function KV({ label, value, unit }: { label: string; value: React.ReactNode; unit?: string }) {
  return (
    <div className="kv__item">
      <span className="kv__label">{label}</span>
      <span className="kv__value num">
        {value}
        {unit ? <span className="unit">{unit}</span> : null}
      </span>
    </div>
  );
}
