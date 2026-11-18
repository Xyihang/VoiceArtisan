import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import { base64ToArrayBuffer, type AudioEngine } from '../lib/audio';
import { WsClient } from '../lib/ws';
import type { ChunkEvent, Task, WsEvent } from '../types';

type StreamState = {
  task: Task | null;
  firstPacketMs: number | null;
  streaming: boolean;
  chunkCount: number;
  notice: string | null;
  error: { code: string; message: string; hint: string } | null;
};

const INITIAL: StreamState = {
  task: null,
  firstPacketMs: null,
  streaming: false,
  chunkCount: 0,
  notice: null,
  error: null,
};

/** 订阅单个任务的流式音频与状态（WebSocket 推送，不轮询） */
export function useTaskStream(engine: AudioEngine) {
  const [state, setState] = useState<StreamState>(INITIAL);
  const clientRef = useRef<WsClient | null>(null);
  const startRef = useRef<number>(0);
  const firstPacketRef = useRef<number | null>(null);
  const playingRef = useRef(false);

  const close = useCallback(() => {
    clientRef.current?.close();
    clientRef.current = null;
  }, []);

  useEffect(() => () => close(), [close]);

  const handle = useCallback(
    async (event: WsEvent) => {
      switch (event.event) {
        case 'task.snapshot':
        case 'task.update': {
          const task = event.data as Task;
          const stillRunning = task.status === 'queued' || task.status === 'running';
          setState((prev) => ({ ...prev, task: { ...prev.task, ...task }, streaming: stillRunning }));
          return;
        }
        case 'task.chunk': {
          const data = event.data as ChunkEvent;
          try {
            await engine.appendWav(base64ToArrayBuffer(data.wav_b64));
          } catch {
            return;
          }
          if (firstPacketRef.current == null) {
            firstPacketRef.current = Math.round(performance.now() - startRef.current);
          }
          // 只缓冲、不自动播放：生成完成后由用户点播放键（首包即播会打断用户当前在做的事）
          setState((prev) => ({
            ...prev,
            firstPacketMs: firstPacketRef.current,
            chunkCount: prev.chunkCount + 1,
          }));
          return;
        }
        case 'task.first_packet': {
          const data = event.data as { first_packet_ms: number };
          if (firstPacketRef.current == null) {
            firstPacketRef.current = data.first_packet_ms;
            setState((prev) => ({ ...prev, firstPacketMs: data.first_packet_ms }));
          }
          return;
        }
        case 'task.notice': {
          setState((prev) => ({ ...prev, notice: (event.data as { message: string }).message }));
          return;
        }
        case 'task.final': {
          const payload = event.data as { error?: { code: string; message: string; hint: string } };
          const taskId = (event.topic || '').replace('task.', '');
          let fresh: Task | null = null;
          if (taskId) {
            try {
              fresh = await api.getTask(taskId);
            } catch {
              fresh = null;
            }
          }
          playingRef.current = false;
          setState((prev) => ({
            ...prev,
            task: fresh ?? prev.task,
            streaming: false,
            error: payload.error ?? null,
          }));
          return;
        }
        default:
          return;
      }
    },
    [engine],
  );

  const start = useCallback(
    (taskId: string) => {
      close();
      engine.reset();
      playingRef.current = false;
      firstPacketRef.current = null;
      startRef.current = performance.now();
      setState({ ...INITIAL, streaming: true });
      const client = new WsClient(`/ws/tasks/${taskId}`);
      clientRef.current = client;
      client.onMessage((event) => {
        void handle(event);
      });
    },
    [close, engine, handle],
  );

  return { ...state, start, close };
}
