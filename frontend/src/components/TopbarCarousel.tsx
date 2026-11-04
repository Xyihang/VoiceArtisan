import { useEffect, useMemo, useRef, useState } from 'react';
import { AudioLines } from 'lucide-react';

type Props = {
  /** 当前页面标题（如「工作台」） */
  title: string;
  /** 页面副标题 / 能力描述 */
  subtitle: string;
  /** 状态片段：模式、显存、云端详情（已去重的纯文本） */
  chips: string[];
  /** 路由变化时用它触发"翻到页面信息" */
  routeKey: string;
};

const PAGE_MS = 6000; // 页面信息停留
const BRAND_MS = 3200; // 品牌停留

/**
 * 顶栏轮播：品牌（声匠 ⟡ VoiceSmith）与「当前页面信息」交替出现。
 *
 * - 切页后先展示页面信息，6s 后翻到品牌，3.2s 再翻回来
 * - 鼠标悬停/键盘聚焦时暂停（不会在你正要读的时候翻走）
 * - `prefers-reduced-motion` 下只做淡入淡出，不做位移
 * - 页面头里的标题/副标题/状态都搬到这里，页面本体不再重复
 */
export function TopbarCarousel({ title, subtitle, chips, routeKey }: Props) {
  const [slide, setSlide] = useState<0 | 1>(1); // 1 = 页面信息，0 = 品牌
  const [paused, setPaused] = useState(false);
  const timerRef = useRef<number | null>(null);

  const prefersReduced = useMemo(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    [],
  );

  // 切页：立刻翻到页面信息，并从这一刻重新计时
  useEffect(() => {
    setSlide(1);
  }, [routeKey]);

  // 轮播定时器
  useEffect(() => {
    if (paused) return;
    if (timerRef.current) window.clearTimeout(timerRef.current);
    const delay = slide === 1 ? PAGE_MS : BRAND_MS;
    timerRef.current = window.setTimeout(() => {
      setSlide((s) => (s === 1 ? 0 : 1));
    }, delay);
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [slide, paused, routeKey]);

  return (
    <div
      className="topbar-carousel"
      data-paused={paused ? 'true' : 'false'}
      data-reduced={prefersReduced ? 'true' : 'false'}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={() => setPaused(false)}
      onClick={() => setSlide((s) => (s === 1 ? 0 : 1))}
      role="group"
      aria-label="品牌与当前页面信息（点击切换）"
    >
      {/* 品牌 */}
      <div className="topbar-slide topbar-slide--brand" data-active={slide === 0 ? 'true' : 'false'} aria-hidden={slide !== 0}>
        <span className="topbar-slide__name">声匠</span>
        <span className="topbar-slide__mark" aria-hidden="true">
          <AudioLines size={18} strokeWidth={2} />
        </span>
        <span className="topbar-slide__sub">VoiceSmith</span>
      </div>

      {/* 当前页面信息 */}
      <div className="topbar-slide topbar-slide--page" data-active={slide === 1 ? 'true' : 'false'} aria-hidden={slide !== 1}>
        <span className="topbar-slide__title">{title}</span>
        <span className="topbar-slide__desc" title={subtitle}>
          {subtitle}
        </span>
        {chips.length ? (
          <span className="topbar-slide__chips">
            {chips.map((text) => (
              <span className="topbar-chip" key={text}>
                {text}
              </span>
            ))}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export default TopbarCarousel;
