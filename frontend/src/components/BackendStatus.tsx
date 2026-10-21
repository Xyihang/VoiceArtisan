import React from 'react';
import { useApp } from '../state/AppContext';

function labelFor(backend: string): { text: string; tone: string; desc: string } {
  switch (backend) {
    case 'cloud':
      return { text: '云端', tone: 'ok', desc: '使用云端 GPU 推理' };
    case 'cloud-offline':
      return { text: '云端不可达', tone: 'warn', desc: '云端连接失败，可在设置中切换本地或检查隧道' };
    case 'local':
      return { text: '本地', tone: 'ok', desc: '使用本地 GPU 推理' };
    case 'local-offline':
      return { text: '本地待加载', tone: 'warn', desc: '本地模型尚未加载到显存' };
    case 'mock':
      return { text: '模拟引擎', tone: 'warn', desc: '当前为联调占位引擎，非真实模型' };
    case 'offline':
      return { text: '服务离线', tone: 'error', desc: '无法连接后端服务，请确认服务已启动' };
    default:
      return { text: '检测中', tone: 'error', desc: '' };
  }
}

/** 顶栏后端状态：颜色 + 文字双编码，云端显示实时网络延迟 */
export function BackendStatus() {
  const { health, live } = useApp();
  const backend = live ? health?.active_backend || 'unknown' : 'offline';
  const info = labelFor(backend);
  const latency = backend === 'cloud' ? health?.cloud?.latency_ms ?? null : null;
  const loaded = Object.keys(health?.local?.loaded_models || {}).length;
  const cpuMode = !!health?.cpu_mode;

  return (
    <span className="chip" title={cpuMode ? '当前用 CPU 推理（无 GPU 加速，速度约慢 25 倍）' : info.desc} aria-live="polite">
      <i className={`status-dot status-dot--${cpuMode ? 'warn' : info.tone}`} aria-hidden="true" />
      <span style={{ color: 'var(--color-text)' }}>{info.text}</span>
      {cpuMode ? <span style={{ color: 'var(--color-warning)' }}>CPU</span> : null}
      {latency != null ? (
        <span className="num" style={{ color: 'var(--color-text-muted)' }} title="云端网络延迟">
          {Math.round(latency)}ms
        </span>
      ) : null}
      {backend === 'local' && loaded > 0 ? (
        <span className="num" style={{ color: 'var(--color-text-muted)' }} title="已加载模型数">
          {loaded} 模型
        </span>
      ) : null}
    </span>
  );
}
