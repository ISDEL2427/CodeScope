import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ApiError, cancelReview, continueReview, downloadReport, finishReview, getAISettings, getBaseline, getReview } from '../api';
import { makeReview, runningReview } from '../test/fixtures';
import ReviewDetailPage from './ReviewDetailPage';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    getReview: vi.fn(),
    cancelReview: vi.fn(),
    downloadReport: vi.fn(),
    continueReview: vi.fn(),
    finishReview: vi.fn(),
    getAISettings: vi.fn(),
    getBaseline: vi.fn(),
  };
});

const mockedGet = vi.mocked(getReview);
const mockedCancel = vi.mocked(cancelReview);
const mockedDownload = vi.mocked(downloadReport);
const mockedContinue = vi.mocked(continueReview);
const mockedFinish = vi.mocked(finishReview);
const mockedSettings = vi.mocked(getAISettings);
const mockedBaseline = vi.mocked(getBaseline);

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={['/reviews/review-1']}>
      <Routes><Route path="/reviews/:id" element={<ReviewDetailPage />} /></Routes>
    </MemoryRouter>,
  );
}

describe('ReviewDetailPage', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedCancel.mockReset();
    mockedDownload.mockReset();
    mockedDownload.mockResolvedValue(undefined);
    mockedContinue.mockReset();
    mockedFinish.mockReset();
    mockedSettings.mockReset();
    mockedSettings.mockResolvedValue({ base_url: '', model: '', has_key: true });
    mockedBaseline.mockReset();
    mockedBaseline.mockResolvedValue({ baseline: null, message: '这是该目标的首次体检。' });
  });

  it('按严重度、类别和来源筛选并展开代码证据', async () => {
    mockedGet.mockResolvedValue(makeReview());
    const user = userEvent.setup();
    renderDetail();
    expect(await screen.findByText('检测到疑似硬编码密钥')).toBeInTheDocument();
    expect(screen.getByText('订单模块承担过多职责')).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('严重度'), 'high');
    expect(screen.getByText('检测到疑似硬编码密钥')).toBeInTheDocument();
    expect(screen.queryByText('订单模块承担过多职责')).not.toBeInTheDocument();
    await user.click(screen.getByText('查看代码证据'));
    expect(screen.getByText('const apiSecret = "REDACTED";')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '清除筛选' }));

    await user.selectOptions(screen.getByLabelText('类别'), 'design');
    expect(screen.getByText('订单模块承担过多职责')).toBeInTheDocument();
    expect(screen.queryByText('检测到疑似硬编码密钥')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '清除筛选' }));

    await user.selectOptions(screen.getByLabelText('来源'), 'static');
    expect(screen.getByText('检测到疑似硬编码密钥')).toBeInTheDocument();
    expect(screen.queryByText('空数组可能导致无效请求')).not.toBeInTheDocument();
    expect(screen.getByText('1 / 3 项')).toBeInTheDocument();
  });

  it('展示跳过文件、独立漏洞和参考链接', async () => {
    mockedGet.mockResolvedValue(makeReview());
    const user = userEvent.setup();
    renderDetail();
    expect(await screen.findByText('example/shop-service')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '依赖漏洞' })).toBeInTheDocument();
    expect(screen.getByText('GHSA-example-0001')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /参考 1/ })).toHaveAttribute('href', 'https://osv.dev/vulnerability/GHSA-example-0001');
    await user.click(screen.getByText(/查看 1 个跳过或未覆盖文件/));
    expect(screen.getByText('dist/bundle.js')).toBeVisible();
    expect(screen.getByText('构建产物，已跳过')).toBeVisible();
  });

  it('轮询运行任务并在完成后停止', async () => {
    vi.useFakeTimers();
    mockedGet.mockResolvedValueOnce(runningReview).mockResolvedValueOnce(makeReview());
    renderDetail();
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole('heading', { name: '审查正在进行' })).toBeInTheDocument();
    expect(screen.getByText('正在审查 src/orders.ts')).toBeInTheDocument();
    expect(mockedGet).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(mockedGet).toHaveBeenCalledTimes(2);
    expect(screen.getByText('检测到疑似硬编码密钥')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '取消审查' })).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it('支持取消正在运行的审查', async () => {
    mockedGet.mockResolvedValue(runningReview);
    mockedCancel.mockResolvedValue(makeReview({ ...runningReview, status: 'cancelled', stage_detail: '用户取消了任务', error: null }));
    const user = userEvent.setup();
    renderDetail();
    await user.click(await screen.findByRole('button', { name: '取消审查' }));
    expect(await screen.findByRole('heading', { name: '已取消' })).toBeInTheDocument();
    expect(mockedCancel).toHaveBeenCalledWith('review-1');
    expect(screen.queryByRole('button', { name: '取消审查' })).not.toBeInTheDocument();
  });

  it('加载失败后可重试并恢复详情', async () => {
    mockedGet.mockRejectedValueOnce(new ApiError('详情服务暂时不可用', 503)).mockResolvedValueOnce(makeReview());
    const user = userEvent.setup();
    renderDetail();
    expect(await screen.findByText('详情服务暂时不可用')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByText('检测到疑似硬编码密钥')).toBeInTheDocument();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it('明确展示部分完成、任务警告和依赖服务不可用', async () => {
    mockedGet.mockResolvedValue(makeReview({
      warnings: ['AI 第 2 个代码块返回无效 JSON，相关发现已丢弃。'],
      dependency_status: 'unavailable', vulnerabilities: [],
    }));
    renderDetail();
    expect(await screen.findByText('审查已部分完成')).toBeInTheDocument();
    expect(screen.getByText(/AI 第 2 个代码块/)).toBeInTheDocument();
    expect(screen.getByText('依赖扫描结果不可用')).toBeInTheDocument();
  });

  it('分别请求 Markdown 和 JSON 报告', async () => {
    mockedGet.mockResolvedValue(makeReview());
    const user = userEvent.setup();
    renderDetail();
    await user.click(await screen.findByRole('button', { name: '下载 Markdown' }));
    await user.click(screen.getByRole('button', { name: '下载 JSON' }));
    expect(mockedDownload).toHaveBeenNthCalledWith(1, 'review-1', 'markdown');
    expect(mockedDownload).toHaveBeenNthCalledWith(2, 'review-1', 'json');
  });

  it('首轮完成后提供继续深入与结束审查的选择', async () => {
    const decisionReview = makeReview({
      status: 'completed',
      awaiting_decision: true,
      depth: 2,
      ai_budget: 300,
      ai_base_url: 'https://api.openai.com/v1',
      ai_model: 'gpt-4o-mini',
    });
    mockedGet.mockResolvedValue(decisionReview);
    mockedFinish.mockResolvedValue(makeReview({ status: 'completed', awaiting_decision: false, stage_detail: '审查已结束；临时源码已清理' }));
    const user = userEvent.setup();
    renderDetail();

    expect(await screen.findByRole('heading', { name: '继续深入审查，还是结束？' })).toBeInTheDocument();
    expect(screen.getByText('当前深度：第 2 轮')).toBeInTheDocument();
    expect(screen.getByText('继续后预算：600 次')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '结束审查并清理源码' }));
    expect(mockedFinish).toHaveBeenCalledWith('review-1');
    await waitFor(() => expect(screen.queryByRole('heading', { name: '继续深入审查，还是结束？' })).not.toBeInTheDocument());
  });

  it('继续深入审查时复用已保存密钥并提交配置', async () => {
    const decisionReview = makeReview({
      status: 'completed',
      awaiting_decision: true,
      depth: 1,
      ai_budget: 150,
      ai_base_url: 'https://api.deepseek.com/v1',
      ai_model: 'deepseek-chat',
    });
    mockedGet.mockResolvedValue(decisionReview);
    mockedContinue.mockResolvedValue(makeReview({ status: 'running', stage: 'ai_review', progress: 30, awaiting_decision: false }));
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole('button', { name: '继续深入审查' }));
    await waitFor(() => expect(mockedContinue).toHaveBeenCalledTimes(1));
    const data = mockedContinue.mock.calls[0][1];
    expect(data.get('base_url')).toBe('https://api.deepseek.com/v1');
    expect(data.get('model')).toBe('deepseek-chat');
    expect(data.get('api_key')).toBeNull();
  });

  it('展示失败任务的后端错误', async () => {
    mockedGet.mockResolvedValue(makeReview({
      status: 'failed', stage: 'ai_review', progress: 60, completed_at: null,
      error: 'AI 服务连续请求失败，任务已停止。', coverage: undefined, findings: [], vulnerabilities: [],
    }));
    renderDetail();
    expect(await screen.findByText('AI 服务连续请求失败，任务已停止。')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '失败' })).toBeInTheDocument();
  });

  it('展示与上次体检的新增、已修复和仍存在风险', async () => {
    const [high, medium, low] = makeReview().findings!;
    mockedGet.mockResolvedValue(makeReview());
    mockedBaseline.mockResolvedValue({
      baseline: {
        current_id: 'review-1',
        current_created_at: '2026-11-21T08:00:00Z',
        baseline_id: 'review-0',
        baseline_created_at: '2026-11-20T08:00:00Z',
        new_findings: [{ ...high, id: 'new-1', title: '新增的硬编码令牌' }],
        resolved_findings: [{ ...medium, id: 'old-1', title: '已修复的模块耦合' }],
        persisting_findings: [{ ...low, id: 'stay-1', title: '仍存在的空数组问题' }],
        new_vulnerabilities: [],
        resolved_vulnerabilities: [],
        persisting_vulnerabilities: [],
      },
      message: null,
    });
    renderDetail();

    expect(await screen.findByRole('heading', { name: '与上次体检对比' })).toBeInTheDocument();
    expect(screen.getByText('新增的硬编码令牌')).toBeInTheDocument();
    expect(screen.getByText('已修复的模块耦合')).toBeInTheDocument();
    expect(screen.getByText('仍存在的空数组问题')).toBeInTheDocument();
    expect(screen.getByText(/^基线 /)).toBeInTheDocument();
    expect(mockedBaseline).toHaveBeenCalledWith('review-1');
  });

  it('只为严重级发现显示需人工确认标记', async () => {
    const [baseCritical, baseHigh] = makeReview().findings!;
    mockedGet.mockResolvedValue(makeReview({
      findings: [
        { ...baseCritical, id: 'critical-1', severity: 'critical', title: '可直接泄露凭据的配置' },
        { ...baseHigh, id: 'high-1', severity: 'high', title: '普通高风险问题' },
      ],
    }));
    renderDetail();

    expect(await screen.findByText('可直接泄露凭据的配置')).toBeInTheDocument();
    expect(screen.getByText('普通高风险问题')).toBeInTheDocument();
    expect(screen.getAllByText('需人工确认')).toHaveLength(1);
  });
});
