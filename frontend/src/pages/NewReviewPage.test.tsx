import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ApiError, createReview, detectModels, getAISettings } from '../api';
import NewReviewPage from './NewReviewPage';
import { makeReview } from '../test/fixtures';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, createReview: vi.fn(), getAISettings: vi.fn(), detectModels: vi.fn() };
});

const mockedCreate = vi.mocked(createReview);
const mockedSettings = vi.mocked(getAISettings);
const mockedDetect = vi.mocked(detectModels);

function renderForm() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<NewReviewPage />} />
        <Route path="/reviews/:id" element={<div>已进入审查详情</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function submitValidGithubForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('仓库地址'), 'https://github.com/example/shop-service');
  await user.type(screen.getByLabelText('API Key'), 'test-secret-key');
  await user.click(screen.getByRole('button', { name: '开始审查' }));
}

describe('NewReviewPage', () => {
  beforeEach(() => {
    mockedCreate.mockReset();
    mockedSettings.mockReset();
    mockedDetect.mockReset();
    mockedSettings.mockResolvedValue({ base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini', has_key: true });
    mockedDetect.mockResolvedValue({ models: [], suggested: null, source: 'none' });
  });

  it('创建 GitHub 审查并只通过 FormData 提交密钥', async () => {
    mockedCreate.mockResolvedValue(makeReview({ id: 'new-review' }));
    const storageSpy = vi.spyOn(Storage.prototype, 'setItem');
    const user = userEvent.setup();
    renderForm();
    await submitValidGithubForm(user);

    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    const data = mockedCreate.mock.calls[0][0];
    expect(data.get('source_type')).toBe('github');
    expect(data.get('repo_url')).toBe('https://github.com/example/shop-service');
    expect(data.get('base_url')).toBe('https://api.openai.com/v1');
    expect(data.get('api_key')).toBe('test-secret-key');
    expect(data.get('model')).toBe('gpt-4o-mini');
    expect(data.get('remember_key')).toBe('true');
    expect(data.get('file')).toBeNull();
    expect(storageSpy).not.toHaveBeenCalled();
    expect(await screen.findByText('已进入审查详情')).toBeInTheDocument();
    storageSpy.mockRestore();
  });

  it('切换 ZIP 后仅上传所选文件', async () => {
    mockedCreate.mockResolvedValue(makeReview({ id: 'zip-review', source_type: 'zip' }));
    const user = userEvent.setup();
    renderForm();
    await user.click(screen.getByRole('button', { name: /ZIP 源码包/ }));
    const archive = new File(['zip-content'], 'sample.zip', { type: 'application/zip' });
    await user.upload(screen.getByLabelText('源码 ZIP 文件'), archive);
    await user.type(screen.getByLabelText('API Key'), 'zip-secret');
    await user.click(screen.getByRole('button', { name: '开始审查' }));

    await waitFor(() => expect(mockedCreate).toHaveBeenCalled());
    const data = mockedCreate.mock.calls[0][0];
    expect(data.get('source_type')).toBe('zip');
    expect(data.get('repo_url')).toBeNull();
    expect(data.get('file')).toBe(archive);
  });

  it('拒绝非法 GitHub 地址且不发送请求', async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(screen.getByLabelText('仓库地址'), 'https://example.com/not-github');
    await user.type(screen.getByLabelText('API Key'), 'secret');
    await user.click(screen.getByRole('button', { name: '开始审查' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('请输入标准的 HTTPS GitHub 仓库地址');
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it('自动识别模型并可继续编辑模型名称', async () => {
    mockedSettings.mockResolvedValue({ base_url: 'https://api.deepseek.com/v1', model: 'gpt-4o-mini', has_key: true });
    mockedDetect.mockResolvedValue({ models: ['deepseek-chat'], suggested: 'deepseek-chat', source: 'provider' });
    const user = userEvent.setup();
    renderForm();
    await waitFor(() => expect(screen.getByLabelText('Base URL')).toHaveValue('https://api.deepseek.com/v1'));

    const keyInput = screen.getByLabelText('API Key');
    await user.type(keyInput, 'sk-new-key');
    await user.tab();

    await waitFor(() => expect(screen.getByLabelText('模型名称')).toHaveValue('deepseek-chat'));
    expect(mockedDetect).toHaveBeenCalledWith('https://api.deepseek.com/v1', 'sk-new-key');
    expect(screen.getByText(/已识别 1 个可用模型/)).toBeInTheDocument();

    await user.clear(screen.getByLabelText('模型名称'));
    await user.type(screen.getByLabelText('模型名称'), 'custom-model');
    await user.tab();
    expect(screen.getByLabelText('模型名称')).toHaveValue('custom-model');
  });

  it('未保存密钥时模型名称默认为空', async () => {
    mockedSettings.mockResolvedValue({ base_url: 'https://api.openai.com/v1', model: '', has_key: false });
    const user = userEvent.setup();
    renderForm();
    await waitFor(() => expect(screen.getByLabelText('API Key')).toHaveAttribute('placeholder', '输入本次审查使用的密钥'));
    expect(screen.getByLabelText('模型名称')).toHaveValue('');

    await user.type(screen.getByLabelText('仓库地址'), 'https://github.com/example/shop-service');
    await user.type(screen.getByLabelText('API Key'), 'test-secret-key');
    await user.click(screen.getByRole('button', { name: '开始审查' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('请填写模型名称');
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it('根据 DeepSeek 密钥格式切换服务地址并填入模型', async () => {
    mockedSettings.mockResolvedValue({ base_url: 'https://api.openai.com/v1', model: '', has_key: false });
    mockedDetect.mockResolvedValue({
      models: ['deepseek-chat'],
      suggested: 'deepseek-chat',
      source: 'heuristic',
      suggested_base_url: 'https://api.deepseek.com/v1',
    });
    const user = userEvent.setup();
    renderForm();
    await waitFor(() => expect(screen.getByLabelText('Base URL')).toHaveValue('https://api.openai.com/v1'));

    await user.type(screen.getByLabelText('API Key'), 'sk-00000000000000000000000000000000');
    await user.tab();

    await waitFor(() => expect(screen.getByLabelText('模型名称')).toHaveValue('deepseek-chat'));
    expect(screen.getByLabelText('Base URL')).toHaveValue('https://api.deepseek.com/v1');
    expect(screen.getByText(/已根据密钥格式切换 Base URL/)).toBeInTheDocument();
  });

  it('保存过密钥后留空也能提交', async () => {
    mockedSettings.mockResolvedValue({ base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini', has_key: true });
    mockedCreate.mockResolvedValue(makeReview({ id: 'keyless-review' }));
    const user = userEvent.setup();
    renderForm();
    await waitFor(() => expect(screen.getByLabelText('API Key')).toHaveAttribute('placeholder', '已在本机保存，留空则继续使用'));
    await user.type(screen.getByLabelText('仓库地址'), 'https://github.com/example/shop-service');
    await user.click(screen.getByRole('button', { name: '开始审查' }));

    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    const data = mockedCreate.mock.calls[0][0];
    expect(data.get('api_key')).toBeNull();
    expect(data.get('remember_key')).toBe('true');
  });

  it('展示后端错误并保留当前表单', async () => {
    mockedCreate.mockRejectedValue(new ApiError('AI 服务配置无效', 400));
    const user = userEvent.setup();
    renderForm();
    await submitValidGithubForm(user);
    expect(await screen.findByRole('alert')).toHaveTextContent('AI 服务配置无效');
    expect(screen.getByLabelText('仓库地址')).toHaveValue('https://github.com/example/shop-service');
    expect(screen.queryByText('已进入审查详情')).not.toBeInTheDocument();
  });
});
