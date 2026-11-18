import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Cloud, PlugZap, X } from 'lucide-react';
import { Field, InlineNote, Spinner, StatusDot } from './ui';
import { parseSshCommand } from '../lib/sshcmd';

type StepState = 'pending' | 'running' | 'ok' | 'failed' | 'skipped';

type Step = { id: string; title?: string; state: StepState; detail?: string };

type Job = {
  job_id: string;
  status: string;
  current_step: string;
  steps: Step[];
  logs?: string[];
  error?: { code?: string; message?: string; next_actions?: string[]; step?: string; ai?: boolean };
  result?: { topology?: string; base_url?: string; latency_ms?: number };
  env?: Record<string, unknown>;
  ai_available?: boolean;
  target?: { host?: string; ssh_port?: number; username?: string };
};

const STATE_TEXT: Record<string, string> = { pending: '等待', running: '进行中', ok: '完成', failed: '失败', skipped: '跳过' };

export function CloudAgentWizard() {
  const [open, setOpen] = useState(false);
  const [host, setHost] = useState('');
  const [sshPort, setSshPort] = useState('22');
  const [username, setUsername] = useState('root');
  const [authType, setAuthType] = useState<'password' | 'key'>('password');
  const [password, setPassword] = useState('');
  const [keyPath, setKeyPath] = useState('');
  const [remotePort, setRemotePort] = useState('8443');
  /** 云端项目目录（「已装好」模式下直接复用，不再上传源码） */
  const [remoteDir, setRemoteDir] = useState('~/qwentts');
  const [localPort, setLocalPort] = useState('18443');
  const [pipIndex, setPipIndex] = useState('https://pypi.tuna.tsinghua.edu.cn/simple');
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retrySecret, setRetrySecret] = useState('');
  const [aiMode, setAiMode] = useState(false);
  const [downloadModels, setDownloadModels] = useState(true);
  const [prepareOnly, setPrepareOnly] = useState(false);
  /** 接入模式：full=完整安装；attach=云端已装好，只建隧道并启动服务 */
  const [attachMode, setAttachMode] = useState(false);
  const [trustHostkey, setTrustHostkey] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [llm, setLlm] = useState<any>(null);
  const [llmBase, setLlmBase] = useState('');
  const [llmModel, setLlmModel] = useState('');
  const [llmKey, setLlmKey] = useState('');
  const [llmMsg, setLlmMsg] = useState('');
  const [llmModels, setLlmModels] = useState<string[]>([]);
  const timer = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (timer.current !== null) {
      window.clearInterval(timer.current);
      timer.current = null;
    }
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);



  const poll = useCallback(
    (jobId: string) => {
      stopPolling();
      timer.current = window.setInterval(async () => {
        try {
          const res = await fetch(`/api/cloud/agent/jobs/${jobId}`);
          if (!res.ok) return;
          const data: Job = await res.json();
          setJob(data);
          setLastJob(data);
          if (data.status === 'succeeded' || data.status === 'failed' || data.status === 'cancelled') {
            stopPolling();
            setBusy(false);
          }
        } catch {
          /* 网络抖动：下一轮再试 */
        }
      }, 1500);
    },
    [stopPolling],
  );

  const [lastJob, setLastJob] = useState<Job | null>(null);
  const JOB_KEY = 'qwentts.cloudAgent.jobId';

  const summaryText = useCallback((data: Job | null) => {
    if (!data) return '';
    const steps = data.steps || [];
    const done = steps.filter((x) => x.state === 'ok' || x.state === 'skipped').length;
    if (data.status === 'running') return `进行中：${data.current_step}（${done}/${steps.length} 步）`;
    if (data.status === 'succeeded') return `已完成${data.result?.topology ? `（${data.result.topology === 'tunnel' ? 'SSH 隧道' : '直连'}）` : ''}`;
    if (data.status === 'cancelled') return '已取消';
    return `失败：${data.error?.code || ''} ${data.error?.step || data.current_step || ''}`.trim();
  }, []);

  const openJob = useCallback(
    async (jobId: string) => {
      try {
        const res = await fetch(`/api/cloud/agent/jobs/${jobId}`);
        if (!res.ok) return;
        const data: Job = await res.json();
        setJob(data);
        setLastJob(data);
        setOpen(true);
        if (data.status === 'running') {
          setBusy(true);
          poll(jobId);
        }
      } catch {
        /* 拉不到就保持原状 */
      }
    },
    [poll],
  );

  // 从别的页面切回来 / 刷新页面：自动接回正在跑的任务，或显示上次结果
  useEffect(() => {
    let alive = true;
    const restore = async () => {
      try {
        const res = await fetch('/api/cloud/agent/jobs');
        if (!res.ok || !alive) return;
        const data = await res.json();
        const items: Job[] = data.items || [];
        const saved = window.localStorage.getItem(JOB_KEY) || '';
        const target = items.find((i) => i.job_id === saved) || items[0];
        if (!target || !alive) return;
        if (target.status === 'running') {
          window.localStorage.setItem(JOB_KEY, target.job_id);
          setBusy(true);
          setOpen(true);
          await openJob(target.job_id);
        } else {
          setLastJob(target);
        }
      } catch {
        /* 忽略 */
      }
    };
    void restore();
    return () => {
      alive = false;
    };
  }, [JOB_KEY, openJob]);
  const loadLlm = async () => {
    try {
      const res = await fetch('/api/cloud/agent/llm');
      if (!res.ok) return;
      const data = await res.json();
      setLlm(data);
      setLlmBase(data.base_url || '');
      setLlmModel(data.model || '');
    } catch {
      /* 忽略 */
    }
  };

  useEffect(() => {
    if (aiOpen) void loadLlm();
  }, [aiOpen]);

  const saveLlm = async () => {
    setLlmMsg('保存中…');
    try {
      const res = await fetch('/api/cloud/agent/llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: true, base_url: llmBase, model: llmModel, api_key: llmKey || undefined }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail?.message || '保存失败');
      setLlm(data);
      setLlmKey('');
      setLlmMsg('已保存');
    } catch (exc) {
      setLlmMsg(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const fetchModels = async () => {
    setLlmMsg('拉取模型列表…');
    try {
      const res = await fetch('/api/cloud/agent/models');
      const data = await res.json();
      setLlmModels(data.models || []);
      const list = (data.models || []).length;
      setLlmMsg(
        list ? `可用模型 ${list} 个` : data.available ? `已配置，但拉取失败：${data.reason || '端点无响应'}` : `不可用：${data.reason || '未配置'}`,
      );
    } catch (exc) {
      setLlmMsg(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const start = async () => {
    setError(null);
    setBusy(true);
    setJob(null);
    const parsed = parseSshCommand(host);
    const realHost = parsed.host || host.trim();
    const realPort = parsed.port && parsed.port !== 22 ? parsed.port : Number(sshPort) || 22;
    const realUser = parsed.username || username.trim();
    try {
      const res = await fetch('/api/cloud/agent/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          host: realHost,
          ssh_port: realPort,
          username: realUser,
          auth:
            authType === 'password'
              ? { type: 'password', password }
              : { type: 'key', key_path: keyPath.trim() },
          options: { remote_port: Number(remotePort) || 8443, local_port: Number(localPort) || 18443, pip_index: pipIndex, ai_mode: aiMode, download_models: attachMode ? false : downloadModels, prepare_only: attachMode ? false : prepareOnly, allow_no_gpu: prepareOnly, trust_hostkey: trustHostkey, mode: attachMode ? 'attach' : 'full', remote_dir: remoteDir || undefined },
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        const d = data?.detail;
        const msg = typeof d === 'string' ? d : d?.detail?.message || d?.message || d?.hint || '发起接入失败';
        throw new Error(msg);
      }
      window.localStorage.setItem(JOB_KEY, data.job_id);
      setJob({ job_id: data.job_id, status: 'running', current_step: 'S0', steps: [] });
      setLastJob({ job_id: data.job_id, status: 'running', current_step: 'S0', steps: [] });
      poll(data.job_id);
    } catch (exc) {
      setBusy(false);
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const currentTarget = () => {
    const parsed = parseSshCommand(host);
    return {
      host: parsed.host || host.trim(),
      ssh_port: parsed.port && parsed.port !== 22 ? parsed.port : Number(sshPort) || 22,
      username: parsed.username || username.trim(),
    };
  };

  // 只有「当前填写的机器 == 任务那台机器」才允许续做，换机器必须从头开始
  const isSameTarget = (data: Job | null) => {
    if (!data?.target) return true;
    const t = currentTarget();
    return data.target.host === t.host && Number(data.target.ssh_port) === t.ssh_port;
  };

  const resumeJob = async () => {
    if (!job) return;
    setError(null);
    setBusy(true);
    try {
      const res = await fetch(`/api/cloud/agent/jobs/${job.job_id}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          auth:
            authType === 'password'
              ? { type: 'password', password: retrySecret }
              : { type: 'key', key_path: keyPath.trim() },
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail?.message || data?.detail || '续做失败');
      window.localStorage.setItem(JOB_KEY, job.job_id);
      poll(job.job_id);
    } catch (exc) {
      setBusy(false);
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const cancel = async () => {
    if (!job) return;
    try {
      await fetch(`/api/cloud/agent/jobs/${job.job_id}/cancel`, { method: 'POST' });
    } catch {
      /* 取消失败不阻塞界面 */
    }
  };

  if (!open) {
    const running = lastJob?.status === 'running';
    return (
      <div className="probe-bar" style={{ marginTop: 12 }}>
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => {
            setJob(null);   // 换机器/重新开始时先清掉旧进度，避免看着像“续跑旧任务”
            setOpen(true);
          }}
        >
          <Cloud size={14} strokeWidth={1.75} /> 一键接入云主机
        </button>
        {lastJob ? (
          <>
            <span className="probe-result" data-state={running ? 'running' : lastJob.status === 'succeeded' ? 'ok' : 'fail'}>
              {running ? <Spinner size={12} /> : <StatusDot tone={lastJob.status === 'succeeded' ? 'ok' : 'danger'} />}
              上次接入：{summaryText(lastJob)}
            </span>
            <button type="button" className="btn btn--sm" onClick={() => void openJob(lastJob.job_id)}>
              查看进度
            </button>
          </>
        ) : (
          <span className="hint">只需连接信息，自动布置源码、依赖、服务并写好本地配置</span>
        )}
      </div>
    );
  }

  const steps = job?.steps?.length ? job.steps : [];
  const failed = job?.status === 'failed';
  const success = job?.status === 'succeeded';

  return (
    <div style={{ marginTop: 12, border: 'var(--border-width) solid var(--color-border)', borderRadius: 'var(--radius-sm)', padding: 12 }}>
      <div className="probe-bar" style={{ marginBottom: 8 }}>
        <strong>一键接入云主机</strong>
        {job ? <span className="hint">{summaryText(job)}</span> : null}
        <button type="button" className="btn btn--sm" onClick={() => { stopPolling(); setOpen(false); }} title="收起（不会中断正在执行的任务）">
          <X size={14} strokeWidth={1.75} /> 收起
        </button>
      </div>

      <div className="field-row">
        <Field label="主机地址" hint="IP 或域名">
          <input className="input" value={host} onChange={(e) => setHost(e.target.value)} placeholder="1.2.3.4 或直接粘贴 ssh -p 13485 root@host" />
        </Field>
        <Field label="SSH 端口">
          <input className="input" value={sshPort} onChange={(e) => setSshPort(e.target.value)} />
        </Field>
        <Field label="用户名">
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
      </div>

      <div className="field-row">
        <Field label="认证方式">
          <select className="select" value={authType} onChange={(e) => setAuthType(e.target.value as 'password' | 'key')}>
            <option value="password">密码</option>
            <option value="key">私钥文件</option>
          </select>
        </Field>
        {authType === 'password' ? (
          <Field label="密码" hint="只存在内存，不落盘、不进日志">
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
        ) : (
          <Field label="私钥路径" hint="形如 C:\\Users\\you\\.ssh\\id_rsa">
            <input className="input" value={keyPath} onChange={(e) => setKeyPath(e.target.value)} />
          </Field>
        )}
        <Field label="云端项目目录" hint="云端已装好时填它，会直接复用；默认 ~/qwentts">
          <input className="input" value={remoteDir} onChange={(e) => setRemoteDir(e.target.value)} />
        </Field>
        <Field label="云端服务端口">
          <input className="input" value={remotePort} onChange={(e) => setRemotePort(e.target.value)} />
        </Field>
        <Field label="本地映射端口" hint="外部隧道工具或本程序隧道使用的本地端口">
          <input className="input" value={localPort} onChange={(e) => setLocalPort(e.target.value)} />
        </Field>
      </div>

      <Field label="pip 镜像">
        <input className="input" value={pipIndex} onChange={(e) => setPipIndex(e.target.value)} />
      </Field>

      <div style={{ marginTop: 8 }}>
        <button type="button" className="btn btn--sm" onClick={() => setAiOpen((v) => !v)}>
          {aiOpen ? '收起 AI 设置' : 'AI 设置（DeepSeek / OpenAI 兼容）'}
          {llm?.api_key_set ? <span className="badge badge--info" style={{ marginLeft: 6 }}>已配置</span> : null}
        </button>
      </div>

      {aiOpen ? (
        <div style={{ border: 'var(--border-width) solid var(--color-border)', borderRadius: 'var(--radius-sm)', padding: 10, marginTop: 8 }}>
          <div className="field-row">
            <Field label="接口地址" hint="OpenAI 兼容，形如 https://api.deepseek.com/v1">
              <input className="input" value={llmBase} onChange={(e) => setLlmBase(e.target.value)} placeholder="https://api.deepseek.com/v1" />
            </Field>
            <Field label="模型" hint="默认 deepseek-chat；也可填 /models 返回的显式版本 ID">
              <input className="input" value={llmModel} onChange={(e) => setLlmModel(e.target.value)} placeholder="deepseek-chat" />
            </Field>
            <Field label="API 密钥" hint={llm?.api_key_from_env ? '来自环境变量 QWENTTS_AGENT_KEY' : llm?.api_key_masked ? `当前 ${llm.api_key_masked}${llm.encrypted_at_rest ? '（已加密存储）' : ''}` : '未配置'}>
              <input className="input" type="password" value={llmKey} onChange={(e) => setLlmKey(e.target.value)} placeholder="sk-..." />
            </Field>
          </div>
          <div className="probe-bar">
            <button type="button" className="btn btn--sm btn--primary" onClick={() => void saveLlm()}>保存</button>
            <button type="button" className="btn btn--sm" onClick={() => void fetchModels()}>拉取模型列表</button>
            {llmModels.length ? <span className="hint">可选：{llmModels.slice(0, 6).join('、')}</span> : null}
            {llmMsg ? <span className="hint">{llmMsg}</span> : null}
          </div>
        </div>
      ) : null}

      <label className="hint" style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4 }}>
        <input type="checkbox" checked={trustHostkey} onChange={(e) => setTrustHostkey(e.target.checked)} />
        重置主机指纹（实例重装/迁移过，确认主机可信时勾选）
      </label>

      <div className="field" style={{ marginTop: 8 }}>
        <label>接入模式</label>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <label className="hint" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="radio" name="attach-mode" checked={!attachMode} onChange={() => setAttachMode(false)} />
            完整安装（首装：上传源码 + 装依赖 + 下模型 + 起服务）
          </label>
          <label className="hint" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="radio" name="attach-mode" checked={attachMode} onChange={() => setAttachMode(true)} />
            已装好，只连通并启动（跳过上传/装依赖/下模型）
          </label>
        </div>
        {attachMode ? (
          <p className="hint" style={{ marginTop: 4 }}>
            适用于云端已经有声匠（依赖与模型都装好）的情况：只做 SSH 连通 → 复用
            <code style={{ margin: '0 4px' }}>{remoteDir || '~/qwentts'}</code>
            → 写入云端配置 → 启动服务 → 建隧道 → 写本地云端配置。
            如果该目录里没有 <code style={{ margin: '0 4px' }}>backend/app/main.py</code>，会直接报错提醒你换目录或改用完整安装。
          </p>
        ) : null}
      </div>

      {!attachMode ? (
        <>
      <label className="hint" style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4 }}>
        <input type="checkbox" checked={prepareOnly} onChange={(e) => setPrepareOnly(e.target.checked)} />
        仅准备环境（无卡模式：只上传/装依赖/下模型，不启动云端服务；开机后点「续做」接着跑）
      </label>

      <label className="hint" style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4 }}>
        <input type="checkbox" checked={downloadModels} onChange={(e) => setDownloadModels(e.target.checked)} />
        云端缺模型时自动下载（ModelScope 国内源，1.7B 三件套约 12.6GB，可中断续传）
      </label>
        </>
      ) : null}

      <label className="hint" style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4 }}>
        <input type="checkbox" checked={aiMode} onChange={(e) => setAiMode(e.target.checked)} />
        AI 自主排障（需在配置里设好 agent.llm；失败时由模型选择白名单命令尝试修复，修不好自动回退标准流程）
      </label>

      <details className="hint" style={{ marginTop: 8 }}>
        <summary style={{ cursor: 'pointer' }}>连不上？云厂商端口没开放怎么办</summary>
        <ol style={{ margin: '6px 0 0 18px', lineHeight: 1.7 }}>
          <li>AutoDL 用户请先在实例页「更多 → 下载 SSH 隧道工具」，填入上面的登录指令与密码，点「开始代理」；</li>
          <li>把云端服务端口（如 8443）代理到本地某个端口（工具里「代理到本地端口」）；</li>
          <li>回到这里把「本地映射端口」改成同一个端口号，再点「开始接入」——程序会优先直接探这个本地端口；</li>
          <li>不用官方工具也行：直连失败时本程序会自动用 SSH 建隧道（127.0.0.1:本地映射端口 → 云端:服务端口）。</li>
        </ol>
      </details>

      <div className="probe-bar" style={{ marginTop: 8 }}>
        <button type="button" className="btn btn--sm btn--primary" onClick={() => void start()} disabled={busy || !host.trim()}>
          {busy ? <Spinner size={12} /> : <PlugZap size={14} strokeWidth={1.75} />}
          {busy ? '接入中…' : '开始接入'}
        </button>
        {busy ? (
          <button type="button" className="btn btn--sm" onClick={() => void cancel()}>
            取消
          </button>
        ) : null}
        {error ? <span className="probe-result" data-state="fail"><StatusDot tone="danger" /> {error}</span> : null}
      </div>

      {steps.length ? (
        <ul className="guide-steps" style={{ marginTop: 10 }}>
          {steps.map((step) => (
            <li key={step.id}>
              <span className="badge" data-state={step.state}>{STATE_TEXT[step.state]}</span>{' '}
              {step.title || step.id}
              {step.detail ? <span className="hint"> · {step.detail}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}

      {job?.logs?.length ? (
        <pre className="hint" style={{ maxHeight: 120, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
          {job.logs.slice(-8).join('\n')}
        </pre>
      ) : null}

      {failed ? (
        <InlineNote kind="warning">
          <strong>{job?.error?.code}</strong> {job?.error?.message}
          <ul style={{ margin: '6px 0 0 18px' }}>
            {(job?.error?.next_actions || []).map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
          {job?.error?.ai ? <span className="hint">（以上建议由 AI 诊断生成）</span> : null}
          {isSameTarget(job) ? (
            <div className="probe-bar" style={{ marginTop: 8 }}>
              {authType === 'password' ? (
                <input
                  className="input"
                  type="password"
                  value={retrySecret}
                  onChange={(e) => setRetrySecret(e.target.value)}
                  placeholder="重新输入密码以从失败步骤续做"
                />
              ) : null}
              <button type="button" className="btn btn--sm btn--primary" onClick={() => void resumeJob()} disabled={busy}>
                续做（跳过已完成步骤）
              </button>
            </div>
          ) : (
            <p className="hint" style={{ marginTop: 8 }}>
              当前填写的是另一台机器（{currentTarget().host}:{currentTarget().ssh_port}），
              这台上的进度没有意义——请直接点上方「开始接入」，会从第一步重新开始。
            </p>
          )}
        </InlineNote>
      ) : null}

      {success ? (
        <InlineNote kind="info">
          已可用：{job?.result?.topology === 'tunnel' ? 'SSH 隧道' : '直连'} {job?.result?.base_url}
          {job?.result?.latency_ms ? ` · 探活 ${job.result.latency_ms}ms` : ''}
          ，本地已切到云端模式。
        </InlineNote>
      ) : null}
    </div>
  );
}

export default CloudAgentWizard;
