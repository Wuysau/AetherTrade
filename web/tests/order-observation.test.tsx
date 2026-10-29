// @vitest-environment jsdom
import {afterEach,it,expect,vi} from 'vitest';
import {render,screen,fireEvent,cleanup,waitFor} from '@testing-library/react';
import {OrderProgramDetail,OrderProgramObservation} from '../src/runtime/orderobservation';
import {RuntimeApi} from '../src/runtime/api';
afterEach(cleanup);
const plan:any={program_id:'original-limit',scope_id:'server-scope',contract_version:'2026-10-29',codec_status:'DECODED',source:'order_programs',canonical_payload_hash:'original-hash',execution_authorized:false,execution_state:'SUBMIT_UNKNOWN',execution_mode:'VENUE_NATIVE',template:'ATOMIC',children_complete:null,protection_status:'UNVERIFIED_DUTY',payload:{management_policy:'MANAGE_UNTIL_FLAT'},nodes:[{node_id:'entry',role:'ENTRY',child_origin:'APPLICATION_GENERATED_CHILD',order:{side:'BUY',quantity:{kind:'QUOTE_NOTIONAL',value:'4000'},price:{kind:'FIXED_LIMIT',value:'81490'},tif:{kind:'GTC'},trigger:{kind:'STATIC',source:'MARK_PRICE',price:'81330'}}}],records:{commands:[{record_id:'cmd-original',payload:{state:'MAY_HAVE_SENT',venue_client_id:'client-original'}}],states:[],financial:[{record_id:'generic-program-admission:intent',payload:{intent_id:'intent',attachment:{command_ids:['cmd-original']}}}]},capability:{current_qualification:'UNKNOWN',admission_blockers:['HOST_ADMISSION_NOT_REGISTERED'],archived_snapshot:{qualification:'PAPER_VERIFIED',snapshot_id:'frozen-7'}},rejection_reasons:[{stage:'RULE',code:'RULE_REVISION_CHANGED'}],truncated:{}};
it('shows full plan and separates committed receipt from MAY and venue observations',()=>{
 render(<OrderProgramDetail detail={plan}/>);
 for(const text of ['81490','81330','4000','MARK_PRICE','GTC','MAY_HAVE_SENT','client-original','原计划','持久化接入回执','SUBMIT_UNKNOWN','UNVERIFIED_DUTY','RULE_REVISION_CHANGED']) expect(screen.getAllByText(text,{exact:false}).length).toBeGreaterThan(0);
 expect(screen.getByText('回执仅证明账本接入已提交；每次发送仍需当前授权与单次许可。')).toBeTruthy();
 expect(screen.getByText('尚未取得完整子单清单；空列表不能证明空仓或责任结束。')).toBeTruthy();
 expect(screen.queryByRole('button',{name:/执行|下单/})).toBeNull();
});
it('unknown versions retain archive without invented node semantics',()=>{
 render(<OrderProgramDetail detail={{...plan,codec_status:'UNSUPPORTED_VERSION',nodes:[]}}/>);
 expect(screen.getByText('未知或无效版本：保留原始记录，仅供只读核查。')).toBeTruthy();
 expect(screen.queryByText('81490',{exact:true})).toBeNull();
});
it('uses read-only server pages at 10/20/50 and does not infer empty as flat',async()=>{
 const fetcher=vi.fn().mockImplementation(async(path:string)=>new Response(JSON.stringify(path.includes('capabilities')?{features:[],current_qualification:'UNKNOWN',admission_blockers:[],expression_versions:{}}:{items:[],scopes:[],next_cursor:null,observation_status:'UNCONFIGURED'})));
 render(<OrderProgramObservation api={new RuntimeApi(fetcher)}/>);
 await screen.findByText('只读订单账本尚未配置');
 fireEvent.change(screen.getByLabelText('订单计划每页记录数'),{target:{value:'20'}});
 await waitFor(()=>expect(fetcher.mock.calls.some(([p])=>p.includes('/order-programs?limit=20'))).toBe(true));
 expect(screen.getByRole('option',{name:'50'})).toBeTruthy();
 expect(fetcher.mock.calls.every(([,init])=>!init.method||init.method==='GET')).toBe(true);
});
