import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from '../lib/api';
import { WsClient } from '../lib/ws';
import type { Health, Meta, SettingsPayload, Task, WsEvent } from '../types';

export type ThemeMode = 'light' | 'dark' | 'system';

export type Toast = {
  id: number;
  level: 'success' | 'error' | 'warning' | 'info';
  title: string;
  description?: string;
};

type AppContextValue = {
  theme: ThemeMode;
  setTheme: (theme: ThemeMode) => void;
  resolvedTheme: 'light' | 'dark';
  health: Health | null;
  meta: Meta | null;
  settings: SettingsPayload | null;
  settingsLoading: boolean;
  reloadSettings: () => Promise<void>;
  /** 重新拉 /api/meta（运行模式变化后能力判定要跟着变） */
  reloadMeta: () => Promise<void>;
  refreshHealth: () => Promise<Health | null>;
  live: boolean;
  events: WsEvent[] | null;
  tasks: Record<string, Task>;
  upsertTask: (task: Task) => void;
  removeTasks: (ids: string[]) => void;
  activeTaskId: string | null;
  setActiveTaskId: (id: string | null) => void;
  toasts: Toast[];
  pushToast: (toast: Omit<Toast, 'id'>) => void;
  dismissToast: (id: number) => void;
  describeError: (error: unknown) => { title: string; description: string };
};

const AppContext = createContext<AppContextValue | null>(null);

function readSystemTheme(): 'light' | 'dark' {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemeMode>(() => {
    const stored = localStorage.getItem('qwentts.theme');
    return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'system';
  });
  const [systemTheme, setSystemTheme] = useState<'light' | 'dark'>(readSystemTheme);
  const [health, setHealth] = useState<Health | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [settingsLoading, setSettingsLoading] = useState(true);
  const [live, setLive] = useState(false);
  const [tasks, setTasks] = useState<Record<string, Task>>({});
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastId = useRef(0);
  const clientRef = useRef<WsClient | null>(null);

  const resolvedTheme = theme === 'system' ? systemTheme : theme;

  useEffect(() => {
    document.documentElement.dataset.theme = resolvedTheme;
    document.documentElement.style.colorScheme = resolvedTheme;
  }, [resolvedTheme]);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (event: MediaQueryListEvent) => setSystemTheme(event.matches ? 'dark' : 'light');
    media.addEventListener('change', handler);
    return () => media.removeEventListener('change', handler);
  }, []);

  const setTheme = useCallback((next: ThemeMode) => {
    setThemeState(next);
    localStorage.setItem('qwentts.theme', next);
  }, []);

  const pushToast = useCallback((toast: Omit<Toast, 'id'>) => {
    toastId.current += 1;
    const id = toastId.current;
    setToasts((prev) => [...prev.slice(-3), { ...toast, id }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 5200);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const describeError = useCallback((error: unknown) => {
    if (error instanceof ApiError) {
      return { title: error.message, description: error.hint || `错误码：${error.code}` };
    }
    return { title: '操作失败', description: (error as Error)?.message || '请稍后重试' };
  }, []);

  const upsertTask = useCallback((task: Task) => {
    setTasks((prev) => ({ ...prev, [task.id]: { ...prev[task.id], ...task } }));
  }, []);

  const removeTasks = useCallback((ids: string[]) => {
    setTasks((prev) => {
      const next = { ...prev };
      ids.forEach((id) => delete next[id]);
      return next;
    });
  }, []);

  /** 命令面板「刷新状态」用：单独重拉健康信息，不动设置 */
  const refreshHealth = useCallback(async () => {
    try {
      const next = await api.health();
      setHealth(next);
      return next;
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
      return null;
    }
  }, [describeError, pushToast]);

  const reloadMeta = useCallback(async () => {
    try {
      const data = await api.meta();
      setMeta(data);
    } catch {
      /* meta 拿不到时保留旧值，不打断用户操作 */
    }
  }, []);

  const reloadSettings = useCallback(async () => {
    setSettingsLoading(true);
    try {
      const [data] = await Promise.all([api.settings(), reloadMeta()]);
      setSettings(data);
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    } finally {
      setSettingsLoading(false);
    }
  }, [describeError, pushToast, reloadMeta]);

  // 初始化：健康、元数据、设置
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [h, m] = await Promise.all([api.health(), api.meta()]);
        if (cancelled) return;
        setHealth(h);
        setMeta(m);
      } catch (error) {
        if (!cancelled) pushToast({ level: 'error', ...describeError(error) });
      }
      await reloadSettings();
    })();
    return () => {
      cancelled = true;
    };
  }, [describeError, pushToast, reloadSettings]);

  // 实时事件：健康、任务状态
  useEffect(() => {
    const client = new WsClient('/ws/events');
    clientRef.current = client;
    const offMsg = client.onMessage((event) => {
      if (event.event === 'system.health') {
        setHealth(event.data as Health);
        return;
      }
      if (event.event === 'task.update' || event.event === 'task.queued') {
        upsertTask(event.data as Task);
        return;
      }
      if (event.event === 'task.deleted') {
        removeTasks((event.data as { ids: string[] }).ids);
        return;
      }
      if (event.event === 'system.idle_shutdown') {
        pushToast({ level: 'warning', title: '云端已自动停机', description: (event.data as any).message });
      }
      if (event.event === 'system.settings') {
        void reloadSettings();
      }
    });
    const offStatus = client.onStatus((online) => {
      setLive(online);
      if (!online) {
        setHealth((prev) => (prev ? { ...prev, active_backend: 'offline' } : prev));
      }
    });
    return () => {
      offMsg();
      offStatus();
      client.close();
    };
  }, [pushToast, reloadSettings, removeTasks, upsertTask]);

  const value = useMemo<AppContextValue>(
    () => ({
      theme,
      setTheme,
      resolvedTheme,
      health,
      meta,
      settings,
      settingsLoading,
      reloadSettings,
    reloadMeta,
      refreshHealth,
      live,
      events: null,
      tasks,
      upsertTask,
      removeTasks,
      activeTaskId,
      setActiveTaskId,
      toasts,
      pushToast,
      dismissToast,
      describeError,
    }),
    [
      theme,
      setTheme,
      resolvedTheme,
      health,
      meta,
      settings,
      settingsLoading,
      reloadSettings,
    reloadMeta,
      refreshHealth,
      live,
      tasks,
      upsertTask,
      removeTasks,
      activeTaskId,
      toasts,
      pushToast,
      dismissToast,
      describeError,
    ],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp 必须在 AppProvider 内使用');
  return ctx;
}
