import React from 'react';

const GROUPS: { title: string; tags: string[] }[] = [
  { title: '性别 / 年龄', tags: ['年轻女性', '成熟女性', '年轻男性', '中年男性', '老年男性', '老年女性'] },
  { title: '语速 / 节奏', tags: ['语速稍慢', '语速偏快', '节奏平稳', '抑扬顿挫', '新闻播报节奏'] },
  { title: '情感', tags: ['温柔亲切', '热情有力', '冷静克制', '活泼俏皮', '悲伤低沉', '纪录片旁白'] },
  { title: '口音', tags: ['标准普通话', '轻微南方口音', '台湾腔', '港腔', '美式英语口音'] },
  { title: '音色质感', tags: ['磁性低沉', '清亮通透', '沙哑质感', '气声柔软', '播音腔'] },
];

type Props = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
};

export function DesignInput({ value, onChange, disabled }: Props) {
  const appendTag = (tag: string) => {
    if (value.includes(tag)) return;
    onChange(value ? `${value.replace(/[，,。]\s*$/, '')}，${tag}` : tag);
  };

  return (
    <div className="field">
      <label className="label" htmlFor="design-desc">
        音色描述
        <span className="label__hint">用自然语言描述想要的音色：性别、年龄、口音、语速、情感、质感</span>
      </label>
      <textarea
        id="design-desc"
        className="textarea textarea--compact"
        value={value}
        disabled={disabled}
        placeholder="例如：一位三十岁左右的女性，声音温柔但有力量，普通话标准，语速稍慢，适合情感类旁白。"
        onChange={(event) => onChange(event.target.value)}
        spellCheck={false}
      />
      <div style={{ display: 'grid', gap: 'var(--space-2)' }}>
        {GROUPS.map((group) => (
          <div key={group.title} className="toolbar" style={{ gap: 'var(--space-1)' }}>
            <span className="hint" style={{ minWidth: 72 }}>
              {group.title}
            </span>
            {group.tags.map((tag) => (
              <button
                key={tag}
                type="button"
                className="chip chip--button"
                aria-pressed={value.includes(tag)}
                onClick={() => appendTag(tag)}
                disabled={disabled}
              >
                {tag}
              </button>
            ))}
          </div>
        ))}
      </div>
      <p className="hint">
        提示：描述越具体，音色越稳定；建议一次只突出 2-3 个关键特征（如「年轻女性 + 温柔 + 语速稍慢」）。
      </p>
    </div>
  );
}
