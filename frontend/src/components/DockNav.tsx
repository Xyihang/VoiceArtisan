import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { LucideIcon } from 'lucide-react';

export type DockRoute = { id: string; label: string; icon: LucideIcon };

type Props = {
  routes: DockRoute[];
  current: string;
  onNavigate: (id: string) => void;
  activeTasks?: number;
  onOpenSearch: () => void;
};

const SVG_NS = 'http://www.w3.org/2000/svg';
const FILTER_ID = 'qwentts-dock-liquid';
const MAP_ID = 'qwentts-dock-liquid-map';
const LIFT_W = 1.34;
const LIFT_H = 1.7;

/** 圆角矩形 SDF（与 shuding/liquid-glass 同款写法），返回 0 表示正好在边上 */
function roundedRectSDF(x: number, y: number, halfW: number, halfH: number, radius: number): number {
  const qx = Math.abs(x) - halfW + radius;
  const qy = Math.abs(y) - halfH + radius;
  const mx = Math.max(qx, 0);
  const my = Math.max(qy, 0);
  return Math.min(Math.max(qx, qy), 0) + Math.sqrt(mx * mx + my * my) - radius;
}

function smoothStep(a: number, b: number, t: number): number {
  const x = Math.max(0, Math.min(1, (t - a) / (b - a)));
  return x * x * (3 - 2 * x);
}

/**
 * 底部液态玻璃导航条。
 *
 * 设计要点（每一条都是踩过坑的）：
 * - 胶囊用 CSS 过渡滑动（不是物理弹簧），跨 tab 时是连续位移，不会瞬移；
 * - 胶囊 z-index 高于 tab：否则"透过玻璃看到的折射副本"与"原始文字"会叠成重影；
 * - 只有按住胶囊才能拖（pointerdown 命中测试），点 tab 走 click，两者互不干扰；
 * - 折射用 SVG 位移贴图，只在贴边一条窄带里扭曲背景，中心不动；
 * - 拖动过程中不重建贴图（尺寸没变），避免掉帧。
 */
