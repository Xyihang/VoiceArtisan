import React from 'react';
import { CheckCircle2, Info, Moon, Monitor, Search, Sun, X, XCircle, AlertTriangle } from 'lucide-react';
import { paletteShortcut } from '../lib/shortcuts';
import { useApp, type ThemeMode } from '../state/AppContext';

/** 顶栏命令入口：可见的搜索按钮 + 平台化快捷键提示（不隐藏快捷键） */
export function CommandButton({ onOpen }: { onOpen: () => void }) {
  const shortcut = paletteShortcut();
  return (
    <button
      type="button"
      className="cmd-trigger"
      onClick={onOpen}
      title={`打开命令面板（${shortcut}）`}
      aria-label={`打开命令面板，快捷键 ${shortcut}`}
      aria-keyshortcuts="Meta+K Control+K"
    >
      <Search size={15} strokeWidth={1.75} aria-hidden="true" />
      <span className="cmd-trigger__label">搜索或跳转</span>
      <kbd className="kbd cmd-trigger__kbd">{shortcut}</kbd>
    </button>
  );
}

const THEME_ORDER: ThemeMode[] = ['system', 'light', 'dark'];
const THEME_META: Record<ThemeMode, { label: string; icon: typeof Sun }> = {
  system: { label: '跟随系统', icon: Monitor },
  light: { label: '浅色', icon: Sun },
  dark: { label: '深色', icon: Moon },
};

export function ThemeToggle() {
  const { theme, setTheme } = useApp();
  const current = THEME_META[theme];
  const next = THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length];
  const Icon = current.icon;
  return (
    <button
      type="button"
      className="btn btn--ghost btn--icon"
      onClick={() => setTheme(next)}
      title={`主题：${current.label}（点击切换为${THEME_META[next].label}）`}
      aria-label={`当前主题${current.label}，点击切换`}
    >
      <Icon size={18} strokeWidth={1.75} />
    </button>
  );
}

const TOAST_ICON = {
  success: CheckCircle2,
  error: XCircle,
  warning: AlertTriangle,
  info: Info,
};

export function Toasts() {
  const { toasts, dismissToast } = useApp();
  if (!toasts.length) return null;
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((toast) => {
        const Icon = TOAST_ICON[toast.level];
        return (
          <div key={toast.id} className={`toast toast--${toast.level}`}>
            <Icon size={16} strokeWidth={1.75} style={{ marginTop: 2, flex: 'none' }} />
            <div className="toast__body">
              <div className="toast__title">{toast.title}</div>
              {toast.description ? <div className="toast__desc">{toast.description}</div> : null}
            </div>
            <button
              type="button"
              className="btn btn--ghost btn--icon btn--sm"
              onClick={() => dismissToast(toast.id)}
              aria-label="关闭通知"
            >
              <X size={14} strokeWidth={2} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
