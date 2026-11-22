import { BarChart3, ChevronDown, FileWarning } from 'lucide-react';
import type { Coverage } from '../types';

export default function CoveragePanel({ coverage }: { coverage?: Coverage }) {
  if (!coverage) {
    return <section className="panel coverage-panel"><div className="result-section-heading compact"><div><span className="section-icon"><BarChart3 size={20} /></span><div><h2>扫描覆盖范围</h2><p>覆盖信息尚未生成，不能据此推断代码已完成全量审查。</p></div></div></div></section>;
  }

  const total = coverage.total_files;
  const analyzed = coverage.analyzed_files;
  const eligible = coverage.eligible_files || total;
  const staticScanned = coverage.static_scanned_files || 0;
  const aiPercentage = eligible > 0 ? Math.round((analyzed / eligible) * 100) : 0;
  const staticPercentage = eligible > 0 ? Math.round((staticScanned / eligible) * 100) : 0;
  const languages = Object.entries(coverage.languages || {}).sort((a, b) => b[1] - a[1]);
  const languageTotal = languages.reduce((sum, [, count]) => sum + count, 0);

  return (
    <section className="panel coverage-panel" aria-labelledby="coverage-title">
      <div className="result-section-heading compact"><div><span className="section-icon"><BarChart3 size={20} /></span><div><h2 id="coverage-title">扫描覆盖范围</h2><p>静态规则扫描覆盖全部可审查文件；AI 深度分析受请求预算限制，按源码优先抽样。</p></div></div></div>
      <div className="coverage-grid">
        <div className="coverage-overview">
          <div className="coverage-bars">
            <div className="coverage-bar">
              <div className="coverage-bar-head"><span>静态规则扫描</span><strong>{eligible > 0 ? `${staticPercentage}%` : '—'}</strong></div>
              <div className="coverage-track coverage-track-static" role="progressbar" aria-label="静态规则扫描覆盖率" aria-valuemin={0} aria-valuemax={100} aria-valuenow={staticPercentage}><span style={{ width: `${staticPercentage}%` }} /></div>
              <small>{staticScanned} / {eligible} 个可审查文件，全部经过安全与质量规则扫描</small>
            </div>
            <div className="coverage-bar">
              <div className="coverage-bar-head"><span>AI 深度分析</span><strong>{eligible > 0 ? `${aiPercentage}%` : '—'}</strong></div>
              <div className="coverage-track coverage-track-ai" role="progressbar" aria-label="AI 深度分析覆盖率" aria-valuemin={0} aria-valuemax={100} aria-valuenow={aiPercentage}><span style={{ width: `${aiPercentage}%` }} /></div>
              <small>{analyzed} / {eligible} 个文件，受单任务 AI 请求预算限制</small>
            </div>
          </div>
          <div className="coverage-numbers"><div><strong>{analyzed}</strong><span>AI 分析文件</span></div><div><strong>{eligible}</strong><span>可审查源码</span></div><div><strong>{staticScanned}</strong><span>静态扫描文件</span></div><div><strong>{coverage.skipped_files.length}</strong><span>跳过条目</span></div></div>
        </div>
        <div className="language-summary">
          <h3>语言分布</h3>
          {languages.length === 0 ? <p className="muted">未识别到可分析语言。</p> : languages.map(([language, count]) => {
            const width = languageTotal > 0 ? Math.max(3, Math.round((count / languageTotal) * 100)) : 0;
            return <div className="language-row" key={language}><div><span>{language}</span><span>{count} 个文件</span></div><div className="language-track" aria-hidden="true"><span style={{ width: `${width}%` }} /></div></div>;
          })}
        </div>
      </div>
      {coverage.skipped_files.length > 0 && (
        <details className="skipped-files">
          <summary><span><FileWarning size={17} />查看 {coverage.skipped_files.length} 个跳过或未覆盖文件</span><ChevronDown size={17} /></summary>
          <ul>{coverage.skipped_files.map((item, index) => <li key={`${item.path}-${index}`}><code>{item.path}</code><span>{item.reason}</span></li>)}</ul>
        </details>
      )}
    </section>
  );
}
