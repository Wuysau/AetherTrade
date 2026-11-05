// @vitest-environment jsdom
import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { RuntimeApi } from '../src/runtime/api';
import { LocaleProvider } from '../src/runtime/i18n';
import { DemoTakeoverControls } from '../src/runtime/demo_takeover';
afterEach(cleanup);

it('requires a fresh responsibility list and explicit acknowledgement', async () => {
  const calls: Array<{path:string; body:unknown}> = [];
  const api = new RuntimeApi(async (path, options) => {
    calls.push({path:String(path),body:options?.body ? JSON.parse(String(options.body)) : null});
    return new Response(JSON.stringify(options?.method === 'POST'
      ? {state:'TAKEOVER_ACKNOWLEDGED',flat_account_verified:false}
      : {state:'NEEDS_OPERATOR',digest:'a'.repeat(64),orders:[{command_id:'unknown-1',state:'UNKNOWN',order_status:null}],
        duties:[{fill_key:'fill-1',state:'HOSTED_ACTIVE',quantity:'0.01'}],flat_account_verified:false}));
  });
  render(<LocaleProvider><DemoTakeoverControls api={api} generation="one" disabled={false}/></LocaleProvider>);
  fireEvent.click(screen.getByRole('button',{name:'读取待接管责任'}));
  await screen.findByText(/unknown-1.*UNKNOWN/);
  const confirm=screen.getByRole('button',{name:'确认由我接管'});
  expect((confirm as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(confirm);
  await screen.findByText('TAKEOVER_ACKNOWLEDGED');
  expect(calls[1].body).toEqual({expected_digest:'a'.repeat(64),acknowledge_responsibility:true});
  expect(screen.getByText(/不表示订单已撤销或仓位已平/)).toBeDefined();
});
