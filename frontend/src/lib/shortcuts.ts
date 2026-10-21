import type { KeyboardEvent as ReactKeyboardEvent } from 'react';

/** 平台相关快捷键标签：命令面板在 macOS 显示 ⌘K，其它平台显示 Ctrl K */
export function isMacPlatform(): boolean {
  if (typeof navigator === 'undefined') return false;
  const platform = navigator.platform || '';
  const ua = navigator.userAgent || '';
  return /Mac|iPhone|iPad|iPod/i.test(platform) || /Mac OS X/i.test(ua);
}

/** 命令面板快捷键（紧凑版，用于按钮内 kbd） */
export function paletteShortcut(): string {
  return isMacPlatform() ? '⌘K' : 'Ctrl K';
}

/** 修饰键前缀：⌘ / Ctrl */
export function modLabel(): string {
  return isMacPlatform() ? '⌘' : 'Ctrl';
}

/** 是否按下了平台修饰键（mac 用 Meta，其它用 Ctrl） */
export function hasMod(event: KeyboardEvent | ReactKeyboardEvent): boolean {
  return isMacPlatform() ? event.metaKey : event.ctrlKey;
}

/** 焦点是否在可编辑控件里——这些位置不接管单键快捷键 */
export function isEditableTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  const tag = el.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
  return el.isContentEditable === true;
}
