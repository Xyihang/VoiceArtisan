import { useEffect, useLayoutEffect, useRef, useState } from 'react';

type Props = {
  /** 当前路由在导航里的下标（决定滑动方向与距离） */
  index: number;
  /** 页面显示名，用于中间骨架 */
  labels: string[];
  /** 子节点 = 真正的新页面（React 渲染） */
  children: React.ReactNode;
  /** 每跨一档的时长（ms） */
  stepMs?: number;
  /** 切页前由 App 抓的旧页快照（HTML + 来源下标） */
  frozen?: { html: string; from: number } | null;
  /** 快照已被消费，App 可以清掉 */
  onFrozenConsumed?: () => void;
};

const GAP = 24;

/**
 * 方案 2：跨页「轨道连续滑动」。
 *
 * - 从旧页到新页之间的每一页都排进一条横向轨道，整体平移；
 * - 只有「起点页」和「目标页」渲染真实内容，中间页是骨架（省接口请求）；
 * - 结束时不重建 DOM（避免动画落地那一帧掉帧）：只清掉多余槽位、把 transform 归零。
 * - 目标页始终是同一个 React 节点（key="live"），不会因为槽位变化被卸载重挂。
 */
export function PageSlide({ index, labels, children, stepMs = 140, frozen, onFrozenConsumed }: Props) {
  const [slots, setSlots] = useState<{ fromHTML: string; from: number; to: number; slotW: number } | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const lastIndex = useRef(index);


  // 路由变化：抓一份旧页的快照，拼出本次要滑过的区间
  useLayoutEffect(() => {
    const host = hostRef.current;
    const prev = lastIndex.current;
    if (prev === index) return;
    lastIndex.current = index;
    if (!host) return;

    // 旧页快照由 App 在「切页之前」抓取，这里直接消费
    const html = frozen && frozen.from === prev ? frozen.html : '';
    if (html) {
      setSlots({ fromHTML: html, from: prev, to: index, slotW: host.clientWidth || 0 });
    }
    onFrozenConsumed?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index]);

  // 开始滑动：命令式地先"瞬移"到起点（无过渡），再下一帧带过渡走向终点。
  // 若用 state 驱动起点，浏览器会从上一帧的 0 开始过渡过去，方向就错了。
  useEffect(() => {
    if (!slots) return;
    const host = hostRef.current;
    const track = trackRef.current;
    if (!host || !track) return;

    const slotW = host.clientWidth;
    const min = Math.min(slots.from, slots.to);
    const startX = -(slots.from - min) * (slotW + GAP);
    const endX = -(slots.to - min) * (slotW + GAP);
    const steps = Math.abs(slots.to - slots.from);
    const dur = Math.min(720, 180 + steps * stepMs);

    // 1) 先摆到起点（关掉过渡）
    track.style.transition = 'none';
    track.style.transform = `translate3d(${startX}px, 0, 0)`;
    void track.offsetWidth; // 强制回流，确保起点已生效

    // 2) 下一帧带过渡滑到终点
    let raf = 0;
    raf = requestAnimationFrame(() => {
      track.style.transition = `transform ${dur}ms cubic-bezier(.4,.05,.5,.95)`;
      track.style.transform = `translate3d(${endX}px, 0, 0)`;
    });

    // 3) 收尾：不重建 DOM，只清掉多余槽位（目标页正好占满视口）
    const timer = window.setTimeout(() => {
      track.style.transition = 'none';
      track.style.transform = 'translate3d(0, 0, 0)';
      track.style.width = 'auto';
      setSlots(null);
    }, dur + 60);

    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(timer);
    };
  }, [slots, stepMs]);

  const min = slots ? Math.min(slots.from, slots.to) : index;
  const max = slots ? Math.max(slots.from, slots.to) : index;

  return (
    <div className="page-stage" ref={hostRef}>
      <div
        className="page-track"
        ref={trackRef}
        style={
          slots
            ? { width: (max - min + 1) * slots.slotW + (max - min) * GAP, columnGap: GAP }
            : undefined
        }
      >
        {slots
          ? Array.from({ length: max - min + 1 }, (_, i) => {
              const pageIndex = min + i;
              const key = `slot-${pageIndex}`;
              if (pageIndex === slots.to) {
                return (
                  <div className="page-slot" key="live" data-page-live="" style={{ width: slots.slotW }}>
                    {children}
                  </div>
                );
              }
              if (pageIndex === slots.from) {
                return (
                  <div
                    className="page-slot"
                    key={key}
                    style={{ width: slots.slotW }}
                    dangerouslySetInnerHTML={{ __html: slots.fromHTML }}
                  />
                );
              }
              return (
                <div className="page-slot page-slot--ghost" key={key} style={{ width: slots.slotW }}>
                  <div className="page">
                    <section className="card">
                      <div className="card__head">
                        <span className="card__title">{labels[pageIndex] || ''}</span>
                      </div>
                      <div className="card__body">
                        <div className="ghost-line" style={{ width: '42%' }} />
                        <div className="ghost-line" style={{ width: '78%' }} />
                        <div className="ghost-line" style={{ width: '64%' }} />
                        <div className="ghost-line" style={{ width: '71%' }} />
                      </div>
                    </section>
                  </div>
                </div>
              );
            })
          : (
              <div className="page-slot" key="live" data-page-live="">
                {children}
              </div>
            )}
      </div>
    </div>
  );
}

export default PageSlide;
