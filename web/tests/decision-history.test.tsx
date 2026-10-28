// @vitest-environment jsdom
import {afterEach,it,expect,vi} from 'vitest';
import {render,screen,fireEvent,cleanup} from '@testing-library/react';
import {DecisionHistory} from '../src/runtime/decisionhistory';
import type {Decision} from '../src/runtime/types';
afterEach(cleanup);
const items=Array.from({length:55},(_,i)=>({id:`d${i}`} as Decision));
const item=(d:Decision)=><span>{d.id}</span>;
it('pages 10 by default with 20 and 50 options and bounded scroll region',()=>{
 render(<DecisionHistory decisions={items} renderItem={item}/>);
 expect(screen.queryByText('d10')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'下一页'}));
 expect(screen.getByText('d10')).toBeTruthy();expect(screen.queryByText('d0')).toBeNull();
 fireEvent.change(screen.getByLabelText('每页记录数'),{target:{value:'20'}});
 expect(screen.getByText('d19')).toBeTruthy();expect(screen.queryByText('d20')).toBeNull();
 fireEvent.change(screen.getByLabelText('每页记录数'),{target:{value:'50'}});
 expect(screen.getByText('d49')).toBeTruthy();expect(screen.queryByText('d50')).toBeNull();
 expect(screen.getByRole('region',{name:'决策流水记录'}).className).toContain('decision-history-scroll');
});
it('opens the page containing an external notification selection and clamps after shrink',()=>{
 const v=render(<DecisionHistory decisions={items} selectedId="d52" renderItem={item}/>);
 expect(screen.getByText('d52')).toBeTruthy();
 expect((screen.getByRole('button',{name:'下一页'}) as HTMLButtonElement).disabled).toBe(true);
 v.rerender(<DecisionHistory decisions={items.slice(0,3)} selectedId="d52" renderItem={item}/>);
 expect(screen.getByText('d0')).toBeTruthy();
});
