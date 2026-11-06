import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowRight,
  Copy,
  Download,
  FileAudio,
  Gauge,
  Mic,
  RotateCcw,
  Sparkles,
  Square,
  Type,
  UploadCloud,
  Wand2,
  History,
  Keyboard,
  Users,
} from 'lucide-react';
import { api } from '../lib/api';
import { downloadFile } from '../lib/download';
import { ERROR_HINT, formatSeconds, formatTime, speakerColor, TYPE_LABEL } from '../lib/format';
import { useApp } from '../state/AppContext';
import type { Asset, DiarizeBackend, DiarizePrefer, Task, TaskParams, Voice } from '../types';
import { AudioPlayer, useAudioEngine } from '../components/Player';
import { DesignInput } from '../components/DesignInput';
import { ParamsPanel } from '../components/ParamsPanel';
import { RefAudio } from '../components/RefAudio';
import { TextEditor } from '../components/TextEditor';
import { NO_VOICE_ID, VoicePicker } from '../components/VoicePicker';
import { InlineNote, KV, ProgressBar, SectionCard, Skeleton, Spinner, StatusBadge } from '../components/ui';
import { useTaskStream } from '../hooks/useTaskStream';

type Tab = 'tts' | 'clone' | 'design' | 'asr';

const TABS: { id: Tab; label: string; icon: typeof Type }[] = [
  { id: 'tts', label: '语音生成', icon: Type },
  { id: 'clone', label: '声音克隆', icon: Mic },
  { id: 'design', label: '音色设计', icon: Wand2 },
  { id: 'asr', label: '音频转文字', icon: FileAudio },
];

/* ------------------- 记忆上次选择（localStorage，刷新恢复） ------------------- */
const STORE_TAB = 'qwentts.workbench.tab';
const STORE_VOICE = 'qwentts.workbench.voiceId';
const STORE_PARAMS = 'qwentts.workbench.params';
const STORE_PARAMS_OPEN = 'qwentts.workbench.paramsOpen';
const STORE_ASR_DIARIZE = 'qwentts.workbench.asrDiarize';
const STORE_ASR_SPEAKERS = 'qwentts.workbench.asrSpeakers';
const STORE_ASR_MIN_SPEAKERS = 'qwentts.workbench.asrMinSpeakers';

function readStoredTab(): Tab {
  const value = localStorage.getItem(STORE_TAB);
  return value === 'clone' || value === 'design' || value === 'asr' ? value : 'tts';
}

function readStoredParams(): TaskParams | null {
  const raw = localStorage.getItem(STORE_PARAMS);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? (parsed as TaskParams) : null;
  } catch {
    return null;
  }
}

function LiveElapsed({ active }: { active: boolean }) {
  const [elapsed, setElapsed] = useState(0);
  const started = useRef(0);

  useEffect(() => {
    if (!active) {
      setElapsed(0);
      return;
    }
    started.current = performance.now();
    const timer = window.setInterval(() => setElapsed(performance.now() - started.current), 100);
    return () => window.clearInterval(timer);
  }, [active]);
  if (!active) return null;
  return (
    <span className="num" style={{ color: 'var(--color-on-primary)' }}>
      {(elapsed / 1000).toFixed(1)}s
    </span>
  );
}

