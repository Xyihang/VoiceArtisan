import type { WsEvent } from '../types';

type Handler = (event: WsEvent) => void;

function wsUrl(path: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${path}`;
}

/** 自动重连的 WebSocket 客户端（事件驱动，前端不轮询）。 */
export class WsClient {
  private path: string;
  private socket: WebSocket | null = null;
  private handlers = new Set<Handler>();
  private statusHandlers = new Set<(online: boolean) => void>();
  private retry = 0;
  private timer: number | null = null;
  private closed = false;

  constructor(path: string) {
    this.path = path;
    this.connect();
  }

  private connect() {
    if (this.closed) return;
    const socket = new WebSocket(wsUrl(this.path));
    this.socket = socket;
    socket.onopen = () => {
      this.retry = 0;
      this.statusHandlers.forEach((h) => h(true));
    };
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as WsEvent;
        if (payload.event === 'ping') return;
        this.handlers.forEach((h) => h(payload));
      } catch {
        /* 忽略非 JSON 帧 */
      }
    };
    socket.onclose = () => {
      this.statusHandlers.forEach((h) => h(false));
      if (this.closed) return;
      const delay = Math.min(5000, 400 * 2 ** this.retry);
      this.retry += 1;
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
    socket.onerror = () => socket.close();
  }

  onMessage(handler: Handler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  onStatus(handler: (online: boolean) => void): () => void {
    this.statusHandlers.add(handler);
    return () => this.statusHandlers.delete(handler);
  }

  close() {
    this.closed = true;
    if (this.timer) window.clearTimeout(this.timer);
    this.socket?.close();
  }
}
