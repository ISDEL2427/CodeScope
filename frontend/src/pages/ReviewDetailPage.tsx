import { useEffect, useState } from 'react';
import { AlertCircle, AlertTriangle, ArrowLeft, Braces, CalendarDays, CheckCircle2, Download, FileArchive, FileJson, FileText, Github, KeyRound, Layers3, ScanSearch, ShieldAlert, Sparkles } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { cancelReview, continueReview, downloadReport, errorMessage, finishReview, getAISettings, getBaseline, getReview } from '../api';
import { formatDate, isActive } from '../constants';
import { StatusBadge } from '../components/Badges';
import BaselinePanel from '../components/BaselinePanel';
import CoveragePanel from '../components/CoveragePanel';
import FindingsPanel from '../components/FindingsPanel';
import ProgressPanel from '../components/ProgressPanel';
import VulnerabilitiesPanel from '../components/VulnerabilitiesPanel';
import { ErrorState, LoadingState } from '../components/PageState';
import type { AISettings, BaselineResponse, Review } from '../types';

export default function ReviewDetailPage() {
  const { id = '' } = useParams();
  const [review, setReview] = useState<Review | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retryKey, setRetryKey] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState('');
  const [downloading, setDownloading] = useState<'json' | 'markdown' | null>(null);
  const [downloadError, setDownloadError] = useState('');
  const [baseline, setBaseline] = useState<BaselineResponse | null>(null);
  const [decisionBusy, setDecisionBusy] = useState<'continue' | 'finish' | null>(null);
  const [decisionError, setDecisionError] = useState('');
  const [aiSettings, setAiSettings] = useState<AISettings | null>(null);
  const [continueBase, setContinueBase] = useState('');
  const [continueKey, setContinueKey] = useState('');
  const [continueModel, setContinueModel] = useState('');

  useEffect(() => {
    getAISettings().then(setAiSettings).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!review?.awaiting_decision) return;
    setContinueBase(review.ai_base_url || '');
    setContinueModel(review.ai_model || '');
    setContinueKey('');
  }, [review?.id, review?.awaiting_decision, review?.ai_base_url, review?.ai_model]);

  useEffect(() => {
    let disposed = false;
    let timer: number | undefined;
    setLoading(true);
    setReview(null);
    setError('');

    async function load() {
      try {
        const data = await getReview(id);
        if (disposed) return;
        setReview(data);
        setError('');
        if (isActive(data.status)) timer = window.setTimeout(() => { void load(); }, 1000);
      } catch (requestError) {
        if (!disposed) setError(errorMessage(requestError));
      } finally {
        if (!disposed) setLoading(false);
      }
    }
    void load();
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [id, retryKey]);

  useEffect(() => {
    if (review?.status !== 'completed') {
      setBaseline(null);
      return;
    }
    let disposed = false;
    setBaseline(null);
    getBaseline(review.id)
      .then((data) => { if (!disposed) setBaseline(data); })
      .catch(() => { if (!disposed) setBaseline({ baseline: null, message: '历史对比暂时不可用，请稍后重试。' }); });
    return () => { disposed = true; };
  }, [review?.id, review?.status, review?.updated_at]);

  async function handleCancel() {
    setCancelling(true);
    setCancelError('');
    try {
      const updated = await cancelReview(id);
      setReview(updated);
    } catch (requestError) {
      setCancelError(errorMessage(requestError));
    } finally {
      setCancelling(false);
    }
  }

  async function handleDownload(format: 'json' | 'markdown') {
    setDownloading(format);
    setDownloadError('');
    try {
      await downloadReport(id, format);
    } catch (requestError) {
      setDownloadError(errorMessage(requestError));
    } finally {
      setDownloading(null);
    }
  }

  async function handleContinue() {
    if (!review) return;
    const resolvedBase = (continueBase || review.ai_base_url || aiSettings?.base_url || '').trim();
    const resolvedModel = (continueModel || review.ai_model || aiSettings?.model || '').trim();
    const resolvedKey = continueKey.trim();
    if (!resolvedBase || !resolvedModel) {
      setDecisionError('请填写 Base URL 与模型名称。');
      return;
    }
    if (!resolvedKey && !aiSettings?.has_key) {
      setDecisionError('请填写 API Key，或先在新建审查页保存密钥。');
      return;
    }
    const data = new FormData();
    data.append('base_url', resolvedBase);
    data.append('model', resolvedModel);
    if (resolvedKey) data.append('api_key', resolvedKey);
    setDecisionBusy('continue');
    setDecisionError('');
    try {
      const updated = await continueReview(id, data);
      setReview(updated);
      setRetryKey((value) => value + 1);
    } catch (requestError) {
      setDecisionError(errorMessage(requestError));
    } finally {
      setDecisionBusy(null);
    }
  }

  async function handleFinish() {
    setDecisionBusy('finish');
    setDecisionError('');
    try {
      const updated = await finishReview(id);
      setReview(updated);
    } catch (requestError) {
      setDecisionError(errorMessage(requestError));
    } finally {
      setDecisionBusy(null);
    }
  }

  if (loading && !review) return <LoadingState label="正在加载审查详情" />;
  if (error && !review) return <ErrorState message={error} onRetry={() => setRetryKey((value) => value + 1)} />;
  if (!review) return null;

  const findings = review.findings || [];
  const vulnerabilities = review.vulnerabilities || [];
  const warnings = review.warnings || [];
  const coverage = review.coverage;
  const coverageEligible = coverage ? (coverage.eligible_files || coverage.total_files) : 0;
  const coveragePercentage = coverage && coverageEligible > 0 ? Math.round((coverage.analyzed_files / coverageEligible) * 100) : null;
  const highSeverity = findings.filter((finding) => finding.severity === 'critical' || finding.severity === 'high').length;
  const dependencyUnavailable = ['failed', 'unavailable', 'error'].includes((review.dependency_status || '').toLowerCase());
  const partial = review.status === 'completed' && (warnings.length > 0 || dependencyUnavailable);
  const hasResultData = Boolean(coverage || findings.length || vulnerabilities.length || warnings.length);
  const showResults = review.status === 'completed' || (!isActive(review.status) && hasResultData);

  return (
    <div className="review-detail-page">
      <Link to="/history" className="back-link"><ArrowLeft size={16} />返回审查历史</Link>
      <section className="review-header">
        <div className="review-identity">
          <div className={`source-icon source-${review.source_type}`}>{review.source_type === 'github' ? <Github size={24} /> : <FileArchive size={24} />}</div>
          <div><div className="review-title-line"><h1>{review.source_name}</h1><StatusBadge status={review.status} /></div><div className="review-meta"><span>{review.source_type === 'github' ? 'GitHub 公共仓库' : 'ZIP 源码包'}</span><span><CalendarDays size={14} />{formatDate(review.created_at)}</span><span>ID {review.id}</span></div></div>
        </div>
        <div className="report-actions" aria-label="下载审查报告">
          <button type="button" className="button button-secondary" onClick={() => void handleDownload('markdown')} disabled={isActive(review.status) || downloading !== null}><FileText size={17} />{downloading === 'markdown' ? '正在下载…' : '下载 Markdown'}</button>
          <button type="button" className="button button-secondary" onClick={() => void handleDownload('json')} disabled={isActive(review.status) || downloading !== null}><FileJson size={17} />{downloading === 'json' ? '正在下载…' : '下载 JSON'}</button>
        </div>
      </section>
      {downloadError && <div className="inline-alert alert-error" role="alert"><Download size={18} />{downloadError}</div>}
      {error && <div className="inline-alert alert-error" role="alert"><AlertCircle size={18} />{error}<button type="button" onClick={() => setRetryKey((value) => value + 1)}>重新加载</button></div>}

      <ProgressPanel review={review} onCancel={() => void handleCancel()} cancelling={cancelling} cancelError={cancelError} />

      {review.status === 'completed' && review.awaiting_decision && (
        <section className="panel decision-panel" aria-labelledby="decision-title">
          <div className="result-section-heading compact">
            <div>
              <span className="section-icon"><Layers3 size={20} /></span>
              <div>
                <h2 id="decision-title">继续深入审查，还是结束？</h2>
                <p>临时源码仍保留在本机。继续会跳过已完整分析的文件并把本轮预算翻倍；结束后立即清理源码。</p>
              </div>
            </div>
          </div>
          <div className="decision-meta">
            <span>当前深度：第 {review.depth ?? 1} 轮</span>
            <span>本轮 AI 请求预算：{review.ai_budget ?? 150} 次</span>
            <span>继续后预算：{(review.ai_budget ?? 150) * 2} 次</span>
          </div>
          <details className="decision-config">
            <summary>调整本轮 AI 配置（可选）</summary>
            <div className="form-grid">
              <div className="field">
                <label htmlFor="continue-base-url">Base URL</label>
                <div className="input-with-icon"><Braces size={18} /><input id="continue-base-url" type="url" value={continueBase} onChange={(event) => setContinueBase(event.target.value)} placeholder="https://api.openai.com/v1" /></div>
              </div>
              <div className="field">
                <label htmlFor="continue-model">模型名称</label>
                <div className="input-with-icon"><Sparkles size={18} /><input id="continue-model" type="text" value={continueModel} onChange={(event) => setContinueModel(event.target.value)} placeholder="例如 gpt-4o-mini" /></div>
              </div>
            </div>
            <div className="field">
              <label htmlFor="continue-api-key">API Key</label>
              <div className="input-with-icon"><KeyRound size={18} /><input id="continue-api-key" type="password" value={continueKey} onChange={(event) => setContinueKey(event.target.value)} placeholder={aiSettings?.has_key ? '已在本机保存，留空则继续使用' : '输入 API Key'} autoComplete="off" spellCheck={false} /></div>
            </div>
          </details>
          {decisionError && <div className="inline-alert alert-error" role="alert"><AlertCircle size={18} /><span>{decisionError}</span></div>}
          <div className="decision-actions">
            <button type="button" className="button button-primary" onClick={() => void handleContinue()} disabled={decisionBusy !== null}><Sparkles size={17} />{decisionBusy === 'continue' ? '正在继续…' : '继续深入审查'}</button>
            <button type="button" className="button button-secondary" onClick={() => void handleFinish()} disabled={decisionBusy !== null}><CheckCircle2 size={17} />{decisionBusy === 'finish' ? '正在结束…' : '结束审查并清理源码'}</button>
          </div>
        </section>
      )}

      {partial && <div className="partial-banner" role="status"><AlertTriangle size={22} /><div><strong>审查已部分完成</strong><p>部分分析或外部服务未能完成。以下结果仍可供复核，请结合警告和覆盖范围判断。</p></div></div>}
      {warnings.length > 0 && <section className="warning-panel" aria-labelledby="warnings-title"><h2 id="warnings-title"><AlertTriangle size={18} />任务警告</h2><ul>{warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul></section>}

      {showResults && (
        <div className="review-results">
          <div className="summary-cards" aria-label="审查结果摘要">
            <div className="summary-card"><span className="summary-icon indigo"><ScanSearch size={20} /></span><div><strong>{findings.length}</strong><span>代码发现</span></div><small>静态与 AI 结果</small></div>
            <div className="summary-card"><span className="summary-icon red"><AlertTriangle size={20} /></span><div><strong>{highSeverity}</strong><span>高优先级发现</span></div><small>严重与高危</small></div>
            <div className="summary-card"><span className="summary-icon green"><FileText size={20} /></span><div><strong>{coveragePercentage === null ? '—' : `${coveragePercentage}%`}</strong><span>AI 深度覆盖</span></div><small>{coverage ? `${coverage.analyzed_files} / ${coverageEligible} 个文件；静态规则已全量扫描` : '覆盖信息未生成'}</small></div>
            <div className="summary-card"><span className="summary-icon amber"><ShieldAlert size={20} /></span><div><strong>{vulnerabilities.length}</strong><span>依赖漏洞</span></div><small>独立 OSV 检查</small></div>
          </div>
          {review.status === 'completed' && <BaselinePanel data={baseline} />}
          <CoveragePanel coverage={coverage} />
          <FindingsPanel findings={findings} />
          <VulnerabilitiesPanel vulnerabilities={vulnerabilities} dependencyStatus={review.dependency_status} />
          <p className="result-disclaimer"><AlertCircle size={15} />AI 结果可能存在误报或遗漏；请打开证据并结合完整代码上下文进行人工确认。</p>
        </div>
      )}
    </div>
  );
}
