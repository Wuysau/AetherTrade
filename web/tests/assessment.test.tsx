// @vitest-environment jsdom
import {afterEach, expect, it} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';
import {AssessmentDetails} from '../src/runtime/assessment';
import type {Decision} from '../src/runtime/types';
afterEach(cleanup);
it('distinguishes data blocked from valid hold and labels legacy', () => {
  const decision = {assessment_status:'DATA_BLOCKED', reason_code:'MISSING_CANDLES', proposal:null} as unknown as Decision;
  const view=render(<AssessmentDetails decision={decision}/>);
  expect(screen.getByText('数据不足，未调用模型')).toBeTruthy();
  view.rerender(<AssessmentDetails decision={{...decision, assessment_status:'DECIDED', action:'HOLD', reason_code:'NO_EDGE'}}/>);
  expect(screen.getByText('有效分析')).toBeTruthy();
  view.rerender(<AssessmentDetails decision={{id:'old'} as Decision}/>);
  expect(screen.getByText(/历史建议/)).toBeTruthy();
});
it('shows exact target and exit without treating ceiling as allocation', () => {
  render(<AssessmentDetails decision={{assessment_status:'DECIDED', proposal:{action:'SET_TARGET',target:{side:'LONG', sizing:'QUOTE_NOTIONAL',value:'500'},exit_plan:{stop_price:'90',review_due_at:'2026-10-28T12:00:00Z'}, valid_until:'2026-10-28T11:00:00Z'}} as unknown as Decision}/>);
  expect(screen.getByText(/500/)).toBeTruthy();
  expect(screen.getByText(/90/)).toBeTruthy();
});
