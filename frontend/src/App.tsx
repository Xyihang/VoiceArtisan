import React, { useEffect, useState } from 'react';
import {
  Activity,
  AudioLines,
  BookOpen,
  ListChecks,
  Mic2,
  Settings2,
  type LucideIcon,
} from 'lucide-react';
import { AppProvider, useApp } from './state/AppContext';
import { BackendStatus } from './components/BackendStatus';
import { ThemeToggle, Toasts } from './components/Chrome';
import { CommandPalette } from './components/CommandPalette';
import { DockNav } from './components/DockNav';
import { hasMod } from './lib/shortcuts';
import { Skeleton } from './components/ui';
import WorkbenchPage from './pages/Workbench';
import VoicesPage from './pages/VoicesPage';
import TasksPage from './pages/TasksPage';
import SettingsPage from './pages/SettingsPage';
import StatusPage from './pages/StatusPage';
import GuidePage from './pages/GuidePage';

type RouteId = 'workbench' | 'voices' | 'tasks' | 'settings' | 'status' | 'guide';

const ROUTES: { id: RouteId; label: string; icon: LucideIcon; title: string }[] = [
  { id: 'workbench', label: '工作台', icon: AudioLines, title: '工作台' },
  { id: 'voices', label: '音色库', icon: Mic2, title: '音色库' },
  { id: 'tasks', label: '任务中心', icon: ListChecks, title: '任务中心' },
  { id: 'settings', label: '设置', icon: Settings2, title: '设置' },
  { id: 'status', label: '状态与日志', icon: Activity, title: '状态与日志' },
  { id: 'guide', label: '教程', icon: BookOpen, title: '教程' },
];

/** 解析 `#/tasks?status=failed&page=2`：路径决定路由，查询串留给页面（筛选写进 URL） */
function readHashRoute(): RouteId {
  const raw = window.location.hash.replace(/^#\/?/, '');
  const path = raw.split('?')[0];
  return (ROUTES.find((r) => r.id === path)?.id || 'workbench') as RouteId;
}

function useHashRoute(): [RouteId, (id: RouteId) => void] {
  const [route, setRoute] = useState<RouteId>(readHashRoute);
  useEffect(() => {
    const handler = () => setRoute(readHashRoute());
    window.addEventListener('hashchange', handler);
    return () => window.removeEventListener('hashchange', handler);
  }, []);
  const navigate = (id: RouteId) => {
    window.location.hash = `#/${id}`;
    setRoute(id);
  };
  return [route, navigate];
}

function ModeBadge() {
  const { health, settings } = useApp();
  const mode = health?.mode || settings?.run.mode || '—';
  const text =
    mode === 'hybrid'
      ? '混合模式'
      : mode === 'cloud'
        ? '仅云端'
        : mode === 'local'
          ? '仅本地'
          : mode === 'mock'
            ? '模拟引擎'
            : String(mode);
  return <span className="chip badge--neutral">{text}</span>;
}

function LoadingShell() {
  return (
    <div className="page">
      <Skeleton height={28} width={220} />
      <div className="workbench">
        <div className="workbench__column">
          <Skeleton height={280} radius={12} />
          <Skeleton height={120} radius={12} />
        </div>
        <div className="workbench__column">
          <Skeleton height={220} radius={12} />
          <Skeleton height={160} radius={12} />
        </div>
      </div>
    </div>
  );
}

function Shell() {
  const { health, meta, tasks } = useApp();
  const [route, navigate] = useHashRoute();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const current = ROUTES.find((r) => r.id === route) || ROUTES[0];
  const activeTasks = Object.values(tasks).filter((t) => t.status === 'running' || t.status === 'queued').length;

  // 全局命令面板：Ctrl/Cmd + K（输入框内同样生效，属修饰键组合）
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (hasMod(event) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const page = (() => {
    if (!health && !meta) return <LoadingShell />;
    switch (route) {
      case 'voices':
        return <VoicesPage />;
      case 'tasks':
        return <TasksPage />;
      case 'settings':
        return <SettingsPage />;
      case 'status':
        return <StatusPage />;
      case 'guide':
        return <GuidePage />;
      default:
        return <WorkbenchPage />;
    }
  })();

  return (
    <div className="shell">
      <div className="main">
        <header className="topbar">
          <span className="topbar__title">{current.title}</span>
          <div className="topbar__brand">
            <span className="topbar__brand-title">声匠</span>
            <span className="topbar__brand-mark" aria-hidden="true">
              <AudioLines size={18} strokeWidth={2} />
            </span>
            <span className="topbar__brand-sub">VoiceSmith</span>
          </div>
          <div className="topbar__meta">
            <ModeBadge />
            <BackendStatus />
            <ThemeToggle />
          </div>
        </header>
        <main>{page}</main>
        <footer className="app-footer">
          <span>
            Made by{' '}
            <a href="mailto:xiyihang360@163.com" className="app-footer__link">
              xiyihang360@163.com
            </a>
          </span>
          <span className="app-footer__dot" aria-hidden="true">
            ·
          </span>
          <span>保留所有权利</span>
          <span className="app-footer__dot" aria-hidden="true">
            ·
          </span>
          <span className="num">© 2026 xiyihang</span>
        </footer>
      </div>
      <DockNav
        routes={ROUTES.map((r) => ({ id: r.id, label: r.label, icon: r.icon }))}
        current={route}
        onNavigate={(id) => navigate(id as RouteId)}
        activeTasks={activeTasks}
        onOpenSearch={() => setPaletteOpen(true)}
      />
      <Toasts />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} onNavigate={navigate} />
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
