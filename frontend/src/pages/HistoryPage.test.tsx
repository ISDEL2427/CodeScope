import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ApiError, listReviews } from '../api';
import { makeReview } from '../test/fixtures';
import HistoryPage from './HistoryPage';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, listReviews: vi.fn() };
});

const mockedList = vi.mocked(listReviews);

function renderHistory() {
  return render(<MemoryRouter><HistoryPage /></MemoryRouter>);
}

describe('HistoryPage', () => {
  it('展示省略结果数组的历史项且不将其误报为零发现', async () => {
    mockedList.mockResolvedValue([
      makeReview({ id: 'summary-only', findings: undefined, vulnerabilities: undefined }),
      makeReview({ id: 'zip-one', source_type: 'zip', source_name: 'local-project.zip', status: 'cancelled', findings: [] }),
    ]);
    renderHistory();
    expect(await screen.findByRole('heading', { name: 'example/shop-service' })).toBeInTheDocument();
    expect(screen.getByText('查看完整结果')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /example\/shop-service/ })).toHaveAttribute('href', '/reviews/summary-only');
    expect(screen.getByText('local-project.zip')).toBeInTheDocument();
    expect(screen.getByText('0 项发现')).toBeInTheDocument();
  });

  it('显示空历史入口', async () => {
    mockedList.mockResolvedValue([]);
    renderHistory();
    expect(await screen.findByText('还没有审查记录')).toBeInTheDocument();
    const emptyState = screen.getByText('还没有审查记录').closest('.empty-state') as HTMLElement;
    expect(within(emptyState).getByRole('link', { name: '新建审查' })).toHaveAttribute('href', '/');
  });

  it('加载失败后可重试', async () => {
    mockedList.mockRejectedValueOnce(new ApiError('历史读取失败', 500)).mockResolvedValueOnce([makeReview()]);
    const user = userEvent.setup();
    renderHistory();
    expect(await screen.findByText('历史读取失败')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByRole('heading', { name: 'example/shop-service' })).toBeInTheDocument();
    expect(mockedList).toHaveBeenCalledTimes(2);
  });
});
