import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AudioLines,
  BookOpen,
  Check,
  ListChecks,
  Mic2,
  Moon,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Wand2,
  type LucideIcon,
} from 'lucide-react';
import { api } from '../lib/api';
import { paletteShortcut } from '../lib/shortcuts';
import { useApp } from '../state/AppContext';
import type { EngineItem, EngineList } from '../types';

type CommandItem = {
  id: string;
  group: string;
  label: string;
  hint?: string;
  icon: LucideIcon;
  keywords: string;
  run: () => void | Promise<void>;
};

const ENGINE_STATUS_LABEL: Record<string, string> = {
  ready: '可用',
  not_installed: '未安装',
  package_missing: '依赖缺失',
  error: '异常',
  experimental: '实验性',
};

/** 跳转前先广播意图，工作台据此切换能力类型（自定义事件，避免为一处跳转引入全局状态） */
function openWorkbench(tab: 'tts' | 'clone') {
  window.dispatchEvent(new CustomEvent('qwentts:workbench-tab', { detail: tab }));
}

export function CommandPalette({
  open,
  onClose,
  onNavigate,
}: {
  open: boolean;
  onClose: () => void;
  onNavigate: (route: 'workbench' | 'voices' | 'tasks' | 'settings' | 'status' | 'guide') => void;
}) {
  const { pushToast, describeError, theme, setTheme, reloadSettings, refreshHealth } = useApp();
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const [engines, setEngines] = useState<EngineList | null>(null);
  const [shown, setShown] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  // 打开：记录原焦点、复位输入、拉取引擎列表
  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    setQuery('');
    setCursor(0);
    setShown(true);
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0);
    let cancelled = false;
    void api
      .listEngines()
      .then((res) => {
        if (!cancelled) setEngines(res);
      })
      .catch(() => {
        if (!cancelled) setEngines({ active: '', items: [] });
      });
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open]);

  // 关闭：还原焦点
  const close = useCallback(() => {
    setShown(false);
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (shown) return;
    restoreRef.current?.focus?.();
    restoreRef.current = null;
  }, [shown]);

  const pickEngine = useCallback(
    async (engine: EngineItem) => {
      try {
        const res = await api.selectEngine(engine.id);
        pushToast({ level: 'success', title: `已切换引擎：${engine.name}`, description: `当前生效：${res.active}` });
        setEngines((prev) => (prev ? { ...prev, active: res.active } : prev));
        void refreshHealth();
      } catch (error) {
        pushToast({ level: 'error', ...describeError(error) });
      }
    },
    [describeError, pushToast, refreshHealth],
  );

  const items = useMemo<CommandItem[]>(() => {
    const pages: CommandItem[] = [
      { id: 'page-workbench', group: '页面跳转', label: '工作台', icon: AudioLines, keywords: 'workbench 首页 生成', run: () => onNavigate('workbench') },
      { id: 'page-voices', group: '页面跳转', label: '音色库', icon: Mic2, keywords: 'voices 音色', run: () => onNavigate('voices') },
      { id: 'page-tasks', group: '页面跳转', label: '任务中心', icon: ListChecks, keywords: 'tasks 任务 列表', run: () => onNavigate('tasks') },
      { id: 'page-settings', group: '页面跳转', label: '设置', icon: Settings2, keywords: 'settings 配置', run: () => onNavigate('settings') },
      { id: 'page-status', group: '页面跳转', label: '状态与日志', icon: Activity, keywords: 'status 日志 健康', run: () => onNavigate('status') },
      { id: 'page-guide', group: '页面跳转', label: '教程', icon: BookOpen, keywords: 'guide 教程 帮助 文档 说明 help docs', run: () => onNavigate('guide') },
    ];
    const actions: CommandItem[] = [
      {
        id: 'act-new-tts',
        group: '操作',
        label: '新建语音生成',
        hint: '工作台 · 语音生成',
        icon: Plus,
        keywords: 'new tts 语音 生成 新建',
        run: () => {
          onNavigate('workbench');
          openWorkbench('tts');
        },
      },
      {
        id: 'act-new-clone',
        group: '操作',
        label: '新建声音克隆',
        hint: '工作台 · 声音克隆',
        icon: Wand2,
        keywords: 'new clone 克隆 新建',
        run: () => {
          onNavigate('workbench');
          openWorkbench('clone');
        },
      },
      {
        id: 'act-failed-tasks',
        group: '操作',
        label: '只看失败任务',
        hint: '任务中心 · status=failed',
        icon: ListChecks,
        keywords: 'failed 失败 筛选',
        run: () => {
          onNavigate('tasks');
          window.location.hash = '#/tasks?status=failed';
        },
      },
      {
        id: 'act-refresh',
        group: '操作',
        label: '刷新状态',
        hint: '健康 + 设置 + 引擎',
        icon: RefreshCw,
        keywords: 'refresh 刷新 状态',
        run: async () => {
          await Promise.all([refreshHealth(), reloadSettings()]);
          const res = await api.listEngines().catch(() => null);
          if (res) setEngines(res);
          pushToast({ level: 'success', title: '状态已刷新' });
        },
      },
      {
        id: 'act-theme',
        group: '操作',
        label: '切换主题',
        hint: `当前：${theme === 'system' ? '跟随系统' : theme === 'light' ? '浅色' : '深色'}`,
        icon: Moon,
        keywords: 'theme 主题 深色 浅色 dark light',
        run: () => {
          const order = ['system', 'light', 'dark'] as const;
          const next = order[(order.indexOf(theme) + 1) % order.length];
          setTheme(next);
          pushToast({ level: 'info', title: `主题已切换为${next === 'system' ? '跟随系统' : next === 'light' ? '浅色' : '深色'}` });
        },
      },
    ];
    const engineItems: CommandItem[] = (engines?.items ?? []).map((engine) => {
      const active = engines?.active === engine.id;
      return {
        id: `engine-${engine.id}`,
        group: '引擎',
        label: engine.name,
        hint: `${engine.params} · ${ENGINE_STATUS_LABEL[engine.status] || engine.status}${active ? ' · 当前' : ''}`,
        icon: active ? Check : AudioLines,
        keywords: `${engine.id} ${engine.vendor} 引擎`,
        run: () => pickEngine(engine),
      };
    });
    return [...pages, ...actions, ...engineItems];
  }, [engines, onNavigate, pickEngine, pushToast, reloadSettings, refreshHealth, setTheme, theme]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => `${item.label} ${item.hint || ''} ${item.keywords}`.toLowerCase().includes(q));
  }, [items, query]);

  useEffect(() => {
    if (cursor > filtered.length - 1) setCursor(filtered.length ? filtered.length - 1 : 0);
  }, [cursor, filtered.length]);

  const runItem = useCallback(
    async (item: CommandItem | undefined) => {
      if (!item) return;
      close();
      try {
        await item.run();
      } catch (error) {
        pushToast({ level: 'error', ...describeError(error) });
      }
    },
    [close, describeError, pushToast],
  );

  // 高亮项跟随滚动（仅列表内，不做页面滚动）
  useEffect(() => {
    if (!shown) return;
    const node = listRef.current?.querySelector<HTMLElement>(`[data-index="${cursor}"]`);
    node?.scrollIntoView({ block: 'nearest' });
  }, [cursor, shown]);

  if (!open) return null;

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setCursor((prev) => (filtered.length ? (prev + 1) % filtered.length : 0));
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setCursor((prev) => (filtered.length ? (prev - 1 + filtered.length) % filtered.length : 0));
      return;
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      setCursor(event.key === 'Home' ? 0 : Math.max(0, filtered.length - 1));
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      void runItem(filtered[cursor]);
      return;
    }
    if (event.key === 'Tab') {
      // 焦点陷阱：Tab 只在面板内部循环
      const focusables = dialogRef.current?.querySelectorAll<HTMLElement>(
        'input, button:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusables || focusables.length === 0) return;
      const list = Array.from(focusables);
      const first = list[0];
      const last = list[list.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  };

  let lastGroup = '';

  return (
    <div className="cmdk__overlay" onMouseDown={close}>
      <div
        className="cmdk"
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        ref={dialogRef}
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="cmdk__search">
          <Search size={16} strokeWidth={1.75} aria-hidden="true" />
          <input
            ref={inputRef}
            className="cmdk__input"
            value={query}
            role="combobox"
            aria-expanded="true"
            aria-controls="cmdk-list"
            aria-autocomplete="list"
            aria-label="搜索命令"
            aria-activedescendant={filtered[cursor] ? `cmdk-item-${filtered[cursor].id}` : undefined}
            placeholder="搜索页面、操作或引擎…"
            onChange={(event) => {
              setQuery(event.target.value);
              setCursor(0);
            }}
          />
          <kbd className="kbd">{paletteShortcut()}</kbd>
        </div>
        <div className="cmdk__list" id="cmdk-list" role="listbox" aria-label="命令结果" ref={listRef}>
          {filtered.length === 0 ? (
            <div className="cmdk__empty">
              没有匹配的命令<span className="hint">试试「任务」「引擎」「主题」</span>
            </div>
          ) : (
            filtered.map((item, index) => {
              const Icon = item.icon;
              const header = item.group !== lastGroup ? item.group : null;
              lastGroup = item.group;
              return (
                <React.Fragment key={item.id}>
                  {header ? <div className="cmdk__group">{header}</div> : null}
                  <div
                    id={`cmdk-item-${item.id}`}
                    data-index={index}
                    role="option"
                    aria-selected={index === cursor}
                    className="cmdk__item"
                    onMouseMove={() => setCursor(index)}
                    onClick={() => void runItem(item)}
                  >
                    <Icon size={15} strokeWidth={1.75} aria-hidden="true" />
                    <span className="cmdk__label">{item.label}</span>
                    {item.hint ? <span className="cmdk__hint">{item.hint}</span> : null}
                  </div>
                </React.Fragment>
              );
            })
          )}
        </div>
        <footer className="cmdk__foot">
          <span>
            <kbd className="kbd">↑</kbd>
            <kbd className="kbd">↓</kbd> 选择
          </span>
          <span>
            <kbd className="kbd">Enter</kbd> 执行
          </span>
          <span>
            <kbd className="kbd">Esc</kbd> 关闭
          </span>
        </footer>
      </div>
    </div>
  );
}

export default CommandPalette;