export function WorkbenchPage() {
  const app = useApp();
  const { meta, health, settings, pushToast, describeError } = app;
  const engine = useAudioEngine();
  const stream = useTaskStream(engine);

  const [tab, setTab] = useState<Tab>(readStoredTab);
  /** 当前引擎的能力标签与名称：决定哪些页签可用、文案怎么措辞（SPEC-ENGINE-AGNOSTIC-1） */
  const [engineCaps, setEngineCaps] = useState<{ id: string; name: string; caps: string[] }>({
    id: '',
    name: '',
    caps: ['tts', 'clone', 'design'],
  });
  const [recent, setRecent] = useState<Task[]>([]);

  useEffect(() => {
    let alive = true;
    const loadRecent = async () => {
      try {
        const res = await api.listTasks({ page: 1, page_size: 6, order: 'desc', status: 'all', type: 'all', q: '' });
        if (alive) setRecent(res.items || []);
      } catch {
        /* 右栏是辅助信息，失败静默 */
      }
    };
    void loadRecent();
    const timer = window.setInterval(loadRecent, 15000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);
  const [text, setText] = useState('');
  const [voiceList, setVoiceList] = useState<{
    builtin: Voice[];
    custom: Voice[];
    languages: { id: string; label: string }[];
  }>({ builtin: [], custom: [], languages: [] });
  const [voiceId, setVoiceId] = useState<string | null>(() => localStorage.getItem(STORE_VOICE));
  const [refAsset, setRefAsset] = useState<Asset | null>(null);
  const [privacyAck, setPrivacyAck] = useState(false);
  const [design, setDesign] = useState('');
  const [instruct, setInstruct] = useState('');
  const [asrAsset, setAsrAsset] = useState<Asset | null>(null);
  const [transcript, setTranscript] = useState<string | null>(null);
  const [params, setParams] = useState<TaskParams | null>(readStoredParams);
  const [paramsOpen, setParamsOpen] = useState(() => localStorage.getItem(STORE_PARAMS_OPEN) === '1');
  const [submitting, setSubmitting] = useState(false);
  const [loadingVoices, setLoadingVoices] = useState(true);
  const [asrBusy, setAsrBusy] = useState(false);
  /** 区分说话人：工作台单次开关，覆盖全局设置；仅在「音频转文字」页签生效 */
  const [asrDiarize, setAsrDiarize] = useState(() => localStorage.getItem(STORE_ASR_DIARIZE) === '1');
  const [asrMaxSpeakers, setAsrMaxSpeakers] = useState(() => {
    const n = Number(localStorage.getItem(STORE_ASR_SPEAKERS));
    return Number.isFinite(n) && n >= 2 && n <= 8 ? n : 4;
  });
  /** 最少说话人（1-8，必须 ≤ 最多说话人） */
  const [asrMinSpeakers, setAsrMinSpeakers] = useState(() => {
    const n = Number(localStorage.getItem(STORE_ASR_MIN_SPEAKERS));
    return Number.isFinite(n) && n >= 1 && n <= 8 ? n : 1;
  });
  /** /api/system/status 的 ASR 链路：实际尝试顺序 + 声纹开关，用于提交前判断能否做声纹 */
  const [asrPlan, setAsrPlan] = useState<{
    candidates: DiarizeBackend[];
    prefer: DiarizePrefer;
    enabled: boolean;
    /** 本地是否装了 CAM++（后端直报，不再靠 candidates 推断） */
    localAvailable?: boolean;
    /** null = 云端能力尚未探测 */
    cloudSupported?: boolean | null;
  } | null>(null);
  const voiceFallbackToasted = useRef(false);

  // meta 到达后补齐新增字段（保留用户上次调过的参数）
  useEffect(() => {
    if (!meta?.defaults) return;
    setParams((prev) => (prev ? { ...meta.defaults, ...prev } : meta.defaults));
  }, [meta]);

  // 有效能力：来自后端 /api/meta 的 capabilities（按 run.mode 计算）。
  // 不能再用本机引擎的 kind_tags —— 否则「云端 1.7B + 本地 0.6B + 纯云端」会把音色设计挡死。
  const effectiveCaps = meta?.capabilities;
  const capsList = useMemo(() => effectiveCaps?.caps ?? ['tts', 'clone', 'design'], [effectiveCaps]);
  const capsMissingHint = effectiveCaps?.missing_hint || '';

  useEffect(() => {
    if (!effectiveCaps) return;
    const label =
      effectiveCaps.source === 'cloud'
        ? `云端 · ${effectiveCaps.cloud_models || '未配置模型'}`
        : effectiveCaps.source === 'both'
          ? `${effectiveCaps.active_engine || '本地'} + 云端`
          : effectiveCaps.active_engine || '本地默认';
    setEngineCaps({
      id: effectiveCaps.active_engine || '',
      name: label,
      caps: effectiveCaps.caps,
    });
  }, [effectiveCaps]);

  // 当前页签被引擎能力禁用时，自动落到可用页签并提示一次
  useEffect(() => {
    const need = tab === 'asr' ? null : tab;
    if (!need || capsList.includes(need)) return;
    const next: Tab = capsList.includes('tts') ? 'tts' : 'clone';
    setTab(next);
    pushToast({
      level: 'info',
      title: '已切换到可用页签',
      description: `当前引擎「${engineCaps.name}」不支持${tab === 'design' ? '音色设计' : tab === 'clone' ? '声音克隆' : '语音生成'}`,
    });
  }, [tab, capsList, engineCaps.name, pushToast]);

  useEffect(() => {
    localStorage.setItem(STORE_TAB, tab);
  }, [tab]);
  useEffect(() => {
    if (voiceId) localStorage.setItem(STORE_VOICE, voiceId);
  }, [voiceId]);
  useEffect(() => {
    if (params) localStorage.setItem(STORE_PARAMS, JSON.stringify(params));
  }, [params]);
  useEffect(() => {
    localStorage.setItem(STORE_PARAMS_OPEN, paramsOpen ? '1' : '0');
  }, [paramsOpen]);
  useEffect(() => {
    localStorage.setItem(STORE_ASR_DIARIZE, asrDiarize ? '1' : '0');
  }, [asrDiarize]);
  useEffect(() => {
    localStorage.setItem(STORE_ASR_SPEAKERS, String(asrMaxSpeakers));
  }, [asrMaxSpeakers]);
  useEffect(() => {
    localStorage.setItem(STORE_ASR_MIN_SPEAKERS, String(asrMinSpeakers));
  }, [asrMinSpeakers]);
  // 下限不能超过上限：上限调小时同步夹紧（与设置页的联动校验一致）
  useEffect(() => {
    if (asrMinSpeakers > asrMaxSpeakers) setAsrMinSpeakers(asrMaxSpeakers);
  }, [asrMaxSpeakers, asrMinSpeakers]);

  // 命令面板「新建语音生成 / 新建声音克隆」跳转后切换能力
  useEffect(() => {
    const handler = (event: Event) => {
      const next = (event as CustomEvent<string>).detail;
      if (next === 'tts' || next === 'clone' || next === 'design' || next === 'asr') setTab(next);
    };
    window.addEventListener('qwentts:workbench-tab', handler);
    return () => window.removeEventListener('qwentts:workbench-tab', handler);
  }, []);

  const loadVoices = useCallback(async () => {
    setLoadingVoices(true);
    try {
      const data = await api.voices();
      setVoiceList({ builtin: data.builtin, custom: data.custom, languages: data.languages });
      setVoiceId((current) => current ?? data.builtin[0]?.id ?? null);
    } catch (error) {
      pushToast({ level: 'error', ...describeError(error) });
    } finally {
      setLoadingVoices(false);
    }
  }, [describeError, pushToast]);

  useEffect(() => {
    void loadVoices();
  }, [loadVoices]);

  // 上次记住的音色被删除：回落默认并提示一次
  useEffect(() => {
    if (loadingVoices) return;
    // 「纯文本合成」是伪音色（不在列表里），必须排除；否则会和下面的 effect 互相改写 → 界面抽搐
    if (!voiceId || voiceId === NO_VOICE_ID) return;
    const all = [...voiceList.builtin, ...voiceList.custom];
    if (!all.length) return;
    if (!all.some((voice) => voice.id === voiceId)) {
      const usable = all.filter((voice) => voice.available !== false);
      setVoiceId(usable[0]?.id ?? NO_VOICE_ID);
      if (!voiceFallbackToasted.current) {
        voiceFallbackToasted.current = true;
        pushToast({
          level: 'warning',
          title: '上次选择的音色已不存在',
          description: '已回落到可用音色，请重新确认。',
        });
      }
    }
  }, [loadingVoices, pushToast, voiceId, voiceList]);

  // 当前引擎不支持内置音色时（非 Qwen），把「上次记住的内置音色」换成「纯文本合成」，避免生成静音/报错
  const noVoiceToasted = useRef(false);
  useEffect(() => {
    if (loadingVoices) return;
    if (!voiceId || voiceId === NO_VOICE_ID) return;
    const all = [...voiceList.builtin, ...voiceList.custom];
    if (!all.length) return;
    const picked = all.find((voice) => voice.id === voiceId);
    if (!picked || picked.available !== false) return;
    setVoiceId(NO_VOICE_ID);
    if (!noVoiceToasted.current) {
      noVoiceToasted.current = true;
      pushToast({
        level: 'info',
        title: '已切换为「纯文本合成」',
        description: '当前引擎不支持该内置音色，改为不使用参考音色的文本合成',
      });
    }
  }, [loadingVoices, pushToast, voiceId, voiceList]);

  // ASR 链路：读实际尝试顺序（asr_candidates）与声纹开关，提交前就能判断「这次能不能做声纹」
  useEffect(() => {
    let alive = true;
    const read = () => {
      void api
        .status()
        .then((res) => {
          if (!alive) return;
          const cfg = res.config?.asr_diarize;
          // asr_diarize 是后端直报的权威块（勾选声纹后的尝试顺序 + 本地/云端可用性）
          const plan = res.asr_diarize;
          setAsrPlan({
            candidates: (plan?.candidates || res.asr_candidates || res.config?.asr_candidates || []) as DiarizeBackend[],
            prefer: (plan?.prefer || cfg?.prefer || 'local') as DiarizePrefer,
            enabled: !!(plan?.enabled ?? cfg?.enabled),
            localAvailable: plan?.local_available,
            cloudSupported: plan?.cloud_supported ?? null,
          });
        })
        .catch(() => {
          /* 状态拿不到时不做链路判断，交给后端报错 */
        });
    };
    read();
    const timer = window.setInterval(read, 20000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const mode = health?.mode || settings?.run.mode || 'hybrid';
  const cloudMode = mode !== 'local';
  const needPrivacy = tab === 'clone' && cloudMode && !!refAsset;

  /** 说话人识别优先位置：设置页刚改过时以 settings 为准，其次用状态接口回读值 */
  const diarizePrefer: DiarizePrefer = settings?.asr?.diarize?.prefer ?? asrPlan?.prefer ?? 'local';

  /** 提交前的链路判断：当前 asr_candidates 与 prefer 能不能真做声纹 */
  const diarizePlan = useMemo(() => {
    if (!asrPlan || !asrPlan.candidates.length) return null;
    const candidates = asrPlan.candidates;
    const where = (name: DiarizeBackend) => (name === 'cloud' ? '云端' : '本地');
    const target: DiarizeBackend = diarizePrefer === 'auto' ? candidates[0] : diarizePrefer;
    const other: DiarizeBackend = target === 'local' ? 'cloud' : 'local';
    const targetOk = candidates.includes(target);
    const otherOk = candidates.includes(other);
    if (targetOk) {
      if (diarizePrefer !== 'auto') return null;
      return {
        kind: 'info' as const,
        text: `自动选择：本次用${where(target)}声纹（尝试顺序 ${candidates.map(where).join(' → ')}）`,
      };
    }
    if (diarizePrefer === 'local' && otherOk) {
      return {
        kind: 'info' as const,
        text: `本地声纹不可用，本次会自动改由${where(other)}识别（asr.diarize.prefer = local）`,
      };
    }
    return {
      kind: 'warning' as const,
      text:
        diarizePrefer === 'cloud'
          ? '当前链路不支持云端声纹（asr.diarize.prefer = cloud），直接提交会报 CLOUD_DIARIZE_UNSUPPORTED'
          : '当前链路没有可用的声纹后端（本地未装 CAM++ 且云端不支持），直接提交会报 DIARIZE_UNAVAILABLE',
    };
  }, [asrPlan, diarizePrefer]);

  /**
   * 是否"Qwen 系"能力集（必须有音色、支持指令控制 instruct）。
   * 以前靠本地引擎 id 前缀猜，纯云端时会误判；现在以有效能力 + 来源为准：
   * 音色设计/克隆都由 Qwen 1.7B 提供，具备其一即视为 Qwen 系。
   */
  const engineIsQwen = useMemo(
    () => capsList.includes('design') || capsList.includes('clone'),
    [capsList],
  );

  const selectedVoice = useMemo(
    () => [...voiceList.builtin, ...voiceList.custom].find((v) => v.id === voiceId) || null,
    [voiceList, voiceId],
  );

  const canSubmit = useMemo(() => {
    // 注意：生成中**不禁用** —— 允许"追加生成"再提交一个任务，只挡重复点击
    if (submitting) return false;
    if (tab === 'asr') return !!asrAsset;
    if (!text.trim() || !params) return false;
    if (tab === 'tts') return !!voiceId || !engineIsQwen;
    if (tab === 'clone') return (!!voiceId && selectedVoice?.kind === 'custom') || !!refAsset;
    if (tab === 'design') return design.trim().length > 3;
    return false;
  }, [asrAsset, design, engineIsQwen, params, refAsset, selectedVoice, stream.streaming, submitting, tab, text, voiceId]);

  /** 按钮为何不可点——就近显示，实时随状态变化 */
  const disabledReason = useMemo<string | null>(() => {
    if (stream.streaming) return null;
    if (submitting) return null;
    if (tab === 'asr') return asrAsset ? null : '请先选择要转写的音频';
    if (!params) return '参数加载中…';
    if (!text.trim()) return '请输入文本';
    if (tab === 'design') return design.trim().length > 3 ? null : '请填写音色描述（至少 4 个字符）';
    if (tab === 'tts') return voiceId || !engineIsQwen ? null : '请选择音色（或选「纯文本合成」）';
    if (tab === 'clone') {
      if (refAsset) return null;
      if (!voiceId) return '请上传参考音频或选择自定义音色';
      if (selectedVoice && selectedVoice.kind !== 'custom') return '当前引擎不支持声音克隆，请上传参考音频';
      return null;
    }
    return null;
  }, [asrAsset, design, engineIsQwen, params, refAsset, selectedVoice, stream.streaming, submitting, tab, text, voiceId]);

  const handleError = useCallback(
    (error: unknown) => {
      pushToast({ level: 'error', ...describeError(error) });
    },
    [describeError, pushToast],
  );

  /** 一键切换声纹位置：热改 asr.diarize.prefer，不用等任务失败才知道 */
  const changeDiarizePrefer = async (next: DiarizePrefer) => {
    try {
      await api.updateSettings({ asr: { diarize: { prefer: next } } });
      await app.reloadSettings();
      pushToast({
        level: 'success',
        title: `说话人识别已切换为${next === 'local' ? '本地' : next === 'cloud' ? '云端' : '自动'}`,
        description: '下一个转写任务按新位置执行。',
      });
    } catch (error) {
      handleError(error);
    }
  };

  const submit = async (retryOf?: string) => {
    if (!params) return;
    if (needPrivacy && !privacyAck) {
      pushToast({ level: 'warning', title: '请先确认隐私提示', description: '云端模式上传参考音频前需要确认。' });
      return;
    }
    setSubmitting(true);
    setTranscript(null);
    try {
      let created;
      if (retryOf) {
        // 重试时把当前开关带过去，否则改了「区分说话人」再点重试仍是旧设置
        created = await api.retryTask(
          retryOf,
          undefined,
          tab === 'asr'
            ? { diarize: asrDiarize, max_speakers: asrMaxSpeakers, min_speakers: asrMinSpeakers }
            : undefined,
        );
      } else {
        // 转写任务把工作台的区分说话人开关写进参数，后端按任务级覆盖全局设置
        const mergedParams =
          tab === 'asr'
            ? {
                ...params,
                diarize: asrDiarize,
                max_speakers: asrMaxSpeakers,
                min_speakers: asrMinSpeakers,
              }
            : params;
        const body: Record<string, unknown> = {
          type: tab,
          text: tab === 'asr' ? '' : text,
          language: params.language,
          params: mergedParams,
          instruct,
          design: tab === 'design' ? design : '',
        };
        if (tab === 'asr') body.asset_id = asrAsset?.id;
        else if (tab === 'clone') {
          if (refAsset) body.asset_id = refAsset.id;
          else body.voice_id = voiceId;
        } else if (tab === 'tts') body.voice_id = voiceId;
        created = await api.createTask(body);
      }
      app.upsertTask(created);
      stream.start(created.id);
      pushToast({
        level: 'info',
        title: `任务已创建 #${created.id}`,
        description: '音频到达即播放，可在任务中心查看详情。',
      });
    } catch (error) {
      handleError(error);
    } finally {
      setSubmitting(false);
    }
  };

  // Ctrl/Cmd + Enter 提交（输入框聚焦时同样生效）
  const submitRef = useRef(submit);
  submitRef.current = submit;
  const canSubmitRef = useRef(canSubmit);
  canSubmitRef.current = canSubmit;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault();
        if (canSubmitRef.current) void submitRef.current();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const cancel = async () => {    if (!stream.task?.id) return;
    try {
      await api.cancelTask(stream.task.id);
      pushToast({ level: 'info', title: '已请求取消', description: '已生成的片段会保留并标注「已截断」。' });
    } catch (error) {
      handleError(error);
    }
  };

  const retryOnCloud = async () => {
    if (!stream.task?.id) return;
    try {
      const created = await api.retryTask(stream.task.id, 'cloud');
      app.upsertTask(created);
      stream.start(created.id);
      pushToast({ level: 'info', title: '已切换到云端继续', description: `新任务 #${created.id}` });
    } catch (error) {
      handleError(error);
    }
  };

  const uploadAsr = async (file: File | Blob, filename: string) => {
    setAsrBusy(true);
    try {
      const asset = await api.uploadAsset(file, filename);
      setAsrAsset(asset);
    } catch (error) {
      handleError(error);
    } finally {
      setAsrBusy(false);
    }
  };

  useEffect(() => {
    if (stream.task?.type === 'asr' && stream.task.transcript) setTranscript(stream.task.transcript);
  }, [stream.task?.type, stream.task?.transcript]);

  const offline = health ? health.active_backend === 'offline' : false;
  const taskError =
    stream.error ||
    (stream.task?.status === 'failed'
      ? {
          code: stream.task.error_code || 'INTERNAL',
          message: stream.task.error_message || '任务失败',
          hint: '',
        }
      : null);

  /** 长文本：只在用户本地参数上给建议，不静默改配置 */
  const longText = text.trim().length > 800;
  const streamingOff = params?.streaming === false;
  /** 只有「开启流式 + 真的在流」时才展示首包/分片进度；用户关掉流式就只看总进度 */
  const showStreamUi = stream.streaming && !streamingOff;

  /** 转写时间轴（区分说话人时带 speaker） */
  const asrSegments = stream.task?.segments ?? [];
  const hasSpeakers = asrSegments.some((seg) => !!seg.speaker);

  const copyError = async () => {
    if (!taskError) return;
    try {
      await navigator.clipboard.writeText(`${taskError.code}: ${taskError.message}`);
      pushToast({ level: 'success', title: '已复制错误信息' });
    } catch {
      pushToast({ level: 'error', title: '复制失败', description: '浏览器拒绝了剪贴板访问' });
    }
  };

  return (
    <div className="page">
      {offline ? (
        <InlineNote kind="error">
          后端服务离线：无法创建任务。请确认服务已启动（终端执行 python -m app.main），并检查浏览器证书信任。
        </InlineNote>
      ) : null}

      <div className="workbench">
        <div className="workbench__column">
          <SectionCard
            title="能力"
            actions={
              <span className="card__hint">
                {meta?.task_types?.map((t) => TYPE_LABEL[t.id]).join(' / ') || '加载中'}
              </span>
            }
          >
            <div className="segmented" role="tablist" aria-label="语音能力">
              {TABS.map((item) => {
                const Icon = item.icon;
                // 能力门禁：asr 与 TTS 引擎无关；tts/clone/design 取决于「有效能力」（按运行模式算）
                const need = item.id === 'asr' ? null : item.id;
                const blocked = !!need && !capsList.includes(need);
                const source = effectiveCaps?.source;
                const why =
                  item.id === 'design'
                    ? source === 'local'
                      ? `本地引擎「${engineCaps.name || '未检测'}」不支持音色设计；把运行模式切成「仅云端」或装 Qwen3-TTS 1.7B 后可用`
                      : `未检测到音色设计能力：${capsMissingHint || '请在「设置 → 云端连接」填写 VoiceDesign 模型名'}`
                    : `当前来源（${engineCaps.name || '未检测'}）不支持该能力：${capsMissingHint || '请检查设置'} `;
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="tab"
                    aria-selected={tab === item.id}
                    aria-disabled={blocked}
                    disabled={blocked}
                    className="segmented__item"
                    title={blocked ? why : undefined}
                    onClick={() => setTab(item.id)}
                  >
                    <Icon size={14} strokeWidth={1.75} />
                    {item.label}
                  </button>
                );
              })}
            </div>

            {tab !== 'asr' ? (
              <TextEditor
                value={text}
                onChange={setText}
                maxChars={meta?.limits.max_text_chars ?? 5000}
                disabled={stream.streaming}
                label={tab === 'clone' ? '要克隆朗读的文本' : tab === 'design' ? '要朗读的文本' : '待合成文本'}
              />
            ) : null}

            {tab !== 'asr' && longText && streamingOff ? (
              <div className="long-text-hint">
                <span>
                  长文本（<span className="num">{text.trim().length}</span> 字）建议开启流式合成（首包更快）
                </span>
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={() => setParams((prev) => ({ ...(prev as TaskParams), streaming: true }))}
                  title="只改本次参数，不修改全局配置"
                >
                  开启流式合成
                </button>
              </div>
            ) : null}

            {tab === 'tts' || tab === 'clone' ? (
              <div className="field">
                <span className="label">
                  {tab === 'clone' ? '参考音色（可选已有自定义音色）' : '音色'}
                  <span className="label__hint">{voiceList.builtin.length + voiceList.custom.length} 个音色</span>
                </span>
                {loadingVoices ? (
                  <div className="voice-grid">
                    {[0, 1, 2, 3].map((i) => (
                      <Skeleton key={i} height={132} radius={12} />
                    ))}
                  </div>
                ) : (
                  <VoicePicker
                    builtin={tab === 'clone' ? [] : voiceList.builtin}
                    custom={voiceList.custom}
                    value={voiceId}
                    onChange={(voice) => setVoiceId(voice.id)}
                    languages={voiceList.languages}
                    onCreate={() => {
                      setTab('clone');
                      pushToast({ level: 'info', title: '请上传或录制参考音频', description: '上传后即可零样本克隆。' });
                    }}
                  />
                )}
              </div>
            ) : null}

            {tab === 'clone' ? (
              <div className="field">
                <span className="label">
                  参考音频
                  <span className="label__hint">3 秒即可克隆，推荐 3-10 秒</span>
                </span>
                <RefAudio
                  asset={refAsset}
                  onChange={setRefAsset}
                  maxSeconds={meta?.limits.max_ref_audio_seconds ?? 30}
                  maxUploadMb={meta?.limits.max_upload_mb ?? 50}
                  privacyRequired={cloudMode}
                  privacyAck={privacyAck}
                  onPrivacyAck={setPrivacyAck}
                  onError={handleError}
                />
              </div>
            ) : null}

            {tab === 'design' ? <DesignInput value={design} onChange={setDesign} disabled={stream.streaming} /> : null}

            {tab === 'tts' || tab === 'clone' ? (
              <div className="field">
                <label className="label" htmlFor="instruct">
                  指令控制
                  <span className="label__hint">用自然语言控制语气、情感、语速与风格</span>
                </label>
                <input
                  id="instruct"
                  className="input"
                  value={instruct}
                  placeholder="例如：语气更温柔一些，语速稍慢，句尾轻收"
                  onChange={(event) => setInstruct(event.target.value)}
                  disabled={stream.streaming}
                />
              </div>
            ) : null}

            {tab === 'asr' ? (
              <div className="field">
                <span className="label">
                  待转写音频
                  <span className="label__hint">支持长音频，上限 {meta?.limits.max_asr_audio_seconds ?? 1800}s</span>
                </span>
                <div className="toolbar">
                  <label className="btn">
                    {asrBusy ? <Spinner size={14} /> : <UploadCloud size={14} strokeWidth={1.75} />}
                    选择音频
                    <input
                      type="file"
                      accept="audio/*"
                      className="visually-hidden"
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) void uploadAsr(file, file.name);
                      }}
                    />
                  </label>
                  {asrAsset ? (
                    <span className="chip">
                      {asrAsset.filename} · <span className="num">{formatSeconds(asrAsset.duration_sec, 1)}</span>
                    </span>
                  ) : (
                    <span className="hint">尚未选择文件</span>
                  )}
                </div>
                <div className="toolbar" style={{ marginTop: 'var(--space-2)', flexWrap: 'wrap' }}>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={asrDiarize}
                      disabled={stream.streaming}
                      onChange={(event) => setAsrDiarize(event.target.checked)}
                      aria-describedby={asrDiarize && diarizePlan ? 'asr-diarize-note' : undefined}
                    />
                    <Users size={14} strokeWidth={1.75} />
                    区分说话人
                  </label>
                  {asrDiarize ? (
                    <>
                      <span className="hint">最少</span>
                      <select
                        className="select"
                        style={{ width: 'auto' }}
                        value={asrMinSpeakers}
                        disabled={stream.streaming}
                        onChange={(event) => setAsrMinSpeakers(Number(event.target.value))}
                        aria-label="最少说话人数"
                        title="说话人下限：不足时后端会强制拆分补足（1 = 不做下限约束）"
                      >
                        {Array.from({ length: asrMaxSpeakers }, (_, i) => i + 1).map((n) => (
                          <option key={n} value={n}>
                            {n}
                          </option>
                        ))}
                      </select>
                      <span className="hint">最多</span>
                      <select
                        className="select"
                        style={{ width: 'auto' }}
                        value={asrMaxSpeakers}
                        disabled={stream.streaming}
                        onChange={(event) => setAsrMaxSpeakers(Number(event.target.value))}
                        aria-label="最多说话人数"
                      >
                        {[2, 3, 4, 5, 6, 7, 8].map((n) => (
                          <option key={n} value={n}>
                            {n}
                          </option>
                        ))}
                      </select>
                      <span className="hint">人 · 按声纹切分并标注 SPK1/SPK2…</span>
                    </>
                  ) : (
                    <span className="hint">仅转写文本，不做声纹切分（更快）</span>
                  )}
                </div>
                {asrDiarize && meta?.capabilities?.diarize_available === false ? (
              <p className="field-note field-note--warn" role="status">
                云端实例未安装说话人区分引擎：本次转写会只出整段文本，不会标注说话人。
                要分说话人请装云端 CAM++（设置 → 云端引擎管理），或改用本地声纹。
              </p>
            ) : null}
            {asrDiarize && diarizePlan ? (
                  <div id="asr-diarize-note">
                    <InlineNote
                      kind={diarizePlan.kind}
                      action={
                        <div className="toolbar" style={{ flexWrap: 'wrap' }}>
                          {asrPlan && asrPlan.candidates.includes('local') && diarizePrefer !== 'local' ? (
                            <button
                              type="button"
                              className="btn btn--sm btn--primary"
                              onClick={() => void changeDiarizePrefer('local')}
                              title="把 asr.diarize.prefer 改成 local，用本地 CAM++ 做声纹"
                            >
                              改用本地声纹
                            </button>
                          ) : null}
                          {asrPlan && asrPlan.candidates.includes('cloud') && diarizePrefer !== 'cloud' ? (
                            <button
                              type="button"
                              className="btn btn--sm"
                              onClick={() => void changeDiarizePrefer('cloud')}
                              title="把 asr.diarize.prefer 改成 cloud"
                            >
                              改用云端声纹
                            </button>
                          ) : null}
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => setAsrDiarize(false)}
                            title="本次只转文字，不做声纹切分"
                          >
                            关闭区分说话人
                          </button>
                          <a className="btn btn--sm btn--ghost" href="#/settings?s=asr">
                            去设置装云端声纹 →
                          </a>
                        </div>
                      }
                    >
                      {diarizePlan.text}
                    </InlineNote>
                  </div>
                ) : null}
                {transcript ? (
                  <div className="nested" style={{ display: 'grid', gap: 'var(--space-2)' }}>
                    <div className="toolbar">
                      <strong style={{ fontSize: 'var(--fs-13)' }}>转写结果</strong>
                      <div className="toolbar__spacer" />
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => {
                          void navigator.clipboard.writeText(transcript);
                          pushToast({ level: 'success', title: '已复制到剪贴板' });
                        }}
                      >
                        <Copy size={13} strokeWidth={1.75} /> 复制
                      </button>
                      {stream.task?.output_file ? (
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          onClick={() => {
                            void downloadFile(
                              api.fileUrl(stream.task!.id, stream.task!.output_file!),
                              stream.task!.output_file!,
                            ).catch(() => {
                              /* 已兜底打开新窗口 */
                            });
                          }}
                        >
                          <Download size={13} strokeWidth={1.75} /> 下载 TXT
                        </button>
                      ) : null}
                    </div>
                    {hasSpeakers ? (
                      <div style={{ display: 'grid', gap: 'var(--space-1)' }}>
                        {asrSegments.map((seg, index) => {
                          const color = speakerColor(seg.speaker);
                          return (
                            <div key={`${index}-${seg.start_s}`} style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'baseline' }}>
                              <span className="chip" style={{ flex: '0 0 auto', color, borderColor: color }}>
                                {seg.speaker || 'SPK?'}
                              </span>
                              <span className="hint num" style={{ flex: '0 0 auto' }}>
                                {formatSeconds(seg.start_s, 1)}
                              </span>
                              <span style={{ fontSize: 'var(--fs-13)' }}>{seg.text}</span>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <p style={{ whiteSpace: 'pre-wrap', fontSize: 'var(--fs-13)' }}>{transcript}</p>
                    )}
                  </div>
                ) : null}
              </div>
            ) : null}
          </SectionCard>

          {params ? (
            <ParamsPanel
              value={params}
              defaults={meta?.defaults ?? params}
              onChange={(patch) => setParams((prev) => ({ ...(prev as TaskParams), ...patch }))}
              languages={voiceList.languages.length ? voiceList.languages : meta?.languages ?? []}
              outputFormats={meta?.output_formats ?? ['wav']}
              disabled={stream.streaming}
              expanded={paramsOpen}
              onExpandedChange={setParamsOpen}
            />
          ) : (
            <Skeleton height={64} radius={12} />
          )}

          <div className={tab !== 'asr' ? 'primary-action primary-action--sticky' : 'primary-action'}>
            <div className="primary-action__row">
              {/* 生成中也可点击：用当前表单再建一个任务（追加），不打断当前流 */}
              <button
                type="button"
                className="btn btn--primary btn--cta"
                onClick={() => void submit()}
                disabled={!canSubmit}
                title={disabledReason || '开始生成（Ctrl / ⌘ + Enter）'}
                aria-describedby="submit-reason"
              >
                {submitting ? <Spinner size={16} /> : <Sparkles size={18} strokeWidth={1.75} />}
                {stream.streaming ? (tab === 'asr' ? '追加转写' : '追加生成') : tab === 'asr' ? '开始转写' : '开始生成'}
                <ArrowRight size={16} strokeWidth={1.75} />
              </button>
              {stream.streaming ? (
                <button type="button" className="btn btn--danger" onClick={cancel} title="停止当前生成流">
                  <Square size={16} strokeWidth={2} /> 停止
                  <LiveElapsed active={stream.streaming} />
                </button>
              ) : null}
            </div>
            {!stream.streaming && disabledReason ? (
              <p className="submit-reason" id="submit-reason">
                {disabledReason}
              </p>
            ) : (
              <p className="submit-reason submit-reason--muted" id="submit-reason">
                快捷键 <kbd className="kbd">Ctrl / ⌘ + Enter</kbd> 直接开始
              </p>
            )}
            {health?.cpu_mode && text.trim() && !stream.streaming ? (
              <p className="hint" style={{ marginTop: 'var(--space-1)', textAlign: 'center', color: 'var(--color-warning)' }}>
                CPU 模式：每字约 8-12 秒，{text.trim().length} 字预计 {Math.max(1, Math.round((text.trim().length * 10) / 60))} 分钟以上；
                想快请把「设置 → 推理设备」改为 GPU（0.6B 回退）或切云端
              </p>
            ) : null}
          </div>
        </div>

        <div className="workbench__column">
          <SectionCard
            title="结果"
            icon={<Gauge size={15} strokeWidth={1.75} />}
            actions={stream.task ? <StatusBadge status={stream.task.status} /> : null}
          >
            {stream.streaming || stream.chunkCount > 0 ? (
              <>
                {stream.streaming && stream.chunkCount === 0 ? (
                  <div className="player player--skeleton" aria-hidden="true">
                    <div className="player__controls">
                      <Skeleton height={40} width={40} radius={999} />
                      <div style={{ display: 'grid', gap: 'var(--space-1)', flex: 1 }}>
                        <Skeleton height={12} width={96} />
                        <Skeleton height={10} width={168} />
                      </div>
                      <Skeleton height={22} width={72} radius={999} />
                    </div>
                    <Skeleton height={76} radius={8} />
                    <span className="hint">
                      {streamingOff ? '正在生成（未启用流式），完成后自动播放' : '正在等待首包，到达即播放'}
                    </span>
                  </div>
                ) : (
                  <AudioPlayer
                    engine={engine}
                    streaming={stream.streaming}
                    firstPacketMs={streamingOff ? null : stream.firstPacketMs}
                    backend={stream.task?.backend}
                    model={stream.task?.model}
                    truncated={stream.task?.canceled}
                    downloadUrl={stream.task?.output_file && !stream.streaming ? api.downloadUrl(stream.task.id) : null}
                    downloadName={stream.task?.output_file || undefined}
                    emptyText="等待首包…"
                    title={stream.streaming ? '流式合成中，首包即播' : '本次结果'}
                  />
                )}
                <ProgressBar
                  value={stream.task?.progress ?? (stream.streaming ? 0.1 : 0)}
                  indeterminate={stream.streaming && !stream.chunkCount && !streamingOff}
                />
                {showStreamUi && !stream.chunkCount ? (
                  <InlineNote kind="info">
                    {streamingOff
                      ? `正在生成（未启用流式合成）……首次加载 ${engineCaps.name || '当前引擎'} 模型可能需要 20-60 秒，请稍候。`
                      : `正在等待首包……首次加载 ${engineCaps.name || '当前引擎'} 模型可能需要 20-60 秒（加载后会复用，后续请求更快）。`}
                    若长时间无响应，可在「状态与日志」查看加载进度或切换到云端。
                  </InlineNote>
                ) : null}
                {stream.task?.degraded && !stream.notice ? (
                  <InlineNote kind="warning">
                    {`当前使用 ${engineCaps.name || '小规格模型'} 回退（显存不足）。可在设置里换小规格引擎，或切换云端模式。`}
                  </InlineNote>
                ) : null}
                {/* 进度提示只在任务「还在跑」时显示，完成后立刻消失（否则会出现“已完成却仍显示生成中”） */}
                {stream.notice && stream.streaming ? (
                  <InlineNote kind="info">{stream.notice}</InlineNote>
                ) : null}
              </>
            ) : stream.task ? (
              <AudioPlayer
                engine={engine}
                firstPacketMs={stream.task.first_packet_ms}
                backend={stream.task.backend}
                model={stream.task.model}
                truncated={stream.task.canceled}
                downloadUrl={stream.task.output_file ? api.downloadUrl(stream.task.id) : null}
                downloadName={stream.task.output_file || undefined}
                emptyText={stream.task.type === 'asr' ? '该任务为文本转写' : '点击播放按钮加载结果'}
                title={`任务 #${stream.task.id}`}
              />
            ) : (
              <div className="empty-state">
                <div className="empty-state__icon">
                  <Mic size={26} strokeWidth={1.5} />
                </div>
                <div className="empty-state__title">还没有生成结果</div>
                <p className="empty-state__desc">在左侧输入文本并点击「开始生成」，首包到达即会播放。</p>
              </div>
            )}
            {taskError ? (
              <InlineNote
                kind="error"
                action={
                  <div className="toolbar">
                    <button type="button" className="btn btn--sm" onClick={() => void submit(stream.task?.id)}>
                      <RotateCcw size={13} strokeWidth={1.75} /> 重试
                    </button>
                    <button type="button" className="btn btn--sm" onClick={() => void copyError()}>
                      <Copy size={13} strokeWidth={1.75} /> 复制错误
                    </button>
                    <button type="button" className="btn btn--sm btn--primary" onClick={retryOnCloud}>
                      一键切换到云端
                    </button>
                  </div>
                }
              >
                任务失败 <span className="error-code">{taskError.code}</span> {taskError.message}
                {taskError.hint ? ` ${taskError.hint}` : ERROR_HINT[taskError.code] ? ` ${ERROR_HINT[taskError.code]}` : ''}
              </InlineNote>
            ) : null}
          </SectionCard>

          <SectionCard title="运行状态" icon={<Gauge size={15} strokeWidth={1.75} />}>
            <div className="kv">
              <KV
                label="当前后端"
                value={
                  health?.active_backend === 'cloud'
                    ? '云端'
                    : health?.active_backend === 'local'
                      ? health?.cpu_mode
                        ? '本地 CPU'
                        : '本地 GPU'
                      : health?.active_backend === 'mock'
                        ? '模拟'
                        : '—'
                }
              />
              <KV label="首包延迟" value={stream.firstPacketMs ?? stream.task?.first_packet_ms ?? '—'} unit="ms" />
              <KV
                label="本次耗时"
                value={stream.task?.elapsed_ms ? Math.round(stream.task.elapsed_ms) : '—'}
                unit="ms"
              />
              <KV label="实时率 RTF" value={stream.task?.rtf != null ? stream.task.rtf.toFixed(2) : '—'} />
              <KV
                label="音频时长"
                value={stream.task?.duration_sec != null ? formatSeconds(stream.task.duration_sec, 1) : '—'}
              />
              <KV label="显存占用" value={health?.local?.vram_allocated_mb ?? 0} unit="MB" />
            </div>
            {selectedVoice ? (
              <p className="hint">
                当前音色：{selectedVoice.name}
                {selectedVoice.language ? ` · ${selectedVoice.language}` : ''}
              </p>
            ) : null}
            {stream.task ? (
              <p className="hint">
                任务 #{stream.task.id} · 创建于 {formatTime(stream.task.created_at)} ·{' '}
                {stream.task.model || '模型待分配'}
              </p>
            ) : null}
          </SectionCard>

          <SectionCard title="使用提示" hint="3 条">
            <ul
              className="hint"
              style={{ margin: 0, paddingLeft: 'var(--space-4)', display: 'grid', gap: 'var(--space-2)' }}
            >
              <li>克隆建议 3-10 秒纯净人声，背景噪音会明显影响相似度。</li>
              <li>长文本会自动按句分段流式合成，首包更快；可在任务中心查看完整耗时。</li>
              <li>本地显存不足时系统会提示并可一键切换到云端继续同一任务。</li>
            </ul>
          </SectionCard>
      <SectionCard
        title="最近任务"
        hint={recent.length ? `${recent.length} 条` : '暂无'}
        icon={<History size={15} strokeWidth={1.75} />}
      >
        {recent.length ? (
          <ul className="recent-list">
            {recent.map((t) => (
              <li key={t.id}>
                <a className="recent-list__row" href={`#/tasks?q=${encodeURIComponent(t.id)}`}>
                  <span className="recent-list__type">{TYPE_LABEL[t.type] || t.type}</span>
                  <span className="recent-list__text" title={t.text || ''}>
                    {(t.text || '').slice(0, 40) || '（无文本）'}
                  </span>
                  <span className="recent-list__meta num">
                    {t.duration_sec ? `${t.duration_sec.toFixed(2)}s` : '—'}
                  </span>
                  <StatusBadge status={t.status} />
                </a>
              </li>
            ))}
          </ul>
        ) : (
          <p className="hint" style={{ margin: 0 }}>
            还没有任务。输入文本后按 <kbd className="kbd">Ctrl</kbd> + <kbd className="kbd">Enter</kbd> 开始生成。
          </p>
        )}
        <a className="recent-list__more" href="#/tasks">查看全部任务 →</a>
      </SectionCard>

      <SectionCard title="快捷键" hint="全站" icon={<Keyboard size={15} strokeWidth={1.75} />}>
        <dl className="shortcut-list">
          <div>
            <dt><kbd className="kbd">Ctrl</kbd> + <kbd className="kbd">K</kbd></dt>
            <dd>命令面板：跳页 / 新建 / 切换引擎</dd>
          </div>
          <div>
            <dt><kbd className="kbd">Ctrl</kbd> + <kbd className="kbd">Enter</kbd></dt>
            <dd>提交当前生成</dd>
          </div>
          <div>
            <dt><kbd className="kbd">j</kbd> / <kbd className="kbd">k</kbd></dt>
            <dd>任务中心行间移动；<kbd className="kbd">r</kbd> 重试、<kbd className="kbd">d</kbd> 下载</dd>
          </div>
          <div>
            <dt><kbd className="kbd">Esc</kbd></dt>
            <dd>关闭面板或浮层</dd>
          </div>
        </dl>
      </SectionCard>
        </div>
      </div>
    </div>
  );
}

export default WorkbenchPage;
