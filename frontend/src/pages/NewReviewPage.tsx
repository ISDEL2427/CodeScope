import { useEffect, useState, type FormEvent } from 'react';
import { AlertCircle, ArrowRight, Braces, FileArchive, FileCode2, Github, KeyRound, Layers3, LockKeyhole, ScanSearch, ShieldCheck, Sparkles, UploadCloud, Wand2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { createReview, detectModels, errorMessage, getAISettings } from '../api';
import type { SourceType } from '../types';

const MAX_ZIP_SIZE = 50 * 1024 * 1024;

function validGithubUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const parts = url.pathname.split('/').filter(Boolean);
    return url.protocol === 'https:'
      && ['github.com', 'www.github.com'].includes(url.hostname)
      && parts.length === 2
      && !url.username
      && !url.password
      && !url.port
      && !url.search
      && !url.hash;
  } catch {
    return false;
  }
}

function validBaseUrl(value: string): boolean {
  try {
    return ['http:', 'https:'].includes(new URL(value).protocol);
  } catch {
    return false;
  }
}

function isDefaultOpenAIBase(value: string): boolean {
  const normalized = value.trim().replace(/\/+$/, '').replace(/\/chat\/completions$/, '');
  return normalized === 'https://api.openai.com/v1';
}

export default function NewReviewPage() {
  const navigate = useNavigate();
  const [sourceType, setSourceType] = useState<SourceType>('github');
  const [repoUrl, setRepoUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [modelEdited, setModelEdited] = useState(false);
  const [baseUrlEdited, setBaseUrlEdited] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [hasKey, setHasKey] = useState(false);
  const [rememberKey, setRememberKey] = useState(true);
  const [detecting, setDetecting] = useState(false);
  const [detectHint, setDetectHint] = useState('');

  useEffect(() => {
    let disposed = false;
    getAISettings()
      .then((saved) => {
        if (disposed) return;
        if (saved.base_url) setBaseUrl(saved.base_url);
        if (saved.has_key && saved.model) setModel(saved.model);
        setHasKey(saved.has_key);
      })
      .catch(() => undefined);
    return () => { disposed = true; };
  }, []);

  function chooseSource(next: SourceType) {
    setSourceType(next);
    setError('');
  }

  async function autoDetect(force: boolean) {
    const trimmedBaseUrl = baseUrl.trim();
    const trimmedKey = apiKey.trim();
    if (!validBaseUrl(trimmedBaseUrl) || (!trimmedKey && !hasKey)) return;
    setDetecting(true);
    setDetectHint('');
    try {
      const detected = await detectModels(trimmedBaseUrl, trimmedKey || undefined);
      const shouldSwitchBase = Boolean(detected.suggested_base_url)
        && !baseUrlEdited
        && isDefaultOpenAIBase(trimmedBaseUrl);
      if (shouldSwitchBase) setBaseUrl(detected.suggested_base_url!);
      if (detected.suggested && (force || !modelEdited || !model.trim())) {
        setModel(detected.suggested);
        setModelEdited(false);
      }
      if (shouldSwitchBase) {
        setDetectHint('已根据密钥格式切换 Base URL，模型名称可继续手动编辑。');
      } else {
        setDetectHint(
          detected.source === 'none'
            ? '未能自动识别模型，请手动填写模型名称。'
            : `已识别 ${detected.models.length} 个可用模型，可继续手动编辑模型名称。`,
        );
      }
    } catch {
      setDetectHint('模型自动识别失败，请手动填写模型名称。');
    } finally {
      setDetecting(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    const trimmedBaseUrl = baseUrl.trim();
    const trimmedKey = apiKey.trim();
    const trimmedModel = model.trim();

    if (sourceType === 'github' && !validGithubUrl(repoUrl.trim())) {
      setError('请输入标准的 HTTPS GitHub 仓库地址，例如 https://github.com/owner/repo。');
      return;
    }
    if (sourceType === 'zip' && !file) {
      setError('请选择要审查的 ZIP 文件。');
      return;
    }
    if (sourceType === 'zip' && file && (!file.name.toLowerCase().endsWith('.zip') || file.size > MAX_ZIP_SIZE)) {
      setError('ZIP 文件必须以 .zip 结尾，且大小不能超过 50 MiB。');
      return;
    }
    if (!validBaseUrl(trimmedBaseUrl)) {
      setError('请输入有效的 OpenAI 兼容 Base URL（HTTP 或 HTTPS）。');
      return;
    }
    if (!trimmedKey && !hasKey) {
      setError('请填写 API Key，或先在本机保存密钥。');
      return;
    }
    if (!trimmedModel) {
      setError('请填写模型名称。');
      return;
    }

    const data = new FormData();
    data.append('source_type', sourceType);
    data.append('base_url', trimmedBaseUrl);
    if (trimmedKey) data.append('api_key', trimmedKey);
    data.append('remember_key', rememberKey ? 'true' : 'false');
    data.append('model', trimmedModel);
    if (sourceType === 'github') data.append('repo_url', repoUrl.trim());
    if (sourceType === 'zip' && file) data.append('file', file);

    setSubmitting(true);
    try {
      const review = await createReview(data);
      if (trimmedKey) setHasKey(true);
      setApiKey('');
      navigate(`/reviews/${review.id}`);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="new-review-page">
      <section className="page-heading hero-heading">
        <div>
          <span className="eyebrow"><ScanSearch size={15} />从源码到可复核结论</span>
          <h1>开始一次代码审查</h1>
          <p>静态扫描、AI 审查与依赖漏洞检查协同工作，每一项发现都保留文件位置与证据。</p>
        </div>
      </section>

      <div className="new-review-layout">
        <form className="panel review-form" onSubmit={handleSubmit} noValidate>
          <div className="section-heading">
            <span className="step-number">01</span>
            <div><h2>选择代码来源</h2><p>两种来源互斥，仅支持公开仓库或本地 ZIP。</p></div>
          </div>
          <div className="source-switch" role="group" aria-label="代码来源">
            <button type="button" className={sourceType === 'github' ? 'source-option selected' : 'source-option'} aria-pressed={sourceType === 'github'} onClick={() => chooseSource('github')}>
              <Github size={22} /><span><strong>GitHub 仓库</strong><small>公开仓库 · HTTPS</small></span><span className="radio-dot" />
            </button>
            <button type="button" className={sourceType === 'zip' ? 'source-option selected' : 'source-option'} aria-pressed={sourceType === 'zip'} onClick={() => chooseSource('zip')}>
              <FileArchive size={22} /><span><strong>ZIP 源码包</strong><small>本地上传 · 最大 50 MiB</small></span><span className="radio-dot" />
            </button>
          </div>

          {sourceType === 'github' ? (
            <div className="field source-field">
              <label htmlFor="repo-url">仓库地址</label>
              <div className="input-with-icon"><Github size={18} /><input id="repo-url" type="url" value={repoUrl} onChange={(event) => setRepoUrl(event.target.value)} placeholder="https://github.com/owner/repository" autoComplete="url" required /></div>
              <small>仅支持 github.com 上的公共仓库，不克隆子模块。</small>
            </div>
          ) : (
            <div className="field source-field">
              <label htmlFor="source-file">源码 ZIP 文件</label>
              <label className={`file-drop ${file ? 'has-file' : ''}`} htmlFor="source-file">
                <UploadCloud size={27} />
                <span><strong>{file ? file.name : '选择本地 ZIP 文件'}</strong><small>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MiB` : '点击浏览文件，上传后由本地后端安全解压'}</small></span>
              </label>
              <input className="visually-hidden-file" id="source-file" type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] || null)} />
            </div>
          )}

          <div className="form-divider" />
          <div className="section-heading">
            <span className="step-number">02</span>
            <div><h2>配置 AI 服务</h2><p>连接任意 OpenAI 兼容的聊天补全接口。</p></div>
          </div>
          <div className="field">
            <label htmlFor="base-url">Base URL</label>
            <div className="input-with-icon"><Braces size={18} /><input id="base-url" type="url" value={baseUrl} onChange={(event) => { setBaseUrl(event.target.value); setBaseUrlEdited(true); }} onBlur={() => void autoDetect(false)} placeholder="https://api.openai.com/v1" autoComplete="url" required /></div>
            <small>后端将请求该地址下的 /chat/completions。</small>
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="api-key">API Key</label>
              <div className="input-with-icon"><KeyRound size={18} /><input id="api-key" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} onBlur={() => void autoDetect(false)} placeholder={hasKey ? '已在本机保存，留空则继续使用' : '输入本次审查使用的密钥'} autoComplete="off" spellCheck={false} required={!hasKey} /></div>
              <label className="check-row"><input type="checkbox" checked={rememberKey} onChange={(event) => setRememberKey(event.target.checked)} />在本机加密保存，下次打开自动使用</label>
            </div>
            <div className="field">
              <div className="field-label-row">
                <label htmlFor="model">模型名称</label>
                <button type="button" className="text-button" onClick={() => void autoDetect(true)} disabled={detecting}>{detecting ? '识别中…' : <><Wand2 size={13} />自动识别</>}</button>
              </div>
              <div className="input-with-icon"><Sparkles size={18} /><input id="model" type="text" value={model} onChange={(event) => { setModel(event.target.value); setModelEdited(true); }} placeholder="输入 Key 后自动识别，或手动填写" autoComplete="off" required /></div>
              {detectHint && <small>{detectHint}</small>}
            </div>
          </div>

          <div className="privacy-note">
            <LockKeyhole size={20} />
            <div><strong>隐私与源码发送提示</strong><p>API Key 通过 Windows DPAPI 加密保存在本机运行目录，不写入 localStorage、数据库或审查报告。筛选后的源码、统计与摘要会发送至你配置的 AI 服务，请确认你拥有相应授权。</p></div>
          </div>

          {error && <div className="inline-alert alert-error" role="alert"><AlertCircle size={18} /><span>{error}</span></div>}
          <button className="button button-primary submit-review" type="submit" disabled={submitting}>
            {submitting ? <><span className="button-spinner" />正在创建审查…</> : <>开始审查<ArrowRight size={18} /></>}
          </button>
        </form>

        <aside className="workflow-aside" aria-label="审查说明">
          <section className="panel workflow-panel">
            <div className="aside-title"><Layers3 size={19} /><h2>审查流程</h2></div>
            <ol className="workflow-list">
              <li><span><UploadCloud size={17} /></span><div><strong>安全接入</strong><small>获取源码并检查输入边界</small></div></li>
              <li><span><FileCode2 size={17} /></span><div><strong>清单与静态预扫描</strong><small>过滤文件，记录实际覆盖范围</small></div></li>
              <li><span><Sparkles size={17} /></span><div><strong>分块 AI 审查</strong><small>文件分析、模块摘要与设计综合</small></div></li>
              <li><span><ShieldCheck size={17} /></span><div><strong>独立依赖检查</strong><small>基于锁定版本查询 OSV 漏洞</small></div></li>
            </ol>
          </section>
          <section className="aside-callout">
            <ScanSearch size={21} />
            <div><strong>证据优先，而非评分</strong><p>结果区分静态事实与 AI 判断，展示证据、跳过文件和部分失败状态。AI 结论可能存在误报，需人工确认。</p></div>
          </section>
        </aside>
      </div>
    </div>
  );
}
