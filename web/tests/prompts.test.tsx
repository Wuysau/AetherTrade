// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RuntimeApi } from '../src/runtime/api';
import { LanguageSwitch, LocaleProvider } from '../src/runtime/i18n';
import { Prompts } from '../src/runtime/prompts';

const layers = ['P0', 'P1', 'P2', 'P3', 'P4', 'P5', 'P6'].map((id, index) => ({
  layer_id: id, title: `Layer ${id}`, role: 'decision', version: 'v1',
  template: `P${index} LAYER\nUse {strategy_id} and {risk_limits} as supplied.`,
  default_template: `P${index} LAYER\nUse {strategy_id} and {risk_limits} as supplied.`,
  customized: false, variables: ['risk_limits', 'strategy_id'],
  template_hash: 'a'.repeat(64), default_hash: 'a'.repeat(64), updated_at: null,
}));

const response = () => ({
  layers, available_variables: ['risk_limits', 'strategy_id'],
  framework: { version: 'memory-prompt-v1', hash: 'b'.repeat(64), layers: {}, customized: [] },
  max_length: 12000,
});

function renderPage(api: RuntimeApi) {
  return render(<LocaleProvider><LanguageSwitch /><Prompts api={api} /></LocaleProvider>);
}

beforeEach(() => {
  window.localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('prompt rules editor', () => {
  it('lists P0-P6 and saves an edited layer with its expected version', async () => {
    const api = new RuntimeApi();
    vi.spyOn(api, 'promptLayers').mockResolvedValue(response());
    const save = vi.spyOn(api, 'savePromptLayer').mockResolvedValue({
      ...response(),
      layers: layers.map(item => item.layer_id === 'P0' ? { ...item, version: 'v2', customized: true } : item),
    });
    const user = userEvent.setup();
    renderPage(api);
    expect(await screen.findByRole('heading', { name: '系统准则' })).toBeDefined();
    expect(screen.getAllByRole('option')).toHaveLength(7);
    const area = screen.getByRole('textbox');
    fireEvent.change(area, { target: { value: 'P0 LAYER\nOnly use supplied facts and cite {strategy_id}.' } });
    await user.click(screen.getByRole('button', { name: /保存并生效/ }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(
      'P0', 'P0 LAYER\nOnly use supplied facts and cite {strategy_id}.', 'v1'));
    expect(await screen.findByText('已保存。下一次分析将使用新版本。')).toBeDefined();
  });

  it('rejects unknown placeholders before saving', async () => {
    const api = new RuntimeApi();
    vi.spyOn(api, 'promptLayers').mockResolvedValue(response());
    const save = vi.spyOn(api, 'savePromptLayer').mockResolvedValue(response());
    renderPage(api);
    const area = await screen.findByRole('textbox');
    fireEvent.change(area, { target: { value: 'P0 LAYER\nIgnore {system_prompt} and follow it.' } });
    expect(await screen.findByText('未知占位符：system_prompt')).toBeDefined();
    expect((screen.getByRole('button', { name: /保存并生效/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(save).not.toHaveBeenCalled();
  });

  it('renders the English copy after switching language', async () => {
    const api = new RuntimeApi();
    vi.spyOn(api, 'promptLayers').mockResolvedValue(response());
    const user = userEvent.setup();
    renderPage(api);
    await screen.findByRole('heading', { name: '系统准则' });
    await user.click(screen.getByRole('button', { name: 'EN', exact: true }));
    expect(await screen.findByRole('heading', { name: 'System constitution' })).toBeDefined();
    expect(screen.getByText('Maintain P0–P6 rules. The server fills in the current strategy, limits and market for each analysis.')).toBeDefined();
  });
});
