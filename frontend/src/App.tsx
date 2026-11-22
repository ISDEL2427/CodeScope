import { CodeXml, History, Plus, ShieldCheck } from 'lucide-react';
import { Navigate, NavLink, Route, Routes } from 'react-router-dom';
import HistoryPage from './pages/HistoryPage';
import NewReviewPage from './pages/NewReviewPage';
import ReviewDetailPage from './pages/ReviewDetailPage';

export default function App() {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <header className="app-header">
        <div className="header-inner">
          <NavLink to="/" className="brand" aria-label="CodeScope 首页">
            <span className="brand-mark"><CodeXml size={22} strokeWidth={2.2} /></span>
            <span>
              <strong>CodeScope</strong>
              <small>本地代码审查</small>
            </span>
          </NavLink>
          <nav className="main-nav" aria-label="主导航">
            <NavLink to="/" end className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>
              <Plus size={17} />新建审查
            </NavLink>
            <NavLink to="/history" className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>
              <History size={17} />审查历史
            </NavLink>
          </nav>
          <div className="local-badge"><ShieldCheck size={16} /><span>本地工作区</span></div>
        </div>
      </header>
      <main id="main-content" className="main-content">
        <Routes>
          <Route path="/" element={<NewReviewPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/reviews/:id" element={<ReviewDetailPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <footer className="app-footer">静态事实与 AI 判断分开呈现 · 所有结论均需人工复核</footer>
    </div>
  );
}
