import { useId, useMemo, useState } from 'react';
import { ChevronDown, Code2, FileSearch, Lightbulb, SlidersHorizontal } from 'lucide-react';
import { categoryLabels, severityLabels, sourceLabels } from '../constants';
import type { Finding, FindingCategory, FindingSource, Severity } from '../types';
import { SeverityBadge } from './Badges';

type Filter<T> = 'all' | T;

export default function FindingsPanel({ findings }: { findings: Finding[] }) {
  const groupId = useId();
  const [severity, setSeverity] = useState<Filter<Severity>>('all');
  const [category, setCategory] = useState<Filter<FindingCategory>>('all');
  const [source, setSource] = useState<Filter<FindingSource>>('all');

  const filtered = useMemo(() => findings.filter((finding) =>
    (severity === 'all' || finding.severity === severity) &&
    (category === 'all' || finding.category === category) &&
    (source === 'all' || finding.source === source),
  ), [findings, severity, category, source]);

  function resetFilters() {
    setSeverity('all');
    setCategory('all');
    setSource('all');
  }

  return (
    <section className="results-section" aria-labelledby={`${groupId}-title`}>
      <div className="result-section-heading">
        <div><span className="section-icon"><FileSearch size={20} /></span><div><h2 id={`${groupId}-title`}>代码发现</h2><p>定位到文件与行号，展开证据后结合上下文人工复核。</p></div></div>
        <span className="count-pill" aria-live="polite">{filtered.length} / {findings.length} 项</span>
      </div>

      {findings.length > 0 && (
        <div className="filters" aria-label="发现筛选">
          <span className="filter-title"><SlidersHorizontal size={16} />筛选</span>
          <div className="filter-field"><label htmlFor={`${groupId}-severity`}>严重度</label><select id={`${groupId}-severity`} value={severity} onChange={(event) => setSeverity(event.target.value as Filter<Severity>)}><option value="all">全部严重度</option>{Object.entries(severityLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
          <div className="filter-field"><label htmlFor={`${groupId}-category`}>类别</label><select id={`${groupId}-category`} value={category} onChange={(event) => setCategory(event.target.value as Filter<FindingCategory>)}><option value="all">全部类别</option>{Object.entries(categoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
          <div className="filter-field"><label htmlFor={`${groupId}-source`}>来源</label><select id={`${groupId}-source`} value={source} onChange={(event) => setSource(event.target.value as Filter<FindingSource>)}><option value="all">全部来源</option><option value="static">静态扫描</option><option value="ai">AI 审查</option></select></div>
          {(severity !== 'all' || category !== 'all' || source !== 'all') && <button className="text-button clear-filters" type="button" onClick={resetFilters}>清除筛选</button>}
        </div>
      )}

      {findings.length === 0 ? (
        <div className="result-empty"><FileSearch size={27} /><h3>当前没有代码发现</h3><p>这不代表代码没有风险，请结合覆盖范围与跳过文件判断结果。</p></div>
      ) : filtered.length === 0 ? (
        <div className="result-empty"><SlidersHorizontal size={27} /><h3>没有符合筛选条件的发现</h3><button className="button button-secondary" type="button" onClick={resetFilters}>清除筛选</button></div>
      ) : (
        <div className="finding-list">
          {filtered.map((finding) => (
            <article className={`finding-card finding-${finding.severity}`} key={finding.id}>
              <div className="finding-topline"><div className="finding-badges"><SeverityBadge severity={finding.severity} /><span className="category-badge">{categoryLabels[finding.category]}</span><span className={`finding-source source-label-${finding.source}`}>{finding.source === 'ai' ? 'AI 判断' : '静态规则'}</span></div>{finding.severity === 'critical' && <span className="manual-review">需人工确认</span>}</div>
              <h3>{finding.title}</h3>
              <div className="file-location"><Code2 size={15} /><code>{finding.file_path}</code><span>第 {finding.line_start}{finding.line_end > finding.line_start ? `–${finding.line_end}` : ''} 行</span></div>
              <p className="finding-description">{finding.description}</p>
              <div className="suggestion"><Lightbulb size={18} /><div><strong>修复建议</strong><p>{finding.suggestion}</p></div></div>
              <details className="evidence">
                <summary><span><Code2 size={16} />查看代码证据</span><ChevronDown size={17} /></summary>
                <div className="evidence-body"><div><span>{finding.file_path}</span><span>L{finding.line_start}{finding.line_end > finding.line_start ? `–L${finding.line_end}` : ''}</span></div><pre tabIndex={0} aria-label={`${finding.file_path} 代码证据`}><code>{finding.evidence}</code></pre></div>
              </details>
              <div className="finding-origin">来源：{sourceLabels[finding.source]} · 编号 {finding.id}</div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