export function DockNav({ routes, current, onNavigate, activeTasks, onOpenSearch }: Props) {
  const dockRef = useRef<HTMLDivElement | null>(null);
  const tabsRef = useRef<HTMLDivElement | null>(null);
  const pillRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const feImageRef = useRef<SVGFEImageElement | null>(null);
  const feDispRef = useRef<SVGFEDisplacementMapElement | null>(null);
  const filterRef = useRef<SVGFilterElement | null>(null);

  const [lifted, setLifted] = useState(false);
  const [centerX, setCenterX] = useState(0);
  const [immediate, setImmediate] = useState(true);
  const mapRef = useRef({ w: 0, h: 0, strength: 0 });
  const dragRef = useRef({ armed: false, dragging: false, startX: 0, startCenter: 0 });

  const activeIndex = Math.max(0, routes.findIndex((r) => r.id === current));

  // ---------------- SVG 滤镜（只挂一次） ----------------
  useEffect(() => {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('width', '0');
    svg.setAttribute('height', '0');
    svg.setAttribute('aria-hidden', 'true');
    svg.style.cssText = 'position:absolute;width:0;height:0;pointer-events:none;';

    const defs = document.createElementNS(SVG_NS, 'defs');
    const filter = document.createElementNS(SVG_NS, 'filter');
    filter.setAttribute('id', FILTER_ID);
    filter.setAttribute('filterUnits', 'userSpaceOnUse');
    filter.setAttribute('colorInterpolationFilters', 'sRGB');

    const feImage = document.createElementNS(SVG_NS, 'feImage');
    feImage.setAttribute('id', MAP_ID);
    const feDisp = document.createElementNS(SVG_NS, 'feDisplacementMap');
    feDisp.setAttribute('in', 'SourceGraphic');
    feDisp.setAttribute('in2', MAP_ID);
    feDisp.setAttribute('xChannelSelector', 'R');
    feDisp.setAttribute('yChannelSelector', 'G');

    filter.appendChild(feImage);
    filter.appendChild(feDisp);
    defs.appendChild(filter);
    svg.appendChild(defs);
    document.body.appendChild(svg);

    filterRef.current = filter;
    feImageRef.current = feImage;
    feDispRef.current = feDisp;
    const canvas = document.createElement('canvas');
    canvas.style.display = 'none';
    canvasRef.current = canvas;

    return () => {
      svg.remove();
      filterRef.current = null;
      feImageRef.current = null;
      feDispRef.current = null;
      canvasRef.current = null;
    };
  }, []);

  /** 生成位移贴图：band 越小越"只有边缘在折射" */
  const buildMap = (w: number, h: number, band: number, strength: number) => {
    const canvas = canvasRef.current;
    const feImage = feImageRef.current;
    const feDisp = feDispRef.current;
    const filter = filterRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !feImage || !feDisp || !filter || !ctx) return;

    const mapW = Math.max(2, Math.round(w));
    const mapH = Math.max(2, Math.round(h));
    canvas.width = mapW;
    canvas.height = mapH;
    const data = new Uint8ClampedArray(mapW * mapH * 4);
    const raw: number[] = [];
    let maxScale = 0;
    for (let y = 0; y < mapH; y += 1) {
      for (let x = 0; x < mapW; x += 1) {
        const ux = x / mapW - 0.5;
        const uy = y / mapH - 0.5;
        const d = roundedRectSDF(ux, uy, 0.5, 0.5, 0.5) + 0.5;
        const k = smoothStep(band, 0, d);
        const dx = ux * k * strength * 2;
        const dy = uy * k * strength * 2;
        maxScale = Math.max(maxScale, Math.abs(dx), Math.abs(dy));
        raw.push(dx, dy);
      }
    }
    maxScale = maxScale || 1;
    let idx = 0;
    for (let i = 0; i < data.length; i += 4) {
      data[i] = (raw[idx] / maxScale) * 127.5 + 127.5;
      idx += 1;
      data[i + 1] = (raw[idx] / maxScale) * 127.5 + 127.5;
      idx += 1;
      data[i + 2] = 0;
      data[i + 3] = 255;
    }
    ctx.putImageData(new ImageData(data, mapW, mapH), 0, 0);
    feImage.setAttributeNS('http://www.w3.org/1999/xlink', 'href', canvas.toDataURL());
    feDisp.setAttribute('scale', String(maxScale));
    filter.setAttribute('x', '0');
    filter.setAttribute('y', '0');
    filter.setAttribute('width', String(mapW));
    filter.setAttribute('height', String(mapH));
    mapRef.current = { w: mapW, h: mapH, strength };
  };

  /** 位置换算：所有坐标都相对 .docknav，且把 tabs 的左偏移算进去 */
  const metrics = () => {
    const dock = dockRef.current;
    const tabsEl = tabsRef.current;
    if (!dock || !tabsEl) return null;
    const tabNodes = Array.from(tabsEl.querySelectorAll<HTMLElement>('[data-dock-tab]'));
    const active = tabNodes[activeIndex] || tabNodes[0];
    if (!active) return null;
    const tabsLeft = tabsEl.offsetLeft;
    const centers = tabNodes.map((n) => tabsLeft + n.offsetLeft + n.offsetWidth / 2);
    return {
      dockH: dock.clientHeight,
      tabsLeft,
      tabsWidth: tabsEl.offsetWidth,
      tabWidth: active.offsetWidth,
      center: centers[activeIndex] || centers[0] || 0,
      centers,
    };
  };

  // ---------------- 初始定位 / 路由变化 ----------------
  useLayoutEffect(() => {
    const m = metrics();
    if (!m) return;
    setImmediate(true);
    setCenterX(m.center);
    // 下一帧恢复过渡，保证后续点击有动画
    requestAnimationFrame(() => setImmediate(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const m = metrics();
    if (!m) return;
    setCenterX(m.center);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  useEffect(() => {
    const onResize = () => {
      const m = metrics();
      if (!m) return;
      setImmediate(true);
      setCenterX(m.center);
      requestAnimationFrame(() => setImmediate(false));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------- 胶囊尺寸 / 折射贴图 ----------------
  const sizes = useMemo(() => {
    const m = metrics();
    if (!m) return null;
    const baseW = m.tabWidth - 4;
    const baseH = m.dockH - 14;
    const w = baseW * (lifted ? LIFT_W : 1);
    const h = baseH * (lifted ? LIFT_H : 1);
    return { w, h, y: m.dockH / 2, tabsLeft: m.tabsLeft, tabsWidth: m.tabsWidth, baseW };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lifted, centerX, routes.length, current]);

  useEffect(() => {
    if (!sizes) return;
    const strength = lifted ? 9 : 3.2;
    const { w, h } = sizes;
    if (
      Math.round(w) !== mapRef.current.w ||
      Math.round(h) !== mapRef.current.h ||
      strength !== mapRef.current.strength
    ) {
      buildMap(w, h, 0.18, strength);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sizes, lifted]);

  // ---------------- 拖拽（只有按住胶囊才行） ----------------
  const pointOnPill = (clientX: number, clientY: number) => {
    const pill = pillRef.current;
    if (!pill) return false;
    const r = pill.getBoundingClientRect();
    const pad = 4;
    return (
      clientX >= r.left - pad && clientX <= r.right + pad && clientY >= r.top - pad && clientY <= r.bottom + pad
    );
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (!pointOnPill(e.clientX, e.clientY)) return;
    const dock = dockRef.current;
    const m = metrics();
    if (!dock || !m) return;
    dragRef.current = {
      armed: true,
      dragging: false,
      startX: e.clientX - dock.getBoundingClientRect().left,
      startCenter: centerX,
    };
    dock.setPointerCapture(e.pointerId);
    setLifted(true);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const dock = dockRef.current;
    const m = metrics();
    if (!dock || !m) return;
    const state = dragRef.current;
    if (!state.armed) return;
    const x = e.clientX - dock.getBoundingClientRect().left;
    if (!state.dragging && Math.abs(x - state.startX) < 4) return;
    if (!state.dragging) {
      state.dragging = true;
      dock.dataset.drag = 'true';
    }
    const lo = m.tabsLeft + 30;
    const hi = m.tabsLeft + m.tabsWidth - 30;
    const next = Math.max(lo, Math.min(hi, state.startCenter + (x - state.startX)));
    setCenterX(next);
    // 拖到哪一格就切到哪一格（松手只做吸附）
    let best = 0;
    let bd = Infinity;
    m.centers.forEach((c, i) => {
      const d = Math.abs(c - next);
      if (d < bd) {
        bd = d;
        best = i;
      }
    });
    const target = routes[best];
    if (target && target.id !== current) onNavigate(target.id);
  };

  const endDrag = () => {
    const dock = dockRef.current;
    const state = dragRef.current;
    if (dock) dock.dataset.drag = 'false';
    const wasDragging = state.dragging;
    dragRef.current = { armed: false, dragging: false, startX: 0, startCenter: 0 };
    setLifted(false);
    if (!wasDragging) return;
    const m = metrics();
    if (!m) return;
    let best = 0;
    let bd = Infinity;
    m.centers.forEach((c, i) => {
      const d = Math.abs(c - centerX);
      if (d < bd) {
        bd = d;
        best = i;
      }
    });
    setCenterX(m.centers[best] ?? m.center);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const delta = e.key === 'ArrowRight' ? 1 : -1;
    const next = routes[(activeIndex + delta + routes.length) % routes.length];
    if (next) onNavigate(next.id);
  };

  const left = sizes ? Math.max(sizes.tabsLeft + sizes.baseW / 2, Math.min(sizes.tabsLeft + sizes.tabsWidth - sizes.baseW / 2, centerX)) : 0;
  const pillStyle = sizes
    ? {
        width: `${sizes.w}px`,
        height: `${sizes.h}px`,
        transform: `translate(${left - sizes.w / 2}px, ${sizes.y - sizes.h / 2}px)`,
        transition: immediate ? 'none' : undefined,
      }
    : undefined;

  return (
    <div className="docknav-zone">
      <nav
        className="docknav"
        role="tablist"
        aria-label="主导航"
        ref={dockRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={onKeyDown}
      >
        <div className={`docknav__pill${lifted ? ' docknav__pill--lift' : ''}`} aria-hidden="true" ref={pillRef} style={pillStyle} />
        <div className="docknav__tabs" ref={tabsRef}>
          {routes.map((item) => {
            const Icon = item.icon;
            const active = item.id === current;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                data-dock-tab=""
                aria-selected={active}
                className="docknav__tab"
                onClick={() => onNavigate(item.id)}
              >
                <Icon size={22} strokeWidth={1.9} />
                <i>{item.label}</i>
                {item.id === 'tasks' && activeTasks ? <span className="docknav__badge">{activeTasks}</span> : null}
              </button>
            );
          })}
        </div>
      </nav>
      <button type="button" className="docknav__fab" onClick={onOpenSearch} aria-label="搜索（Ctrl K）" title="搜索（Ctrl K）">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="M20 20l-3.5-3.5" />
        </svg>
      </button>
    </div>
  );
}

export default DockNav;
