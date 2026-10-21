import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {Power,
  BadgeCheck,
  Cpu,
  Database,
  PlugZap,
  RefreshCw,
  Save,
  Search,
  ServerCog,
  Sparkles,
  Trash2,
  X,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import { api } from '../lib/api';
import { useApp } from '../state/AppContext';
import type { RunMode } from '../types';
import { ConfirmDialog, Field, InlineNote, SectionCard, Spinner, StatusDot } from '../components/ui';
import { EngineSection } from '../components/EngineCard';
import { CloudAgentWizard } from '../components/CloudAgentWizard';
import { parseSshCommand } from '../lib/sshcmd';

const MODEL_KINDS: { id: string; label: string; short: string; desc: string }[] = [
  { id: 'custom_voice', label: '语音生成（CustomVoice 1.7B/0.6B）', short: '语音生成', desc: '内置音色 + 指令控制，最常用' },
  { id: 'base', label: '声音克隆（Base 1.7B/0.6B）', short: '声音克隆', desc: '零样本克隆，参考音频编码器需要额外显存' },
  { id: 'voice_design', label: '音色设计（VoiceDesign 1.7B）', short: '音色设计', desc: '自然语言生成音色，仅 1.7B 提供' },
];

type SectionId =
  | 'mode'
  | 'cloud'
  | 'tunnel'
  | 'asr'
  | 'warmup'
  | 'engines'
  | 'local'
  | 'qc'
  | 'text'
  | 'post'
  | 'defaults'
  | 'license';

/**
 * 分区清单：锚点导航 + 搜索索引共用一份（labels 只放「分区标题 + 字段名」，
 * 不把 hint/值也塞进来，避免搜「1」匹配到所有数字）。
 */
const SECTIONS: { id: SectionId; label: string; icon: LucideIcon; labels: string[] }[] = [
  {
    id: 'mode',
    label: '运行模式',
    icon: ServerCog,
    labels: ['运行模式', '混合模式长文本阈值', '混合模式 ASR 优先位置'],
  },
  {
    id: 'cloud',
    label: '云端连接',
    icon: Zap,
    labels: [
      '云端连接',
      '服务地址',
      'API 密钥',
      '校验 TLS 证书',
      '空闲自动停机',
      '空闲时长（分钟）',
      '云端 Base 模型',
      '云端 CustomVoice 模型',
      '云端 VoiceDesign 模型',
      '测试连接',
    ],
  },
  {
    id: 'tunnel',
    label: 'SSH 隧道',
    icon: PlugZap,
    labels: ['SSH 隧道', '启用隧道', 'SSH 主机', 'SSH 端口', '用户名', '私钥路径', '密码', '本地映射端口', '远端地址 / 端口'],
  },
  {
    id: 'asr',
    label: '语音转文字',
    icon: Database,
    labels: [
      '语音转文字',
      'ASR 引擎',
      '本地模型规模',
      '计算精度',
      '区分说话人',
      '说话人识别位置',
      '云端声纹',
      '最少说话人',
      '最多说话人',
      '声纹',
    ],
  },
  {
    id: 'warmup',
    label: '模型预热',
    icon: Cpu,
    labels: [
      '模型预热',
      '语音生成',
      '声音克隆',
      '音色设计',
      '预热选中',
      '卸载选中',
      '全部卸载',
      '启动时自动预热',
      '启动预热哪些模型',
    ],
  },
  { id: 'engines', label: '引擎与模型', icon: Cpu, labels: ['引擎与模型', '引擎', '模型', '安装', '预览', '切换', '依赖'] },
  {
    id: 'local',
    label: '本地模型',
    icon: Database,
    labels: [
      '本地模型',
      '推理设备',
      '保持模型常驻',
      '显存不足时 CPU offload',
      '允许 0.6B 回退',
      '空闲卸载时长（秒）',
      '数值精度',
      '允许显存超额',
      '显存预留（GB）',
    ],
  },
  { id: 'qc', label: '内容质检', icon: BadgeCheck, labels: ['内容质检', '自动质检', '相似度阈值'] },
  { id: 'text', label: '文本处理', icon: Sparkles, labels: ['文本处理', '文本规范化', '规范化语言'] },
  {
    id: 'post',
    label: '后期处理',
    icon: Sparkles,
    labels: ['后期处理', '低切', '高架', '压缩', '峰值归一化'],
  },
  {
    id: 'defaults',
    label: '默认参数',
    icon: ServerCog,
    labels: ['默认参数', '语速', '音高（半音）', '温度', 'Top P', '采样步数', '输出格式'],
  },
  { id: 'license', label: '授权', icon: BadgeCheck, labels: ['授权', '许可', '数据位置', '隐私'] },
];

const ANCHOR_PREFIX = 'settings-';

/** 读 `#/settings?s=cloud`：状态页「去设置」直接落到对应分区 */
function readAnchorParam(): SectionId | null {
  const raw = window.location.hash.replace(/^#\/?/, '');
  const index = raw.indexOf('?');
  if (index < 0) return null;
  const id = new URLSearchParams(raw.slice(index + 1)).get('s');
  return SECTIONS.some((s) => s.id === id) ? (id as SectionId) : null;
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function scrollToSection(id: SectionId) {
  const node = document.getElementById(`${ANCHOR_PREFIX}${id}`);
  if (!node) return;
  node.scrollIntoView({ block: 'start', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
}

/* =========================== 字段级校验（只提示，不阻止自动保存） =========================== */

function urlIssue(raw: string, mode: RunMode): string | null {
  const value = (raw || '').trim();
  if (!value) return mode === 'local' ? '未填写（仅本地模式可留空）' : '必填：云端/混合模式需要服务地址';
  if (!/^https?:\/\//i.test(value)) return '必须以 http:// 或 https:// 开头';
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return '地址无法解析，示例：https://gpu.example.com:8443';
  }
  if (!url.hostname) return '缺少主机名';
  if (url.port && (Number(url.port) < 1 || Number(url.port) > 65535)) return '端口需在 1-65535 之间';
  if (!url.port && (url.protocol === 'https:' || url.protocol === 'http:')) {
    return `缺少端口（默认 ${url.protocol === 'https:' ? 443 : 80}）；本项目服务通常监听 8443`;
  }
  return null;
}

function rangeIssue(value: unknown, min: number, max: number, unit = ''): string | null {
  const num = Number(value);
  if (!Number.isFinite(num)) return '必须是数字';
  if (num < min || num > max) return `建议范围 ${min}–${max}${unit}`;
  return null;
}

function buildIssues(draft: any): Record<string, string> {
  const mode: RunMode = draft.run.mode;
  const issues: Record<string, string> = {};
  const put = (key: string, message: string | null) => {
    if (message) issues[key] = message;
  };
  put('cloud.base_url', urlIssue(draft.cloud.base_url, mode));
  if (!String(draft.cloud.api_key || '').trim() && mode !== 'local') {
    issues['cloud.api_key'] = '云端强制鉴权：留空时请求会被拒绝（401）';
  }
  put('cloud.idle_shutdown_minutes', rangeIssue(draft.cloud.idle_shutdown_minutes, 0, 240, ' 分钟'));
  put('run.hybrid_long_text_chars', rangeIssue(draft.run.hybrid_long_text_chars, 50, 5000, ' 字'));
  put('local.vram_reserve_gb', rangeIssue(draft.local.vram_reserve_gb ?? 0, 0, 8, ' GB'));
  put('local.unload_after_idle_seconds', rangeIssue(draft.local.unload_after_idle_seconds, 0, 7200, ' 秒'));
  put('qc.min_similarity', rangeIssue(draft.qc?.min_similarity ?? 0.75, 0.3, 1, ''));
  // 说话人上下限：越界或 min > max 都是硬冲突（这种状态下不落盘）
  const diarize = draft.asr?.diarize;
  if (diarize) {
    const min = Number(diarize.min_speakers ?? 1);
    const max = Number(diarize.max_speakers ?? 4);
    if (!Number.isFinite(min) || min < 1 || min > 8) put('asr.diarize.min_speakers', '最少说话人必须在 1-8 之间');
    else if (min > max) put('asr.diarize.min_speakers', `最少说话人（${min}）不能大于最多说话人（${max}）`);
  }
  return issues;
}

type ProbeState = { state: 'idle' | 'running' | 'ok' | 'fail'; message?: string; latency?: number };

function explainProbeError(error: unknown, base: string): string {
  const name = (error as Error)?.name;
  if (name === 'AbortError') return '探测超时（8 秒）：云端服务无响应或端口未开放';
  if (window.location.protocol === 'https:' && /^http:\/\//i.test(base)) {
    return '浏览器拦截了 HTTPS 页面里的 http:// 请求（混合内容），请改用 https:// 或 SSH 隧道';
  }
  return '请求未完成：隧道模式下浏览器无法自己走 SSH（只有后端能走），请用「一键接入云主机」或后端探活验证；直连模式失败通常是自签证书、CORS 或端口未开放';
}

export function SettingsPage() {
  const { settings, reloadSettings, health, pushToast, describeError } = useApp();
  const [draft, setDraft] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [preheating, setPreheating] = useState(false);
  const [warmupSelection, setWarmupSelection] = useState<string[]>(['custom_voice']);
  const [warmupReport, setWarmupReport] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [activeSection, setActiveSection] = useState<SectionId>('mode');
  const [probe, setProbe] = useState<ProbeState>({ state: 'idle' });
  const [confirmAction, setConfirmAction] = useState<'unload-all' | 'reset' | 'cloud-stop' | null>(null);
  const [cloudStopping, setCloudStopping] = useState(false);
  const [cloudStopResult, setCloudStopResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [sshPaste, setSshPaste] = useState('');
  const [tunnelFillMsg, setTunnelFillMsg] = useState('');
  const draftRef = useRef<any>(null);
  const dirtyRef = useRef(false);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const jumpedRef = useRef(false);

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  useEffect(() => {
    if (!settings) return;
    if (dirtyRef.current) return; // 有未保存改动时不要被服务端回包覆盖
    setDraft(JSON.parse(JSON.stringify(settings)));
    const kinds = settings.local.warmup_kinds;
    if (Array.isArray(kinds) && kinds.length) setWarmupSelection(kinds);
  }, [settings]);

  /* --------------------------- 搜索：分区标题 / 字段名 --------------------------- */
  const query = search.trim().toLowerCase();
  const matchedSections = useMemo(() => {
    if (!query) return null;
    const ids = new Set<SectionId>();
    SECTIONS.forEach((section) => {
      const haystack = [section.label, ...section.labels].map((text) => text.toLowerCase());
      if (haystack.some((text) => text.includes(query))) ids.add(section.id);
    });
    return ids;
  }, [query]);

  const isShown = useCallback(
    (id: SectionId) => !matchedSections || matchedSections.has(id),
    [matchedSections],
  );

  /** 把命中的关键字包成 <mark>，让「过滤」和「高亮」同时成立 */
  const hl = useCallback(
    (text: string): React.ReactNode => {
      if (!query) return text;
      const index = text.toLowerCase().indexOf(query);
      if (index < 0) return text;
      return (
        <>
          {text.slice(0, index)}
          <mark className="hl">{text.slice(index, index + query.length)}</mark>
          {text.slice(index + query.length)}
        </>
      );
    },
    [query],
  );

  const navItems = useMemo(
    () => SECTIONS.filter((section) => !matchedSections || matchedSections.has(section.id)),
    [matchedSections],
  );

  /* --------------------------- 滚动高亮（IntersectionObserver + 几何校正） --------------------------- */
  useEffect(() => {
    if (!draft) return;
    const nodes = SECTIONS.map((section) => document.getElementById(`${ANCHOR_PREFIX}${section.id}`)).filter(
      Boolean,
    ) as HTMLElement[];
    if (!nodes.length) return;

    /** 取「最后一个已越过顶部偏移的分区」；滚到底部则直接落到最后一个可见分区 */
    const compute = () => {
      const offset = 96;
      let current = SECTIONS[0].id;
      let lastVisible: SectionId | null = null;
      SECTIONS.forEach((section) => {
        const el = document.getElementById(`${ANCHOR_PREFIX}${section.id}`);
        if (!el || el.offsetParent === null) return; // 被搜索过滤掉的分区不参与
        lastVisible = section.id;
        if (el.getBoundingClientRect().top - offset <= 0) current = section.id;
      });
      const scroller = document.scrollingElement;
      const atBottom = scroller ? window.innerHeight + window.scrollY >= scroller.scrollHeight - 8 : false;
      if (atBottom && lastVisible) current = lastVisible;
      setActiveSection(current);
    };

    const observer = new IntersectionObserver(compute, { rootMargin: '-96px 0px -58% 0px', threshold: 0 });
    nodes.forEach((node) => observer.observe(node));

    // IntersectionObserver 只在「跨过阈值」时回调：同一屏内小幅滚动不会触发，故补一个 rAF 节流的滚动校正
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        compute();
      });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    compute();
    return () => {
      observer.disconnect();
      window.removeEventListener('scroll', onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [draft]);

  // 从状态页「去设置」跳进来：落到 `?s=` 指定的分区（只跳一次）
  useEffect(() => {
    if (!draft || jumpedRef.current) return;
    const target = readAnchorParam();
    if (!target) return;
    jumpedRef.current = true;
    // 二次校正：引擎卡片等异步内容会改变布局，80ms 时的滚动位置会漂移
    [80, 700].forEach((delay) =>
      window.setTimeout(() => {
        setActiveSection(target);
        scrollToSection(target);
      }, delay),
    );
  }, [draft]);

  const issues = useMemo(() => (draft ? buildIssues(draft) : {}), [draft]);
  const issueCount = Object.keys(issues).length;

  /* --------------------------- 预热 / 卸载 --------------------------- */
  const doPreload = async () => {
    if (!warmupSelection.length) return;
    setPreheating(true);
    setWarmupReport(null);
    try {
      const res = await api.preload(warmupSelection);
      const parts = Object.entries(res.detail).map(([kind, info]) => {
        const name = MODEL_KINDS.find((m) => m.id === kind)?.short || kind;
        if (info.status === 'ready') {
          return `${name}：已加载${info.degraded ? '（0.6B 回退）' : ''}${info.offload ? '（offload）' : ''}`;
        }
        if (info.status === 'evicted') return `${name}：被挤出显存`;
        return `${name}：${info.status}`;
      });
      const summary = `${parts.join('；')}。耗时 ${(res.elapsed_ms / 1000).toFixed(1)}s，显存 ${res.vram_allocated_mb} MB。`;
      setWarmupReport((res.note ? `${res.note} ` : '') + summary);
      pushToast({
        level: Object.values(res.detail).some((d) => d.status === 'ready') ? 'success' : 'error',
        title: '预热完成',
        description: (res.note || parts.join('；')).slice(0, 140),
      });
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    } finally {
      setPreheating(false);
    }
  };

  const doUnload = async (kinds: string[]) => {
    setPreheating(true);
    try {
      const res = await api.unload(kinds);
      // 全部卸载时同时关闭「启动时自动预热」，避免下次启动又被装回显存
      if (!kinds.length) {
        try {
          const next = await api.updateSettings({ local: { warmup_on_start: false } });
          dirtyRef.current = false;
          setDirty(false);
          setDraft(JSON.parse(JSON.stringify(next)));
          await reloadSettings();
        } catch {
          /* 忽略：卸载本身已成功 */
        }
      }
      setWarmupReport(
        `已卸载${kinds.length ? `：${kinds.join('、')}` : '全部模型（并已关闭启动自动预热）'}；当前已加载：${res.loaded.join('、') || '无'}，显存 ${0} MB`,
      );
      pushToast({
        level: 'success',
        title: '已释放显存',
        description: kinds.length ? `剩余：${res.loaded.join('、') || '无'}` : '已同时关闭「启动时自动预热」，重启后不会再自动占显存',
      });
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    } finally {
      setPreheating(false);
    }
  };

  const patch = (section: string, key: string, value: unknown) => {
    dirtyRef.current = true;
    setDirty(true);
    setDraft((prev: any) => ({ ...prev, [section]: { ...prev[section], [key]: value } }));
  };

  const patchDeep = (section: string, sub: string, key: string, value: unknown) => {
    dirtyRef.current = true;
    setDirty(true);
    setDraft((prev: any) => ({
      ...prev,
      [section]: { ...prev[section], [sub]: { ...prev[section][sub], [key]: value } },
    }));
  };

  const buildPayload = (d: any) => ({
    run: d.run,
    cloud: {
      base_url: d.cloud.base_url,
      api_key: d.cloud.api_key,
      verify_tls: d.cloud.verify_tls,
      models: d.cloud.models,
      tunnel: d.cloud.tunnel,
      idle_shutdown_enabled: d.cloud.idle_shutdown_enabled,
      idle_shutdown_minutes: d.cloud.idle_shutdown_minutes,
      request_timeout_s: d.cloud.request_timeout_s,
      chunk_timeout_s: d.cloud.chunk_timeout_s,
      retries: d.cloud.retries,
      retry_backoff_s: d.cloud.retry_backoff_s,
      readiness_timeout_s: d.cloud.readiness_timeout_s,
      idle_shutdown_url: d.cloud.idle_shutdown_url,
    },
    asr: { engine: d.asr.engine, local: d.asr.local, cloud: d.asr.cloud, diarize: d.asr.diarize },
    defaults: d.defaults,
    tts: d.tts,
    post: d.post,
    export: d.export,
    qc: d.qc,
    local: {
      keep_loaded: d.local.keep_loaded,
      warmup_on_start: d.local.warmup_on_start,
      warmup_kinds: d.local.warmup_kinds,
      unload_after_idle_seconds: d.local.unload_after_idle_seconds,
      allow_small_fallback: d.local.allow_small_fallback,
      low_vram: d.local.low_vram,
      device: d.local.device,
      dtype: d.local.dtype,
      vram_reserve_gb: d.local.vram_reserve_gb,
      allow_vram_overcommit: d.local.allow_vram_overcommit,
    },
    mock: d.mock,
  });

  const save = async (silent = false) => {
    const current = draftRef.current;
    if (!current) return;
    // 硬冲突（min_speakers > max_speakers）不落盘：否则后端会拒或存下自相矛盾的配置
    const conflict = buildIssues(current)['asr.diarize.min_speakers'];
    if (conflict) {
      if (!silent) pushToast({ level: 'warning', title: '设置未保存', description: conflict });
      return;
    }
    if (!silent) setSaving(true);
    try {
      const next = await api.updateSettings(buildPayload(current));
      dirtyRef.current = false;
      setDirty(false);
      setSavedAt(Date.now());
      await reloadSettings();
      if (!silent) {
        setDraft(JSON.parse(JSON.stringify(next)));
        pushToast({ level: 'success', title: '设置已保存并生效' });
      }
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    } finally {
      if (!silent) setSaving(false);
    }
  };

  // 改动后自动保存（避免切走页面丢掉修改）；min/max 冲突时先不保存，等改对再落盘
  useEffect(() => {
    if (!dirty) return;
    if (issues['asr.diarize.min_speakers']) return;
    const timer = window.setTimeout(() => {
      void save(true);
    }, 700);
    return () => window.clearTimeout(timer);
  }, [dirty, draft, issues]);

  const switchMode = async (mode: RunMode) => {
    try {
      await api.switchMode(mode);
      await reloadSettings();
      pushToast({ level: 'success', title: `已切换到${mode === 'local' ? '仅本地' : mode === 'cloud' ? '仅云端' : '混合'}模式` });
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    }
  };

  const reset = async () => {
    try {
      const next = await api.resetSettings();
      setDraft(JSON.parse(JSON.stringify(next)));
      await reloadSettings();
      pushToast({ level: 'success', title: '已恢复配置文件默认值' });
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    }
  };

  /** 一键停止云端：后端经 SSH 杀掉云端推理进程（不销毁实例） */
  const doStopCloud = async (action: 'stop_service' | 'shutdown' = 'shutdown') => {
    setCloudStopping(true);
    setCloudStopResult(null);
    try {
      const res = await api.stopCloud({ stop_tunnel: true, action });
      setCloudStopResult({
        ok: true,
        text: `${res.message}（${res.host} · 端口${res.port_closed ? '已关闭' : '仍开放'}）`,
      });
      patchDeep('cloud', 'tunnel', 'enabled', false);
    } catch (err) {
      setCloudStopResult({ ok: false, text: (err as Error).message || '停止失败' });
    } finally {
      setCloudStopping(false);
    }
  };

  /** 纯前端只读探测：直接打 cloud.base_url + /v1/health（不改后端，不带证书覆盖） */
  const testConnection = async () => {
    const base = String(draft.cloud.base_url || '').trim().replace(/\/+$/, '');
    const urlError = urlIssue(base, draft.run.mode);
    if (urlError) {
      setProbe({ state: 'fail', message: `地址有误：${urlError}` });
      return;
    }
    setProbe({ state: 'running' });
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 8000);
    const started = performance.now();
    try {
      const response = await fetch(`${base}/v1/health`, {
        method: 'GET',
        headers: String(draft.cloud.api_key || '').trim() ? { 'X-API-Key': String(draft.cloud.api_key).trim() } : {},
        signal: controller.signal,
        cache: 'no-store',
      });
      const latency = Math.max(1, Math.round(performance.now() - started));
      if (response.ok) {
        setProbe({ state: 'ok', latency, message: '连接成功' });
        pushToast({ level: 'success', title: '云端连接成功', description: `延迟 ${latency} ms` });
      } else {
        setProbe({
          state: 'fail',
          latency,
          message: `服务可达但返回 HTTP ${response.status}${response.status === 401 || response.status === 403 ? '（API 密钥不正确）' : ''}`,
        });
      }
    } catch (error) {
      setProbe({ state: 'fail', message: explainProbeError(error, base) });
    } finally {
      window.clearTimeout(timer);
    }
  };

  // 只在「还没有草稿」时占位：自动保存后的 reloadSettings 不能让整页闪回加载态
  // （否则引擎卡片、预热勾选等子状态会被反复卸载重建）
  if (!draft) {
    return (
      <div className="page">
        <h1>设置</h1>
        <SectionCard title="加载中">
          <Spinner />
        </SectionCard>
      </div>
    );
  }

  const mode = draft.run.mode as RunMode;
  const loadedKinds = Object.keys(health?.local?.loaded_models || {});

  return (
    <div className="page settings-page">
      <div className="page__head">
        <div>
          <h1>设置</h1>
          <p className="page__subtitle">
            运行模式、云端连接、隧道、模型与参数默认值；保存后立即生效并写入 data/runtime.json
          </p>
        </div>
        <div className="toolbar">
          <button type="button" className="btn btn--sm" onClick={() => void reloadSettings()}>
            <RefreshCw size={14} strokeWidth={1.75} /> 重新读取
          </button>
          <button type="button" className="btn btn--sm btn--danger" onClick={() => setConfirmAction('reset')}>
            <Trash2 size={14} strokeWidth={1.75} /> 恢复默认
          </button>
          <button type="button" className="btn btn--primary btn--sm" onClick={() => void save()} disabled={saving}>
            {saving ? <Spinner size={14} /> : <Save size={14} strokeWidth={1.75} />} 保存
          </button>
        </div>
      </div>

      <div className="settings-body">
        <nav className="settings-nav" aria-label="设置分区">
          <div className="settings-nav__search">
            <Search size={14} strokeWidth={1.75} aria-hidden="true" />
            <input
              className="input"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索设置项，如「质检」"
              aria-label="搜索设置项"
            />
            {search ? (
              <button
                type="button"
                className="settings-nav__clear"
                onClick={() => setSearch('')}
                aria-label="清除搜索"
                title="清除搜索"
              >
                <X size={13} strokeWidth={2} />
              </button>
            ) : null}
          </div>
          <p className="settings-nav__status" aria-live="polite">
            {query ? (
              <>
                命中 <span className="num">{navItems.length}</span> / {SECTIONS.length} 个分区
              </>
            ) : (
              <>
                共 <span className="num">{SECTIONS.length}</span> 个分区
              </>
            )}
            {issueCount ? (
              <>
                {' · '}
                <span className="settings-nav__issues">
                  <StatusDot tone="warn" /> {issueCount} 项待确认
                </span>
              </>
            ) : null}
          </p>
          <ul className="settings-nav__list">
            {navItems.map((section) => {
              const Icon = section.icon;
              return (
                <li key={section.id}>
                  <button
                    type="button"
                    className="settings-nav__item"
                    aria-current={activeSection === section.id}
                    onClick={() => {
                      setActiveSection(section.id);
                      scrollToSection(section.id);
                    }}
                  >
                    <Icon size={14} strokeWidth={1.75} aria-hidden="true" />
                    <span>{hl(section.label)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          <select
            className="select settings-nav__select"
            value={activeSection}
            aria-label="跳转到设置分区"
            onChange={(event) => {
              const id = event.target.value as SectionId;
              setActiveSection(id);
              scrollToSection(id);
            }}
          >
            {navItems.map((section) => (
              <option key={section.id} value={section.id}>
                {section.label}
              </option>
            ))}
          </select>
        </nav>

        <div className="settings-content" ref={contentRef}>
          <InlineNote kind="info">
            配置文件：<code>{draft.config_file}</code>。界面修改会写入运行期覆盖文件，优先级高于配置文件；密钥在界面中始终脱敏显示。
            <div style={{ marginTop: 'var(--space-2)' }}>
              {dirty ? '有修改，正在自动保存…' : savedAt ? `已自动保存（${new Date(savedAt).toLocaleTimeString()}），切页不会丢失` : '修改后约 0.7 秒自动保存'}
            </div>
          </InlineNote>

          {matchedSections && navItems.length === 0 ? (
            <div className="settings-empty" role="status">
              <p>
                没有匹配「{search.trim()}」的设置项。
              </p>
              <div className="toolbar">
                <button type="button" className="btn btn--sm" onClick={() => setSearch('')}>
                  清除搜索
                </button>
                <span className="hint">试试「质检」「隧道」「显存」这类关键词。</span>
              </div>
            </div>
          ) : null}

          <SectionCard
            id={`${ANCHOR_PREFIX}mode`}
            title={hl('运行模式')}
            icon={<ServerCog size={15} strokeWidth={1.75} />}
            style={isShown('mode') ? undefined : { display: 'none' }}
          >
            <div className="segmented" role="tablist" aria-label="运行模式">
              {[
                { id: 'local', label: '仅本地', desc: '全部任务使用本地 GPU' },
                { id: 'cloud', label: '仅云端', desc: '全部任务使用云端 GPU' },
                { id: 'hybrid', label: '混合', desc: '按任务与显存自动选址' },
              ].map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  aria-selected={draft.run.mode === item.id}
                  className="segmented__item"
                  onClick={() => {
                    patch('run', 'mode', item.id);
                    void switchMode(item.id as RunMode);
                  }}
                  title={item.desc}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <p className="hint">
              当前生效模式：
              <strong>
                {health?.mode === 'hybrid'
                  ? '混合'
                  : health?.mode === 'cloud'
                    ? '仅云端'
                    : health?.mode === 'local'
                      ? '仅本地'
                      : health?.mode === 'mock'
                        ? '模拟引擎（联调）'
                        : health?.mode || draft.run.mode}
              </strong>
              ，活动后端：
              <strong>{health?.active_backend === 'cloud' ? '云端' : health?.active_backend === 'local' ? '本地' : health?.active_backend === 'mock' ? '模拟' : health?.active_backend || '—'}</strong>
            </p>
            <div className="field-row">
              <Field label={hl('混合模式长文本阈值')} hint="超过该字数的克隆任务优先云端">
                <input
                  className="input num"
                  type="number"
                  min={50}
                  max={5000}
                  value={draft.run.hybrid_long_text_chars}
                  aria-invalid={issues['run.hybrid_long_text_chars'] ? true : undefined}
                  onChange={(e) => patch('run', 'hybrid_long_text_chars', Number(e.target.value))}
                />
                {issues['run.hybrid_long_text_chars'] ? (
                  <p className="field-error">{issues['run.hybrid_long_text_chars']}</p>
                ) : null}
              </Field>
              <Field label={hl('混合模式 ASR 优先位置')}>
                <select
                  className="select"
                  value={draft.run.asr_prefer}
                  onChange={(e) => patch('run', 'asr_prefer', e.target.value)}
                >
                  <option value="local">本地优先</option>
                  <option value="cloud">云端优先</option>
                </select>
              </Field>
            </div>
          </SectionCard>

          <SectionCard
            id={`${ANCHOR_PREFIX}cloud`}
            title={hl('云端连接')}
            icon={<Zap size={15} strokeWidth={1.75} />}
            style={isShown('cloud') ? undefined : { display: 'none' }}
          >
            <div className="field-row">
              <Field label={hl('服务地址')} hint="支持 https://host:port">
                <input
                  className="input"
                  value={draft.cloud.base_url}
                  onChange={(e) => patch('cloud', 'base_url', e.target.value)}
                  placeholder="https://your-cloud-gpu-host:8443"
                  aria-invalid={issues['cloud.base_url'] ? true : undefined}
                  aria-describedby="cloud-base-url-error"
                />
                {issues['cloud.base_url'] ? (
                  <p className="field-error" id="cloud-base-url-error">
                    {issues['cloud.base_url']}
                  </p>
                ) : null}
              </Field>
              <Field label={hl('API 密钥')} hint="云端强制鉴权，留空则沿用原值">
                <input
                  className="input"
                  type="password"
                  value={draft.cloud.api_key}
                  onChange={(e) => patch('cloud', 'api_key', e.target.value)}
                  placeholder="X-API-Key"
                  aria-invalid={issues['cloud.api_key'] ? true : undefined}
                />
                {issues['cloud.api_key'] ? <p className="field-error">{issues['cloud.api_key']}</p> : null}
              </Field>
            </div>

            {draft.cloud.tunnel.enabled && draft.cloud.tunnel.ssh_host ? (
              <p className="hint" style={{ marginTop: 4 }}>
                隧道已启用：实际请求走 <code className="num">https://127.0.0.1:{String(draft.cloud.tunnel.local_port || 18443)}</code>
                ，上面这个「服务地址」只用来决定 http/https 协议
              </p>
            ) : null}

            <div className="probe-bar">
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => void testConnection()}
                disabled={probe.state === 'running'}
                title="浏览器直接请求 {服务地址}/v1/health，只读探测，不修改任何配置"
              >
                <PlugZap size={14} strokeWidth={1.75} /> {probe.state === 'running' ? '探测中…' : '测试连接'}
              </button>
              <button
                type="button"
                className="btn btn--sm btn--danger"
                onClick={() => setConfirmAction('cloud-stop')}
                disabled={cloudStopping}
                title="通过 SSH 让云端实例关机（AutoDL 等平台会停止计费）；也可在弹窗里选「仅停止服务」"
              >
                {cloudStopping ? <Spinner size={12} /> : <Power size={14} strokeWidth={1.75} />}
                {cloudStopping ? '处理中…' : '一键关机'}
              </button>
              {cloudStopResult ? (
                <span className="probe-result" data-state={cloudStopResult.ok ? 'ok' : 'fail'} role="status">
                  <StatusDot tone={cloudStopResult.ok ? 'ok' : 'danger'} /> {cloudStopResult.text}
                </span>
              ) : null}
              <span className="probe-result" data-state={probe.state} role="status" aria-live="polite">
                {probe.state === 'running' ? (
                  <>
                    <Spinner size={12} /> 正在探测 {String(draft.cloud.base_url || '').trim()}/v1/health
                  </>
                ) : probe.state === 'ok' ? (
                  <>
                    <StatusDot tone="ok" /> 连接成功（延迟 <span className="num">{probe.latency}</span> ms）
                  </>
                ) : probe.state === 'fail' ? (
                  <>
                    <StatusDot tone="danger" /> {probe.message}
                  </>
                ) : (
                  <span className="hint">点「测试连接」会用浏览器直连 /v1/health（跳过代理，不影响正在运行的任务）</span>
                )}
              </span>
            </div>

            <CloudAgentWizard />

            <div className="field-row">
              <Field label={hl('校验 TLS 证书')} hint={draft.cloud.verify_tls ? undefined : '浏览器不受该开关影响：自签证书会直接握手失败'}>
                <select
                  className="select"
                  value={String(draft.cloud.verify_tls)}
                  onChange={(e) => patch('cloud', 'verify_tls', e.target.value === 'true')}
                >
                  <option value="true">校验（生产推荐）</option>
                  <option value="false">不校验（自签证书调试）</option>
                </select>
              </Field>
              <Field label={hl('空闲自动停机')} hint="默认关闭；开启后仅云端/混合模式生效，用于节省云端费用">
                <select
                  className="select"
                  value={String(draft.cloud.idle_shutdown_enabled)}
                  onChange={(e) => patch('cloud', 'idle_shutdown_enabled', e.target.value === 'true')}
                >
                  <option value="false">关闭（默认）</option>
                  <option value="true">开启</option>
                </select>
                <p className={draft.cloud.idle_shutdown_enabled ? 'field-note field-note--warn' : 'field-note'}>
                  {draft.cloud.idle_shutdown_enabled
                    ? `后果：空闲超过 ${draft.cloud.idle_shutdown_minutes} 分钟后云端实例会被停机，下一次提交任务需要等它重新启动（首包变慢）。`
                    : '后果：云端实例会一直保持开机，持续计费。'}
                </p>
              </Field>
              <Field label={hl('空闲时长（分钟）')} hint="0 表示不自动停机">
                <input
                  className="input num"
                  type="number"
                  min={0}
                  max={240}
                  value={draft.cloud.idle_shutdown_minutes}
                  aria-invalid={issues['cloud.idle_shutdown_minutes'] ? true : undefined}
                  onChange={(e) => patch('cloud', 'idle_shutdown_minutes', Number(e.target.value))}
                />
                {issues['cloud.idle_shutdown_minutes'] ? (
                  <p className="field-error">{issues['cloud.idle_shutdown_minutes']}</p>
                ) : null}
              </Field>
            </div>
            <div className="field-row">
              <Field label={hl('云端 Base 模型')}>
                <input
                  className="input"
                  value={draft.cloud.models.base}
                  onChange={(e) => patchDeep('cloud', 'models', 'base', e.target.value)}
                />
              </Field>
              <Field label={hl('云端 CustomVoice 模型')}>
                <input
                  className="input"
                  value={draft.cloud.models.custom_voice}
                  onChange={(e) => patchDeep('cloud', 'models', 'custom_voice', e.target.value)}
                />
              </Field>
              <Field label={hl('云端 VoiceDesign 模型')}>
                <input
                  className="input"
                  value={draft.cloud.models.voice_design}
                  onChange={(e) => patchDeep('cloud', 'models', 'voice_design', e.target.value)}
                />
              </Field>
            </div>
          </SectionCard>

          <SectionCard
            id={`${ANCHOR_PREFIX}tunnel`}
            title={hl('SSH 隧道')}
            hint="把云端服务映射到本地端口，客户端无感知"
            style={isShown('tunnel') ? undefined : { display: 'none' }}
          >
            <div className="probe-bar" style={{ marginBottom: 6, alignItems: 'flex-end' }}>
              <Field label="粘贴登录指令" hint="从云控制台复制整行，例如 ssh -p 13485 root@connect.cqa1.seetacloud.com">
                <input
                  className="input"
                  value={sshPaste}
                  onChange={(e) => setSshPaste(e.target.value)}
                  placeholder="ssh -p 13485 root@connect.cqa1.seetacloud.com"
                />
              </Field>
              <Field label="SSH 密码" hint="用私钥则留空、在下方填私钥路径">
                <input
                  className="input"
                  type="password"
                  value={draft.cloud.tunnel.password}
                  onChange={(e) => patchDeep('cloud', 'tunnel', 'password', e.target.value)}
                  placeholder="留空则用私钥"
                />
              </Field>
              <button
                type="button"
                className="btn btn--sm btn--primary"
                onClick={() => {
                  const parsed = parseSshCommand(sshPaste);
                  if (!parsed.host) {
                    setTunnelFillMsg('没识别出主机，请贴完整指令，如 ssh -p 13485 root@host');
                    return;
                  }
                  const url = String(draft.cloud.base_url || '');
                  const isLoopback = /^(https?):\/\/(127\.0\.0\.1|localhost)/i.test(url);
                  const portMatch = url.match(/:(\d{2,5})/);
                  const localPort = Number(draft.cloud.tunnel.local_port) || 18443;
                  let remotePort = Number(draft.cloud.tunnel.remote_port) || 0;
                  // 只有服务地址不是本地回环时，才从它取端口（回环地址的端口就是本地映射端口，不能反填）
                  if (!isLoopback && portMatch) remotePort = Number(portMatch[1]);
                  if (!remotePort || remotePort === localPort) remotePort = 8443;
                  patchDeep('cloud', 'tunnel', 'ssh_host', parsed.host);
                  patchDeep('cloud', 'tunnel', 'ssh_port', parsed.port);
                  if (parsed.username) patchDeep('cloud', 'tunnel', 'username', parsed.username);
                  if (parsed.key_path) patchDeep('cloud', 'tunnel', 'key_path', parsed.key_path);
                  patchDeep('cloud', 'tunnel', 'remote_host', '127.0.0.1');
                  patchDeep('cloud', 'tunnel', 'remote_port', remotePort);
                  patchDeep('cloud', 'tunnel', 'local_port', localPort);
                  patchDeep('cloud', 'tunnel', 'enabled', true);
                  patch('cloud', 'base_url', `https://127.0.0.1:${localPort}`);
                  if (parsed.password) patchDeep('cloud', 'tunnel', 'password', parsed.password);
                  setTunnelFillMsg(
                    '已填入：' + parsed.username + '@' + parsed.host + ':' + parsed.port
                    + (parsed.password ? '（密码已带入）' : parsed.key_path ? '（私钥已带入）' : '（请补密码或私钥）'),
                  );
                }}
              >
                一键启用隧道
              </button>
              {tunnelFillMsg ? <span className="hint">{tunnelFillMsg}</span> : null}
            </div>

            <p className="hint" style={{ marginBottom: 10 }}>
              会自动填成：本地 <code className="num">{String(draft.cloud.tunnel.local_port || 18443)}</code> →
              云端 <code className="num">{String(draft.cloud.tunnel.remote_host || '127.0.0.1')}:{String(draft.cloud.tunnel.remote_port || 8443)}</code>
              （经 <code>{draft.cloud.tunnel.username || 'user'}@{draft.cloud.tunnel.ssh_host || '主机'}:{String(draft.cloud.tunnel.ssh_port || 22)}</code>）；
              02 的服务地址 <code>{String(draft.cloud.base_url || '未填')}</code> 只用来决定 http/https，实际请求走本地映射端口。
            </p>
            <div className="field-row">
              <Field label={hl('启用隧道')}>
                <select
                  className="select"
                  value={String(draft.cloud.tunnel.enabled)}
                  onChange={(e) => patchDeep('cloud', 'tunnel', 'enabled', e.target.value === 'true')}
                >
                  <option value="false">关闭</option>
                  <option value="true">开启</option>
                </select>
              </Field>
              <Field label={hl('SSH 主机')}>
                <input
                  className="input"
                  value={draft.cloud.tunnel.ssh_host}
                  onChange={(e) => patchDeep('cloud', 'tunnel', 'ssh_host', e.target.value)}
                />
              </Field>
              <Field label={hl('SSH 端口')}>
                <input
                  className="input num"
                  type="number"
                  value={draft.cloud.tunnel.ssh_port}
                  onChange={(e) => patchDeep('cloud', 'tunnel', 'ssh_port', Number(e.target.value))}
                />
              </Field>
              <Field label={hl('用户名')}>
                <input
                  className="input"
                  value={draft.cloud.tunnel.username}
                  onChange={(e) => patchDeep('cloud', 'tunnel', 'username', e.target.value)}
                />
              </Field>
            </div>
            <div className="field-row">
              <Field label={hl('私钥路径')} hint="留空则用密码登录">
                <input
                  className="input"
                  value={draft.cloud.tunnel.key_path}
                  onChange={(e) => patchDeep('cloud', 'tunnel', 'key_path', e.target.value)}
                />
              </Field>
              <Field label={hl('密码')} hint="DPAPI 加密后保存（runtime.json 里是密文）">
                <input
                  className="input"
                  type="password"
                  value={draft.cloud.tunnel.password}
                  onChange={(e) => patchDeep('cloud', 'tunnel', 'password', e.target.value)}
                />
              </Field>
              <Field label={hl('本地映射端口')}>
                <input
                  className="input num"
                  type="number"
                  value={draft.cloud.tunnel.local_port}
                  onChange={(e) => patchDeep('cloud', 'tunnel', 'local_port', Number(e.target.value))}
                />
              </Field>
              <Field label={hl('远端地址 / 端口')}>
                <div className="toolbar">
                  <input
                    className="input"
                    value={draft.cloud.tunnel.remote_host}
                    onChange={(e) => patchDeep('cloud', 'tunnel', 'remote_host', e.target.value)}
                  />
                  <input
                    className="input num"
                    style={{ maxWidth: 90 }}
                    type="number"
                    value={draft.cloud.tunnel.remote_port}
                    onChange={(e) => patchDeep('cloud', 'tunnel', 'remote_port', Number(e.target.value))}
                  />
                </div>
              </Field>
            </div>
            {health?.tunnel?.enabled ? (
              <InlineNote kind={health.tunnel.running ? 'info' : 'warning'}>
                隧道状态（上次检查）：{health.tunnel.running ? `运行中（本地 ${health.tunnel.local_port}）` : `未运行：${health.tunnel.error || '等待连接'}`}
            {!health.tunnel.running ? ' —— 参数改完后保存并重启后端（或点「一键接入云主机」）会重新建立隧道' : ''}
              </InlineNote>
            ) : null}
          </SectionCard>

          <SectionCard
            id={`${ANCHOR_PREFIX}asr`}
            title={hl('语音转文字')}
            icon={<Database size={15} strokeWidth={1.75} />}
            style={isShown('asr') ? undefined : { display: 'none' }}
          >
            <div className="field-row">
              <Field label={hl('ASR 引擎')}>
                <select className="select" value={draft.asr.engine} onChange={(e) => patch('asr', 'engine', e.target.value)}>
                  <option value="local">本地（faster-whisper）</option>
                  <option value="cloud">云端接口</option>
                </select>
              </Field>
              <Field label={hl('本地模型规模')} hint="改由「引擎与模型 → 语音识别」统一安装与切换">
                <div className="probe-bar" style={{ gap: 8 }}>
                  <span className="hint">当前：{draft.asr.local.engine_id || draft.asr.local.model_size || '未选择'}</span>
                  <button type="button" className="btn btn--sm" onClick={() => scrollToSection('engines')}>
                    去引擎管理
                  </button>
                </div>
              </Field>
              <Field label={hl('区分说话人')} hint="转录时给每句标注说话人；需先安装「CAM++ 声纹」">
                <select
                  className="select"
                  value={String(draft.asr.diarize?.enabled ?? false)}
                  onChange={(e) => patchDeep('asr', 'diarize', 'enabled', e.target.value === 'true')}
                >
                  <option value="false">关闭（默认）</option>
                  <option value="true">开启</option>
                </select>
              </Field>
              <Field
                label={hl('说话人识别位置')}
                hint="声纹由谁来做：本地 CAM++ 不依赖网络、云端不占显存；自动 = 云端支持就用云端"
              >
                <select
                  className="select"
                  value={String(draft.asr.diarize?.prefer ?? 'local')}
                  onChange={(e) => patchDeep('asr', 'diarize', 'prefer', e.target.value)}
                  aria-label="说话人识别位置 prefer"
                >
                  <option value="local">本地（CAM++，默认）</option>
                  <option value="cloud">云端（远端声纹服务）</option>
                  <option value="auto">自动（云端优先，不支持回落本地）</option>
                </select>
              </Field>
              <Field label={hl('最少说话人')} hint="1-8，须 ≤ 最多说话人；不足下限时会强制拆分补足">
                <input
                  id="asr-min-speakers"
                  className="input num"
                  type="number"
                  min={1}
                  max={8}
                  value={draft.asr.diarize?.min_speakers ?? 1}
                  onChange={(e) => patchDeep('asr', 'diarize', 'min_speakers', Number(e.target.value))}
                  aria-label="最少说话人 min_speakers"
                  aria-invalid={issues['asr.diarize.min_speakers'] ? true : undefined}
                />
                {issues['asr.diarize.min_speakers'] ? (
                  <span className="field-error">{issues['asr.diarize.min_speakers']}</span>
                ) : null}
              </Field>
              <Field label={hl('最多说话人')} hint="聚类上限（2-8），3-4 人会议最稳；识别出的簇超过上限会合并最相似的">
                <select
                  className="select"
                  value={String(draft.asr.diarize?.max_speakers ?? 4)}
                  onChange={(e) => patchDeep('asr', 'diarize', 'max_speakers', Number(e.target.value))}
                  aria-label="最多说话人 max_speakers"
                >
                  {[2, 3, 4, 5, 6, 7, 8].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={hl('计算精度')}>
                <select
                  className="select"
                  value={draft.asr.local.compute_type}
                  onChange={(e) => patchDeep('asr', 'local', 'compute_type', e.target.value)}
                >
                  {['int8', 'float16', 'int8_float16', 'float32'].map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </SectionCard>

          <SectionCard
            id={`${ANCHOR_PREFIX}warmup`}
            title={hl('模型预热')}
            hint="仅 Qwen3-TTS 引擎生效；选择要提前加载到显存/内存的模型，首个请求就快"
            icon={<Cpu size={15} strokeWidth={1.75} />}
            style={isShown('warmup') ? undefined : { display: 'none' }}
          >
            <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
              {MODEL_KINDS.map((item) => {
                const loadedInfo = health?.local?.loaded_models?.[item.id];
                const checked = warmupSelection.includes(item.id);
                return (
                  <label
                    key={item.id}
                    className="nested"
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 'var(--space-3)',
                      cursor: 'pointer',
                      borderColor: checked ? 'var(--color-primary)' : undefined,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) =>
                        setWarmupSelection((prev) =>
                          e.target.checked ? [...prev, item.id] : prev.filter((k) => k !== item.id),
                        )
                      }
                      aria-label={`选择预热 ${item.label}`}
                    />
                    <span style={{ display: 'grid', gap: 2, flex: 1 }}>
                      <strong style={{ fontSize: 'var(--fs-13)' }}>{hl(item.short)}</strong>
                      <span className="hint">{item.desc}</span>
                    </span>
                    {loadedInfo ? (
                      <span className="badge badge--success">
                        已加载{loadedInfo.degraded ? ' · 0.6B 回退' : ''}
                        {loadedInfo.offload ? ' · offload' : ''}
                      </span>
                    ) : (
                      <span className="badge badge--muted">未加载</span>
                    )}
                  </label>
                );
              })}
            </div>
            <div className="toolbar">
              <button
                type="button"
                className="btn btn--primary btn--sm"
                onClick={() => void doPreload()}
                disabled={preheating || warmupSelection.length === 0}
              >
                {preheating ? <Spinner size={13} /> : <Zap size={13} strokeWidth={1.75} />}
                预热选中（{warmupSelection.length}）
              </button>
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => void doUnload(warmupSelection)}
                disabled={preheating || warmupSelection.length === 0}
              >
                <Trash2 size={13} strokeWidth={1.75} /> 卸载选中
              </button>
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => setConfirmAction('unload-all')}
                disabled={preheating}
              >
                <Trash2 size={13} strokeWidth={1.75} /> 全部卸载（释放显存）
              </button>
              <span className="hint">
                已加载：{loadedKinds.join('、') || '无'} · 显存{' '}
                <span className="num">{health?.local?.vram_allocated_mb ?? 0}</span> MB
              </span>
            </div>
            {warmupReport ? <InlineNote kind="info">{warmupReport}</InlineNote> : null}
            <div className="field-row">
              <Field label={hl('启动时自动预热')} hint="保存后下次启动生效">
                <select
                  className="select"
                  value={String(draft.local.warmup_on_start ?? true)}
                  onChange={(e) => patch('local', 'warmup_on_start', e.target.value === 'true')}
                >
                  <option value="true">开启</option>
                  <option value="false">关闭</option>
                </select>
              </Field>
              <Field label={hl('启动预热哪些模型')} hint="默认只预热语音生成模型；全部预热会多占显存">
                <div className="toolbar" style={{ gap: 'var(--space-1)' }}>
                  {MODEL_KINDS.map((item) => {
                    const list: string[] = draft.local.warmup_kinds || [];
                    const on = list.includes(item.id);
                    return (
                      <button
                        key={item.id}
                        type="button"
                        className="chip chip--button"
                        aria-pressed={on}
                        onClick={() =>
                          patch(
                            'local',
                            'warmup_kinds',
                            on ? list.filter((k) => k !== item.id) : [...list, item.id],
                          )
                        }
                      >
                        {item.short}
                      </button>
                    );
                  })}
                </div>
              </Field>
            </div>
          </SectionCard>

          <div data-visible={isShown('engines') ? 'true' : 'false'} className="settings-engine-slot">
            <EngineSection id={`${ANCHOR_PREFIX}engines`} />
          </div>

          <SectionCard
            id={`${ANCHOR_PREFIX}local`}
            title={hl('本地模型')}
            hint="路径只读，修改请编辑 config/config.yaml"
            style={isShown('local') ? undefined : { display: 'none' }}
          >
            <InlineNote kind="info">
              以下路径<b>只对 Qwen3-TTS（1.7B / 0.6B）有效</b>。其它引擎（CosyVoice / VoxCPM / IndexTTS）
              使用各自的模型目录，见上方「引擎与模型」卡片里的路径与体积。
            </InlineNote>
            <div className="kv">
              {Object.entries(draft.local.models).map(([key, value]) => (
                <div className="kv__item" key={key}>
                  <span className="kv__label">{key}</span>
                  <span className="num" style={{ fontSize: 'var(--fs-12)', wordBreak: 'break-all' }}>
                    {String(value)}
                  </span>
                </div>
              ))}
            </div>
            <div className="field-row">
              <Field label={hl('推理设备')} hint="1.7B 需 ≥4.5GB 显存；显存不足可先用 CPU（慢约 25 倍）">
                <select
                  className="select"
                  value={draft.local.device}
                  onChange={(e) => patch('local', 'device', e.target.value)}
                >
                  <option value="cuda:0">GPU（cuda:0）</option>
                  <option value="cpu">CPU（1.7B 也能跑，较慢）</option>
                </select>
              </Field>
              <Field label={hl('保持模型常驻')} hint="关闭则每次空闲后卸载">
                <select
                  className="select"
                  value={String(draft.local.keep_loaded)}
                  onChange={(e) => patch('local', 'keep_loaded', e.target.value === 'true')}
                >
                  <option value="true">常驻（推荐）</option>
                  <option value="false">空闲卸载</option>
                </select>
              </Field>
              <Field label={hl('显存不足时 CPU offload')} hint="实验特性：目前只能加载、无法生成，建议保持关闭">
                <select
                  className="select"
                  value={String(draft.local.low_vram?.enabled ?? false)}
                  onChange={(e) => patchDeep('local', 'low_vram', 'enabled', e.target.value === 'true')}
                >
                  <option value="false">关闭（推荐）</option>
                  <option value="true">开启（实验）</option>
                </select>
              </Field>
              <Field
                label={hl('允许 0.6B 回退')}
                hint="仅 Qwen3-TTS 生效；默认关闭，开启后 1.7B 显存不足会显式降级到 0.6B 并在任务中标注"
              >
                <select
                  className="select"
                  value={String(draft.local.allow_small_fallback)}
                  onChange={(e) => patch('local', 'allow_small_fallback', e.target.value === 'true')}
                >
                  <option value="false">禁止（推荐：直接换 0.6B 引擎或在引擎卡片里切换）</option>
                  <option value="true">允许（自动回退）</option>
                </select>
              </Field>
              <Field label={hl('空闲卸载时长（秒）')}>
                <input
                  className="input num"
                  type="number"
                  min={0}
                  max={7200}
                  value={draft.local.unload_after_idle_seconds}
                  aria-invalid={issues['local.unload_after_idle_seconds'] ? true : undefined}
                  onChange={(e) => patch('local', 'unload_after_idle_seconds', Number(e.target.value))}
                />
                {issues['local.unload_after_idle_seconds'] ? (
                  <p className="field-error">{issues['local.unload_after_idle_seconds']}</p>
                ) : null}
              </Field>
              <Field label={hl('数值精度')} hint="fp16 更省显存（4GB 卡跑 1.7B 用这个）；采样异常时换 bf16">
                <select
                  className="select"
                  value={draft.local.dtype}
                  onChange={(e) => patch('local', 'dtype', e.target.value)}
                >
                  <option value="float16">float16（推荐，4GB 可跑 1.7B）</option>
                  <option value="bfloat16">bfloat16（数值更稳，略慢）</option>
                  <option value="float32">float32（最稳，最占显存）</option>
                </select>
              </Field>
              <Field label={hl('允许显存超额')} hint="Windows 可回落到共享内存；4GB 卡整模型加载 1.7B 需要">
                <select
                  className="select"
                  value={String(draft.local.allow_vram_overcommit ?? true)}
                  onChange={(e) => patch('local', 'allow_vram_overcommit', e.target.value === 'true')}
                >
                  <option value="true">开启（推荐）</option>
                  <option value="false">关闭（严格按空闲显存判定）</option>
                </select>
              </Field>
              <Field label={hl('显存预留（GB）')} hint="留给系统/桌面/其它进程的显存，判定是否可加载时先扣掉">
                <input
                  className="input num"
                  type="number"
                  min={0}
                  max={8}
                  step={0.5}
                  value={draft.local.vram_reserve_gb ?? 0}
                  aria-invalid={issues['local.vram_reserve_gb'] ? true : undefined}
                  onChange={(e) => patch('local', 'vram_reserve_gb', Number(e.target.value))}
                />
                {issues['local.vram_reserve_gb'] ? <p className="field-error">{issues['local.vram_reserve_gb']}</p> : null}
              </Field>
            </div>
            <div className="toolbar">
              <span className="hint">
                已加载：{loadedKinds.join('、') || '无'}
                （预热与卸载在上方「模型预热」中操作）
              </span>
              <div className="toolbar__spacer" />
              <button type="button" className="btn btn--sm" onClick={() => setConfirmAction('unload-all')}>
                <Trash2 size={13} strokeWidth={1.75} /> 全部卸载
              </button>
            </div>
          </SectionCard>

          <SectionCard
            id={`${ANCHOR_PREFIX}qc`}
            title={hl('内容质检')}
            hint="任务完成后用 ASR 回译比对原文，发现内容缺失/错读"
            icon={<BadgeCheck size={15} strokeWidth={1.75} />}
            style={isShown('qc') ? undefined : { display: 'none' }}
          >
            <div className="field-row">
              <Field label={hl('自动质检')} hint="默认关闭；开启后每个语音任务会多跑一次本地转写">
                <select
                  className="select"
                  value={String(draft.qc?.enabled ?? false)}
                  onChange={(e) => patch('qc', 'enabled', e.target.value === 'true')}
                >
                  <option value="false">关闭（推荐，节省时间）</option>
                  <option value="true">开启</option>
                </select>
                <p className={draft.qc?.enabled ? 'field-note field-note--warn' : 'field-note'}>
                  {draft.qc?.enabled
                    ? '后果：每个任务完成后会多跑一次本地 ASR 转写，单任务耗时约增加 10–30%。'
                    : '后果：不质检，内容缺失/错读不会在任务详情里提示。'}
                </p>
              </Field>
              <Field label={hl('相似度阈值')} hint="低于该值在任务日志与界面提示质检未通过">
                <input
                  className="input num"
                  type="number"
                  min={0.3}
                  max={1}
                  step={0.05}
                  value={draft.qc?.min_similarity ?? 0.75}
                  aria-invalid={issues['qc.min_similarity'] ? true : undefined}
                  onChange={(e) => patch('qc', 'min_similarity', Number(e.target.value))}
                />
                {issues['qc.min_similarity'] ? <p className="field-error">{issues['qc.min_similarity']}</p> : null}
              </Field>
            </div>
            <p className="hint">
              质检结果写入任务的「转写文本」字段（形如 <code>[质检 86%] …回译文本…</code>），不改变任务状态，仅作提示。
            </p>
          </SectionCard>

          <SectionCard
            id={`${ANCHOR_PREFIX}text`}
            title={hl('文本处理')}
            hint="合成前把数字、单位、型号、日期转成中文读法"
            icon={<Sparkles size={15} strokeWidth={1.75} />}
            style={isShown('text') ? undefined : { display: 'none' }}
          >
            <div className="field-row">
              <Field label={hl('文本规范化')} hint="例：4GB→四 G B、¥99→九十九元、2026-10-18→二零二六年十月十八日">
                <select
                  className="select"
                  value={String(draft.tts?.normalize ?? true)}
                  onChange={(e) => patch('tts', 'normalize', e.target.value === 'true')}
                >
                  <option value="true">开启（推荐）</option>
                  <option value="false">关闭（保留原始写法）</option>
                </select>
              </Field>
              <Field label={hl('规范化语言')} hint="目前仅中文规则；其它语言原样输出">
                <select
                  className="select"
                  value={draft.tts?.normalize_lang ?? 'zh'}
                  onChange={(e) => patch('tts', 'normalize_lang', e.target.value)}
                >
                  <option value="zh">中文 (zh)</option>
                  <option value="en">英文 (en，暂原样)</option>
                </select>
              </Field>
            </div>
            <p className="hint">规范化幂等，云端二次下发不会重复改写；质检（ASR 回译）用规范化后的文本比对。</p>
          </SectionCard>

          <SectionCard
            title={hl('后期处理链')}
            hint="低切 → 高架 → 压缩 → 峰值归一化；关闭时输出与模型原生完全一致"
            icon={<Sparkles size={15} strokeWidth={1.75} />}
            style={isShown('post') ? undefined : { display: 'none' }}
          >
            <div className="field-row">
              <Field label={hl('启用后期处理')} hint="默认关闭；开启后每个生成任务都会过一遍处理链">
                <select
                  className="select"
                  value={String(draft.post?.enabled ?? false)}
                  onChange={(e) => patch('post', 'enabled', e.target.value === 'true')}
                >
                  <option value="false">关闭（默认）</option>
                  <option value="true">开启</option>
                </select>
              </Field>
              <Field label={hl('低切频率 Hz')} hint="去掉低频隆隆声，常用 80">
                <input
                  className="input"
                  type="number"
                  min={0}
                  max={300}
                  value={draft.post?.low_cut_hz ?? 80}
                  onChange={(e) => patch('post', 'low_cut_hz', Number(e.target.value))}
                />
              </Field>
              <Field label={hl('高架增益 dB')} hint="抬升高频清晰度，0 表示不动">
                <input
                  className="input"
                  type="number"
                  step={0.5}
                  min={-12}
                  max={12}
                  value={draft.post?.high_shelf_db ?? 0}
                  onChange={(e) => patch('post', 'high_shelf_db', Number(e.target.value))}
                />
              </Field>
            </div>
            <div className="field-row">
              <Field label={hl('压缩')} hint="超过阈值的部分按比率压，听感更稳">
                <select
                  className="select"
                  value={String(draft.post?.compress ?? true)}
                  onChange={(e) => patch('post', 'compress', e.target.value === 'true')}
                >
                  <option value="true">开启</option>
                  <option value="false">关闭</option>
                </select>
              </Field>
              <Field label={hl('压缩阈值 dB')}>
                <input
                  className="input"
                  type="number"
                  step={0.5}
                  min={-60}
                  max={0}
                  value={draft.post?.threshold_db ?? -18}
                  onChange={(e) => patch('post', 'threshold_db', Number(e.target.value))}
                />
              </Field>
              <Field label={hl('压缩比')}>
                <input
                  className="input"
                  type="number"
                  step={0.1}
                  min={1}
                  max={20}
                  value={draft.post?.ratio ?? 2.5}
                  onChange={(e) => patch('post', 'ratio', Number(e.target.value))}
                />
              </Field>
              <Field label={hl('峰值归一化 dBFS')} hint="留空表示不归一化">
                <input
                  className="input"
                  type="number"
                  step={0.5}
                  min={-20}
                  max={0}
                  value={draft.post?.normalize_peak_db ?? -1}
                  onChange={(e) =>
                    patch('post', 'normalize_peak_db', e.target.value === '' ? null : Number(e.target.value))
                  }
                />
              </Field>
            </div>
            <p className="hint">处理只作用于成品音频，不改引擎参数；实测峰值会写进任务参数（params._post）。</p>
          </SectionCard>

          <SectionCard
            id={`${ANCHOR_PREFIX}defaults`}
            title={hl('默认参数')}
            hint="新建任务时的初始值"
            style={isShown('defaults') ? undefined : { display: 'none' }}
          >
            <div className="field-row">
              {[
                ['speed', '语速', 0.5, 2, 0.05],
                ['pitch', '音高（半音）', -12, 12, 1],
                ['temperature', '温度', 0.1, 1.5, 0.05],
                ['top_p', 'Top P', 0.3, 1, 0.05],
                ['steps', '采样步数', 256, 4096, 128],
              ].map(([key, label, min, max, step]) => (
                <Field key={String(key)} label={hl(String(label))}>
                  <input
                    className="input num"
                    type="number"
                    min={Number(min)}
                    max={Number(max)}
                    step={Number(step)}
                    value={draft.defaults[key as string]}
                    onChange={(e) => patch('defaults', String(key), Number(e.target.value))}
                  />
                </Field>
              ))}
              <Field label={hl('输出格式')}>
                <select
                  className="select"
                  value={draft.defaults.output_format}
                  onChange={(e) => patch('defaults', 'output_format', e.target.value)}
                >
                  {settings?.limits.allowed_output_formats.map((fmt) => (
                    <option key={fmt} value={fmt}>
                      {fmt.toUpperCase()}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={hl('导出档位')} hint="Original 保留模型原生采样率；Standard 转 44.1k/16bit MP3 192k；For editing 转 48k/24bit 单声道 WAV">
                <select
                  className="select"
                  value={draft.export?.tier ?? 'original'}
                  onChange={(e) => patch('export', 'tier', e.target.value)}
                >
                  <option value="original">Original（模型原生）</option>
                  <option value="standard">Standard（44.1k MP3 192k）</option>
                  <option value="editing">For editing（48k 24bit WAV）</option>
                </select>
              </Field>
              <Field label={hl('目标时长（秒）')} hint="0 或留空 = 不控制；优先级高于语速">
                <input
                  className="input num"
                  type="number"
                  min={0}
                  step={0.5}
                  value={draft.defaults.target_duration_s ?? ''}
                  onChange={(e) =>
                    patch('defaults', 'target_duration_s', e.target.value === '' ? null : Number(e.target.value))
                  }
                />
              </Field>
            </div>
          </SectionCard>

          <SectionCard
            id={`${ANCHOR_PREFIX}license`}
            title={hl('授权')}
            hint="模型与数据的许可说明（只读）"
            icon={<BadgeCheck size={15} strokeWidth={1.75} />}
            style={isShown('license') ? undefined : { display: 'none' }}
          >
            <ul className="license-list">
              <li>
                <strong>模型许可</strong>：本地模型权重由各自的发布方授权（Qwen3-TTS 系列等），商用请以模型仓库页面为准；
                每个引擎的许可证与商用条款写在「<button type="button" className="link-btn" onClick={() => scrollToSection('engines')}>引擎与模型</button>」卡片里。
              </li>
              <li>
                <strong>数据位置</strong>：自定义音色、参考音频与生成结果只保存在本机 <code className="num">data/</code> 目录，
                不会自动上传；仅在云端/混合模式下合成时按需上传参考音频。
              </li>
              <li>
                <strong>密钥与隐私</strong>：API 密钥、SSH 密码写入运行期覆盖文件（<code>data/runtime.json</code>），
                界面中始终脱敏显示；请自行控制该目录的访问权限。
              </li>
            </ul>
          </SectionCard>
        </div>
      </div>

      {confirmAction === 'cloud-stop' ? (
        <ConfirmDialog
          title="确认关机 / 停止云端服务？"
          confirmLabel="关机（省费用）"
          busy={cloudStopping}
          onCancel={() => setConfirmAction(null)}
          onConfirm={() => {
            setConfirmAction(null);
            void doStopCloud('shutdown');
          }}
          secondaryLabel="仅停止服务（不关机）"
          onSecondary={() => {
            setConfirmAction(null);
            void doStopCloud('stop_service');
          }}
        >
          <p>
            <strong>确认关机</strong>：将通过 SSH 让云端实例执行 <code>shutdown -h now</code>，影响：
          </p>
          <ul>
            <li>云端正在跑的任务会立即中断（无法恢复，需要重跑）</li>
            <li>本地 SSH 隧道会同时断开，运行模式退回本地/混合</li>
            <li>关机后实例停止，平台停止计费；模型与数据在数据盘保留，<strong>再次使用需到平台控制台「开机」</strong></li>
            <li>若只想释放显存、保持实例在线，选下面的「仅停止服务」</li>
          </ul>
          <p className="hint">
            目标：{String(draft.cloud.tunnel.username || '')}@{String(draft.cloud.tunnel.ssh_host || '未填写')}:
            {String(draft.cloud.tunnel.ssh_port || 22)}
          </p>
        </ConfirmDialog>
      ) : null}

      {confirmAction === 'unload-all' ? (
        <ConfirmDialog
          title="确认全部卸载模型？"
          confirmLabel="全部卸载并释放显存"
          busy={preheating}
          onCancel={() => setConfirmAction(null)}
          onConfirm={() => {
            setConfirmAction(null);
            void doUnload([]);
          }}
        >
          <p>这会立刻把已加载到显存里的模型全部卸载，并同时关闭「启动时自动预热」。</p>
          <ul>
            <li>当前已加载：<strong>{loadedKinds.join('、') || '无'}</strong></li>
            <li>下一次生成需要重新加载模型，首个任务会明显变慢（约数十秒）。</li>
            <li>不会删除任何模型文件，也不会改动其它设置。</li>
          </ul>
        </ConfirmDialog>
      ) : null}

      {confirmAction === 'reset' ? (
        <ConfirmDialog
          title="确认恢复默认设置？"
          confirmLabel="清除覆盖并恢复默认"
          onCancel={() => setConfirmAction(null)}
          onConfirm={() => {
            setConfirmAction(null);
            void reset();
          }}
        >
          <p>这会清除界面写入的运行期覆盖，回到 config/config.yaml 里的默认值。</p>
          <ul>
            <li>受影响：运行模式、云端地址与密钥、隧道、模型与默认参数等全部运行期覆盖。</li>
            <li>不受影响：已生成的音频、任务记录、自定义音色、磁盘上的模型与引擎。</li>
            <li>恢复后如需原配置，需要重新填写（覆盖文件不会备份）。</li>
          </ul>
        </ConfirmDialog>
      ) : null}
    </div>
  );
}

export default SettingsPage;
