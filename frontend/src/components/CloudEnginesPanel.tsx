import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, Cloud, Download, Link2, PlugZap, RefreshCw, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { parseSshCommand } from '../lib/sshcmd';
import type { CloudEngineJob, CloudEngineList, CloudHostProfile } from '../types';
import { ProgressBar, Skeleton, Spinner } from './ui';

const KIND_LABEL: Record<string, string> = {
  tts: '语音合成',
  asr: '语音识别',
  speaker: '声纹（说话人区分）',
};

const STATUS_TEXT: Record<string, string> = {
  ready: '已安装',
  not_installed: '未安装',
  package_missing: '缺依赖',
  installing: '安装中',
};

function statusTone(status: string): { color: string; text: string } {
  if (status === 'ready') return { color: 'var(--color-success)', text: STATUS_TEXT.ready };
  if (status === 'package_missing') return { color: 'var(--color-warning)', text: STATUS_TEXT.package_missing };
  return { color: 'var(--color-text-secondary)', text: STATUS_TEXT[status] || status };
}

/**
 * 云端引擎管理：模型在云端主机上（不占本机磁盘/显存），通过 SSH 调云端 engines_cli。
 * 与本机「引擎与模型」分区完全独立。
 */
export function CloudEnginesPanel({ pushToast }: { pushToast?: (t: any) => void } = {}) {
  const [host, setHost] = useState<CloudHostProfile | null>(null);
  const [form, setForm] = useState({
    host: '',
    ssh_port: 22,
    username: 'root',
    password: '',
    project_dir: '/root/autodl-tmp/QWENTTS',
    python: '/root/miniconda3/bin/python',
  });
  const [editing, setEditing] = useState(false);
  /** 直接粘贴云厂商给的登录命令，一键填 host/端口/用户 */
  const [pasteText, setPasteText] = useState('');
  const [busy, setBusy] = useState('');
  const [list, setList] = useState<CloudEngineList | null>(null);
  const [error, setError] = useState('');
  const [testResult, setTestResult] = useState<any>(null);
  const [job, setJob] = useState<CloudEngineJob | null>(null);

  const toast = (level: string, title: string, description = '') =>
    pushToast?.({ level, title, description });

  const loadHost = useCallback(async () => {
    try {
      const data = await api.cloudHost();
      setHost(data);
      // 不论是否已保存，都用后端给的值预填：未部署时它来自「一键接入」/「SSH 隧道」配置
      setForm((prev) => ({
        ...prev,
        host: data.host || prev.host,
        ssh_port: data.ssh_port || prev.ssh_port,
        username: data.username || prev.username,
        project_dir: data.project_dir || prev.project_dir,
        python: data.python || prev.python,
        password: '',
      }));
      if (!data.configured) setEditing(true);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  const loadEngines = useCallback(async () => {
    setBusy('list');
    setError('');
    try {
      setList(await api.cloudEngines());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setList(null);
    } finally {
      setBusy('');
    }
  }, []);

  useEffect(() => {
    void loadHost();
  }, [loadHost]);

  useEffect(() => {
    if (host?.configured) void loadEngines();
  }, [host?.configured, loadEngines]);

  // 安装任务轮询
  useEffect(() => {
    if (!job || job.status !== 'running') return;
    const timer = window.setInterval(async () => {
      try {
        const fresh = await api.cloudEngineJob(job.job_id);
        setJob(fresh);
        if (fresh.status !== 'running') {
          void loadEngines();
          // 云端模型文件刚落盘时立刻读可能拿到半成品尺寸，1.5s 后再刷一次
          window.setTimeout(() => void loadEngines(), 1500);
          if (fresh.status === 'completed') toast('success', '云端安装完成', fresh.engine_id);
          else toast('error', '云端安装失败', fresh.error || '看日志尾部');
        }
      } catch {
        /* 轮询失败静默，下一轮再试 */
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [job, loadEngines]);

  const saveHost = async () => {
    setBusy('save');
    setError('');
    try {
      const saved = await api.saveCloudHost({
        host: form.host.trim(),
        ssh_port: Number(form.ssh_port) || 22,
        username: form.username.trim(),
        project_dir: form.project_dir.trim(),
        python: form.python.trim(),
        ...(form.password ? { password: form.password } : {}),
      });
      setHost(saved);
      setForm((prev) => ({ ...prev, password: '' }));
      setEditing(false);
      toast('success', '云端主机档案已保存', saved.host || '');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy('');
    }
  };

  const testHost = async () => {
    setBusy('test');
    setError('');
    setTestResult(null);
    try {
      const res = await api.testCloudHost();
      setTestResult(res);
      toast(res.ok ? 'success' : 'warning', res.ok ? '云端连接正常' : '连接成功但项目目录不存在', res.host || '');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy('');
    }
  };

  const startInstall = async (engineId: string) => {
    setError('');
    try {
      const started = await api.installCloudEngine(engineId);
      setJob({
        job_id: started.job_id,
        engine_id: engineId,
        status: 'running',
        progress: 0,
        phase: '准备',
        logs: [],
      });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const removeEngine = async (engineId: string) => {
    setBusy(`remove:${engineId}`);
    setError('');
    try {
      const res = await api.removeCloudEngine(engineId);
      toast('success', `已删除云端 ${engineId}`, res?.message?.slice?.(0, 120) || '');
      await loadEngines();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy('');
    }
  };

  const selectEngine = async (engineId: string) => {
    setBusy(`select:${engineId}`);
    setError('');
    try {
      await api.selectCloudEngine(engineId);
      // 乐观反馈：先把本地列表里的「当前」改过去，再拉一次权威值
      setList((prev) => {
        if (!prev) return prev;
        const item = (prev.items || []).find((it) => it.id === engineId);
        const kind = item?.kind || (item?.kind_tags?.includes('speaker') ? 'speaker' : item?.kind_tags?.includes('asr') ? 'asr' : 'tts');
        if (kind === 'asr') return { ...prev, active_asr: engineId };
        if (kind === 'speaker') return { ...prev, active_speaker: engineId };
        return { ...prev, active: engineId };
      });
      toast('success', `云端当前引擎已切到 ${engineId}`);
      await loadEngines();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy('');
    }
  };

  const groups = useMemo(() => {
    const items = list?.items || [];
    const map: Record<string, typeof items> = { tts: [], asr: [], speaker: [] };
    items.forEach((it) => {
      const kind = it.kind || (it.kind_tags?.includes('speaker') ? 'speaker' : it.kind_tags?.includes('asr') ? 'asr' : 'tts');
      (map[kind] ||= []).push(it);
    });
    return map;
  }, [list]);

  const configured = host?.configured;

  return (
    <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
      {/* 主机档案 */}
      <div className="nested" style={{ display: 'grid', gap: 'var(--space-2)' }}>
        <div className="toolbar">
          <strong style={{ fontSize: 'var(--fs-13)' }}>
            <Cloud size={14} strokeWidth={1.75} /> 云端主机
          </strong>
          <div className="toolbar__spacer" />
          {configured ? (
            <>
              <span className="hint">
                {host?.username}@{host?.host}:{host?.ssh_port}
                {host?.has_password ? ' · 密码已加密保存' : ' · 未保存密码'}
              </span>
              <button
                type="button"
                className="btn btn--sm"
                disabled={busy === 'sync'}
                title="把「一键接入云主机 / 云端连接」里的主机、端口、用户名、项目目录同步过来"
                onClick={() => {
                  void (async () => {
                    setBusy('sync');
                    setError('');
                    try {
                      const res = await api.syncCloudHostFromAgent();
                      await loadHost();
                      if (res.updated?.length) {
                        toast('success', '已从接入配置同步', '更新：' + res.updated.join('、') + '（密码仍用本页已保存的）');
                        await loadEngines();
                      } else {
                        toast('info', '无需同步', res.message || '主机档案已有值');
                      }
                    } catch (exc) {
                      setError(exc instanceof Error ? exc.message : String(exc));
                    } finally {
                      setBusy('');
                    }
                  })();
                }}
              >
                {busy === 'sync' ? <Spinner size={12} /> : <Link2 size={13} strokeWidth={1.75} />} 从接入配置同步
              </button>
              <button type="button" className="btn btn--sm" onClick={() => setEditing((v) => !v)}>
                {editing ? '收起' : '修改'}
              </button>
              <button type="button" className="btn btn--sm" disabled={busy === 'test'} onClick={() => void testHost()}>
                {busy === 'test' ? <Spinner size={12} /> : <PlugZap size={13} strokeWidth={1.75} />} 测试连接
              </button>
              <button
                type="button"
                className="btn btn--sm"
                disabled={busy === 'list'}
                onClick={() => void loadEngines()}
              >
                {busy === 'list' ? <Spinner size={12} /> : <RefreshCw size={13} strokeWidth={1.75} />} 刷新列表
              </button>
            </>
          ) : (
            <span className="hint">还没配置：填好下面几项保存即可（也可先用「一键接入云主机」）</span>
          )}
        </div>

        {editing || !configured ? (
          <>
            {(() => {
              const src = host?.sources || {};
              const fromAgent = Object.values(src).some((v) => v === 'agent');
              const fromTunnel = Object.values(src).some((v) => v === 'tunnel');
              if (!fromAgent && !fromTunnel) return null;
              return (
                <p className="hint">
                  已自动填入：
                  {fromAgent ? '「一键接入云主机」上次成功的部署参数' : '「云端连接 → SSH 隧道」里的主机信息'}
                  ，确认无误后点「保存并连接」。
                </p>
              );
            })()}
            <div className="field">
              <label className="label" htmlFor="cloud-paste">
                粘贴登录命令（可选）
              </label>
              <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                <input
                  id="cloud-paste"
                  className="input"
                  placeholder="ssh -p 41425 root@connect.nmb1.seetacloud.com"
                  value={pasteText}
                  onChange={(e) => setPasteText(e.target.value)}
                />
                <button
                  type="button"
                  className="btn btn--sm"
                  disabled={!pasteText.trim()}
                  onClick={() => {
                    const parsed = parseSshCommand(pasteText);
                    if (!parsed.host) {
                      setError('没解析出主机地址，请检查这条命令');
                      return;
                    }
                    setForm((prev) => ({
                      ...prev,
                      host: parsed.host,
                      ssh_port: parsed.port || prev.ssh_port,
                      username: parsed.username || prev.username,
                      password: parsed.password || prev.password,
                    }));
                    setError('');
                  }}
                >
                  填入
                </button>
              </div>
              <p className="hint" style={{ marginTop: 4 }}>
                从云厂商控制台复制「登录指令」整行粘进来，点「填入」会自动识别主机、端口、用户名；
                密码仍需在下面那栏填（控制台里那条密码）。
              </p>
            </div>

            <div className="field-row">
              <div className="field">
                <label className="label" htmlFor="cloud-host">主机</label>
                <input
                  id="cloud-host"
                  className="input"
                  value={form.host}
                  placeholder="region-42.seetacloud.com"
                  onChange={(e) => setForm({ ...form, host: e.target.value })}
                />
              </div>
              <div className="field" style={{ maxWidth: 120 }}>
                <label className="label" htmlFor="cloud-port">SSH 端口</label>
                <input
                  id="cloud-port"
                  className="input num"
                  type="number"
                  value={form.ssh_port}
                  onChange={(e) => setForm({ ...form, ssh_port: Number(e.target.value) })}
                />
              </div>
              <div className="field">
                <label className="label" htmlFor="cloud-user">用户名</label>
                <input
                  id="cloud-user"
                  className="input"
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
                />
              </div>
              <div className="field">
                <label className="label" htmlFor="cloud-pass">密码</label>
                <input
                  id="cloud-pass"
                  className="input"
                  type="password"
                  value={form.password}
                  placeholder={host?.has_password ? '留空=沿用已保存的密码' : 'SSH 密码'}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                />
              </div>
            </div>
            <div className="field-row">
              <div className="field">
                <label className="label" htmlFor="cloud-project">云端项目目录</label>
                <input
                  id="cloud-project"
                  className="input"
                  value={form.project_dir}
                  onChange={(e) => setForm({ ...form, project_dir: e.target.value })}
                />
              </div>
              <div className="field">
                <label className="label" htmlFor="cloud-python">云端解释器</label>
                <input
                  id="cloud-python"
                  className="input"
                  value={form.python}
                  onChange={(e) => setForm({ ...form, python: e.target.value })}
                />
              </div>
            </div>
            <div className="toolbar">
              <button
                type="button"
                className="btn btn--sm btn--primary"
                disabled={busy === 'save' || !form.host.trim() || !form.username.trim()}
                onClick={() => void saveHost()}
              >
                {busy === 'save' ? <Spinner size={12} /> : null} 保存并连接
              </button>
              <span className="hint">密码用 Windows DPAPI 加密存到 data/cloud-host.json，界面不回显</span>
            </div>
          </>
        ) : null}

        {testResult ? (
          <p className="hint">
            连接正常：{testResult.python} · 指纹 <code>{String(testResult.fingerprint || '').slice(0, 34)}</code> ·
            项目目录{testResult.project_present ? '存在' : '未找到'}
          </p>
        ) : null}
        {error ? <p className="field-error">{error}</p> : null}
      </div>

      {/* 安装进度 */}
      {job ? (
        <div className="nested" style={{ display: 'grid', gap: 'var(--space-1)' }}>
          <div className="toolbar">
            <strong style={{ fontSize: 'var(--fs-13)' }}>云端安装：{job.engine_id}</strong>
            <div className="toolbar__spacer" />
            <span className="hint">
              {job.status === 'running' ? `${job.phase || '进行中'} ${Math.round((job.progress || 0) * 100)}%` : job.status === 'completed' ? '已完成' : '失败'}
            </span>
          </div>
          <ProgressBar value={job.progress || 0} />
          <div className="log-list" style={{ maxHeight: 140, overflowY: 'auto' }}>
            {(job.logs || []).slice(-12).map((line, i) => (
              <div className="log-line" key={i}>
                <span className="log-line__level log-line__level--info">info</span>
                <span>{line.message}</span>
              </div>
            ))}
          </div>
          {job.error ? <p className="field-error">{job.error}</p> : null}
        </div>
      ) : null}

      {/* 引擎列表 */}
      {!configured ? (
        <p className="hint">配置云端主机后这里会列出云端装了哪些模型（含手工直传上去的）。</p>
      ) : !list ? (
        busy === 'list' ? <Skeleton height={80} radius={12} /> : null
      ) : (
        <>
          <p className="hint">
            共 {list.items.length} 个云端引擎 · 云端当前引擎：<code>{list.active || '—'}</code>
            （这些模型在 {list.host || host?.host} 上，不占本机磁盘与显存）
          </p>
          {(['tts', 'asr', 'speaker'] as const).map((kind) =>
            (groups[kind] || []).length ? (
              <div key={kind} style={{ display: 'grid', gap: 'var(--space-1)' }}>
                <strong style={{ fontSize: 'var(--fs-13)' }}>{KIND_LABEL[kind]}</strong>
                {(groups[kind] || []).map((item) => {
                  const tone = statusTone(item.status);
                  // 当前引擎分三路：TTS 看 active，ASR 看 active_asr，声纹看 active_speaker
                  const active =
                    list.active === item.id ||
                    list.active_asr === item.id ||
                    list.active_speaker === item.id;
                  return (
                    <div
                      key={item.id}
                      className="nested"
                      style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', flexWrap: 'wrap' }}
                    >
                      <span className="chip" style={{ borderColor: tone.color, color: tone.color }}>
                        {tone.text}
                      </span>
                      <strong style={{ fontSize: 'var(--fs-13)' }}>{item.name}</strong>
                      <span className="hint num">{item.id}</span>
                      <span className="hint num">
                        {item.size_mb > 0 ? `${item.size_mb.toFixed(1)} MB` : '—'}
                      </span>
                      {active ? (
                        <span className="badge badge--success">
                          <Check size={11} strokeWidth={2} /> 当前
                        </span>
                      ) : null}
                      <div className="toolbar__spacer" />
                      {item.status === 'ready' ? (
                        <>
                          {!active ? (
                            <button
                              type="button"
                              className="btn btn--sm"
                              disabled={busy === `select:${item.id}`}
                              onClick={() => void selectEngine(item.id)}
                              title="把云端当前引擎切到这个"
                            >
                              设为当前
                            </button>
                          ) : null}
                          <button
                            type="button"
                            className="btn btn--sm btn--danger"
                            disabled={busy === `remove:${item.id}`}
                            onClick={() => void removeEngine(item.id)}
                            title="删除云端这个模型"
                          >
                            <Trash2 size={13} strokeWidth={1.75} /> 删除
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="btn btn--sm btn--primary"
                          disabled={job?.status === 'running'}
                          onClick={() => void startInstall(item.id)}
                        >
                          <Download size={13} strokeWidth={1.75} /> 安装
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : null,
          )}
        </>
      )}
    </div>
  );
}

export default CloudEnginesPanel;
