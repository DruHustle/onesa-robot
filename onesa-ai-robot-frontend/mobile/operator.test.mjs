import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createOperator, taskLink} from './operator.mjs';

const id = '00000000-0000-4000-8000-000000000001';
const secondId = '00000000-0000-4000-8000-000000000002';
const accountId = 'robot_' + 'a'.repeat(64);
const otherAccount = 'robot_' + 'b'.repeat(64);
const task = (state = 'proposed') => ({id, state, position:0, created:1000, reason:'synthetic'});
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a; reject=b;}); return {promise,resolve,reject}; };
function fixture(options = {}) {
  let time = 1000000, elapsed = 0, nextTimer = 1;
  const timers = new Map(), saved = new Map(), seen = [];
  const user = {id:accountId,name:'Synthetic Operator',csrf:'synthetic-csrf',role:'operator',authentication_method:'onesa',confirmation_expires_at:1300};
  const status = {mode:'simulation',physical_actuation:false,stopped:true,active_task:null,position_radians:0,sampled_at:1000};
  const whole = {connection:'simulated_actuators',physical_actuation:false,stopped:true,active_command:null,pending:null};
  let tasks = [], intercept = null;
  const api = async (path, value = {}) => {
    seen.push({path,...value});
    const result = intercept?.(path,value);
    if (result !== undefined) return result;
    if (path === '/auth/me') return {...user};
    if (path === '/status') return {...status};
    if (path === '/humanoid/state') return {...whole};
    if (path === '/tasks' && value.method !== 'POST') return {tasks:tasks.map(t=>({...t}))};
    if (path === '/heartbeat') return {status:'received',physical_actuation:false};
    if (path === '/stop') {
      status.stopped = whole.stopped = true; status.active_task = null;
      tasks = tasks.map(t=>({...t,state:'cancelled'}));
      return {status:'stopped',physical_actuation:false};
    }
    if (path === '/reset') { status.stopped = whole.stopped = false; return {status:'reset',physical_actuation:false}; }
    if (path === '/tasks' && value.method === 'POST') { tasks = [task()]; return {id,state:'proposed'}; }
    if (path === '/tasks/'+id+'/approve') { tasks = [task('running')]; status.active_task = id; return {id,state:'running'}; }
    if (path === '/tasks/'+id+'/cancel') { tasks = [task('cancelled')]; status.active_task = null; return {status:'cancelled'}; }
    if (path === '/auth/logout') return {status:'signed_out'};
    if (path === '/auth/identity/start') return {authorization_url:'https://auth.onesa.eu/authorize?state=native_'+ 'S'.repeat(43)+'&code_challenge_method=S256'};
    if (path === '/auth/identity/mobile/exchange') { user.confirmation_expires_at = time/1000+300; user.csrf='rotated-csrf'; return {}; }
    throw Error('Unexpected synthetic endpoint '+path);
  };
  const model = createOperator({api, storage:{get:async key=>saved.get(key),set:async(key,value)=>{saved.set(key,value);},remove:async key=>{saved.delete(key);}},
    uuid:()=>id, now:()=>time, monotonic:()=>elapsed,
    openBrowser:async url=>({type:'success',url:'onesarobot://account/callback?state='+new URL(url).searchParams.get('state')+'&code=synthetic'}),
    schedule:(fn,delay)=>{const n=nextTimer++;timers.set(n,{fn,at:elapsed+delay});return n;},unschedule:n=>timers.delete(n),...options});
  return {model,seen,user,status,whole,saved,timers,
    tasks:value=>{tasks=value;},intercept:value=>{intercept=value;},
    advance:async ms=>{elapsed+=ms;time+=ms;for(const [n,timer] of [...timers])if(timer.at<=elapsed){timers.delete(n);timer.fn();}await new Promise(resolve=>setImmediate(resolve));},
    ready:async()=>{status.stopped=whole.stopped=false;await model.start();},
    running:()=>{status.stopped=whole.stopped=false;status.active_task=id;tasks=[task('running')];}};
}

test('only an exact protected-task link is accepted; callbacks carry no navigation authority', () => {
  assert.equal(taskLink('onesarobot://tasks/'+id),id);
  for (const url of ['https://tasks/'+id,'onesarobot://account/callback?code=x','onesarobot://tasks/'+id+'?next=other',
    'onesarobot://tasks/'+id+'#x','onesarobot://person@tasks/'+id,'onesarobot://tasks/'+id+'/extra']) assert.equal(taskLink(url),null);
});

test('restoration discards unbound legacy CSRF; identity has no display-name fallback', async () => {
  const f=fixture(); f.saved.set('csrf','stale-unbound'); delete f.user.id;
  await f.model.start();
  assert.equal(f.saved.has('csrf'),false); assert.equal(f.model.snapshot().account,null);
  assert.equal(f.seen.some(x=>x.method==='POST'),false);
  f.model.dispose();
});

test('current task and whole-body observations gate reset, proposal and approval', async () => {
  const f=fixture(); await f.model.start();
  assert.equal(f.model.snapshot().canReset,true); assert.equal(f.model.snapshot().canPropose,false);
  await f.model.command('reset'); assert.equal(f.model.snapshot().canReset,false);
  await f.model.command('reset'); assert.equal(f.seen.filter(x=>x.path==='/reset').length,1);
  await f.model.command('propose'); assert.equal(f.model.snapshot().selected.id,id);
  assert.equal(f.model.snapshot().canApprove,true);
  await f.model.command('approve',id); assert.equal(f.model.snapshot().selected.state,'running');
  assert.ok(f.seen.some(x=>x.path==='/heartbeat' && x.csrf==='synthetic-csrf'));
  f.model.dispose();
});

test('a confirmation belongs to its reviewed lifecycle and account and can be consumed only once', async () => {
  const f=fixture(); await f.model.start();
  const stale=f.model.review('reset');
  f.model.foregroundChanged(false); f.model.foregroundChanged(true); await f.model.refresh();
  await stale(); assert.equal(f.seen.some(x=>x.path==='/reset'),false);
  const accountReview=f.model.review('reset');
  f.user.id=otherAccount; await f.model.connect(true);
  await accountReview(); assert.equal(f.seen.some(x=>x.path==='/reset'),false);
  const current=f.model.review('reset'); await current(); await current();
  assert.equal(f.seen.filter(x=>x.path==='/reset').length,1);
  f.model.dispose();
});

test('foreground observation repeats without overlap; background and stale reads cannot renew supervision', async () => {
  const f=fixture(); f.running(); await f.model.start();
  const initial=f.seen.filter(x=>x.path==='/heartbeat').length;
  await f.advance(500); assert.equal(f.seen.filter(x=>x.path==='/heartbeat').length,initial+1);
  const held=deferred(); f.intercept(path=>path==='/tasks'?held.promise:undefined);
  const first=f.model.refresh(); await new Promise(resolve=>setImmediate(resolve));
  const second=f.model.refresh();
  f.model.foregroundChanged(false); held.resolve({tasks:[task('running')]}); await Promise.all([first,second]);
  assert.equal(f.seen.filter(x=>x.path==='/heartbeat').length,initial+1);
  assert.equal(f.model.snapshot().available,false);
  assert.equal(f.model.snapshot().canApprove,false);
  f.model.dispose();
});

test('loss of any independent observation denies mutations and does not send a heartbeat', async () => {
  for (const route of ['/auth/me','/status','/tasks','/humanoid/state']) {
    const f=fixture(); await f.ready(); f.running();
    f.intercept(path=>path===route?Promise.reject(Error('offline')):undefined);
    await f.model.refresh();
    const count=f.seen.length;
    await f.model.command('propose'); await f.model.command('reset');
    assert.equal(f.seen.length,count); assert.equal(f.model.snapshot().available,false);
    assert.equal(f.seen.some(x=>x.path==='/heartbeat'),false);
    f.model.dispose();
  }
});

test('a failed batch waits for its other bounded reads before starting another batch', async () => {
  const f=fixture(); await f.ready(); const held=deferred();
  f.intercept(path=>path==='/tasks'?held.promise:path==='/status'?Promise.reject(Error('offline')):undefined);
  const pending=f.model.refresh(); await f.advance(500);
  const count=f.seen.length;
  await f.advance(2000); assert.equal(f.seen.length,count);
  assert.equal(f.model.snapshot().available,false);
  held.resolve({tasks:[]}); await pending;
  f.model.dispose();
});

test('timed-out telemetry, malformed heartbeat and foreign active task never verify supervision', async () => {
  const f=fixture(); await f.ready(); f.running();
  f.intercept(path=>path==='/heartbeat'?{status:'received'}:undefined);
  await f.model.refresh(); assert.equal(f.model.snapshot().available,false);
  f.intercept(null); f.status.active_task=secondId; f.tasks([]);
  const before=f.seen.filter(x=>x.path==='/heartbeat').length;
  await f.model.refresh(); assert.equal(f.seen.filter(x=>x.path==='/heartbeat').length,before);
  assert.equal(f.model.snapshot().canPropose,false);
  f.running(); const held=deferred(); f.intercept(path=>path==='/tasks'?held.promise:undefined);
  const pending=f.model.refresh(); await f.advance(1600); held.resolve({tasks:[task('running')]}); await pending;
  assert.equal(f.model.snapshot().available,false); assert.equal(f.seen.filter(x=>x.path==='/heartbeat').length,before);
  f.model.dispose();
});

test('independent Stop fences a late mutation and verifies all readbacks', async () => {
  const f=fixture(); await f.ready(); const held=deferred();
  f.intercept((path,value)=>path==='/tasks'&&value.method==='POST'?held.promise:undefined);
  const mutation=f.model.command('propose');
  await f.model.stop(); assert.match(f.model.snapshot().message,/Stop verified/);
  held.resolve({id,state:'proposed'}); await mutation;
  assert.equal(f.model.snapshot().selected,null); assert.equal(f.model.snapshot().canPropose,false);
  assert.equal(f.seen.filter(x=>x.path==='/tasks'&&x.method==='POST').length,1);
  assert.equal(f.model.snapshot().busy,false); f.model.dispose();
});

test('offline Stop stays unverified after later successful background reads', async () => {
  const f=fixture(); await f.model.start();
  f.intercept(path=>path==='/stop'?Promise.reject(Error('offline')):undefined);
  await f.model.stop(); await f.model.refresh();
  assert.match(f.model.snapshot().error,/Stop not verified/); assert.equal(f.model.snapshot().message,'');
  f.model.dispose();
});

test('401 expiry clears private state, but the exact task returns only to the same stable account', async () => {
  const f=fixture(); await f.ready(); f.tasks([task()]); await f.model.refresh(); await f.model.select(id);
  f.intercept(path=>path==='/auth/me'?Promise.reject(Object.assign(Error('expired'),{status:401})):undefined);
  await f.model.refresh(); assert.equal(f.model.snapshot().account,null); assert.deepEqual(f.model.snapshot().items,[]);
  f.intercept(null); f.user.name='Renamed Operator'; await f.model.connect(true);
  assert.equal(f.model.snapshot().selected.id,id);
  f.user.id=otherAccount; f.user.name='Synthetic Operator'; await f.model.connect(true);
  assert.equal(f.model.snapshot().selected,null); assert.equal(f.saved.has('robot-task-return-v1'),false);
  f.model.dispose();
});

test('authenticated read cannot replace the current account by a matching display name', async () => {
  const f=fixture(); await f.ready(); f.tasks([task()]); await f.model.refresh(); await f.model.select(id);
  f.user.id=otherAccount; await f.model.refresh();
  assert.equal(f.model.snapshot().account,null); assert.deepEqual(f.model.snapshot().items,[]);
  assert.equal(f.seen.some(x=>x.path==='/heartbeat'),false); f.model.dispose();
});

test('MFA rotates CSRF for the same account and requires a new explicit confirmation without replay', async () => {
  const f=fixture(); await f.ready(); f.tasks([task()]); f.user.confirmation_expires_at=900;
  await f.model.refresh(); await f.model.select(id); await f.model.command('approve',id);
  assert.equal(f.seen.some(x=>x.path.endsWith('/approve')),false);
  assert.equal(f.model.snapshot().account.csrf,'rotated-csrf');
  assert.match(f.model.snapshot().message,/MFA confirmed.*again/);
  await f.model.command('approve',id);
  const approvals=f.seen.filter(x=>x.path.endsWith('/approve'));
  assert.equal(approvals.length,1); assert.equal(approvals[0].csrf,'rotated-csrf');
  f.model.dispose();
});

test('MFA account substitution fails closed; viewer never sends a mutation or supervision', async () => {
  const f=fixture(); await f.ready(); f.tasks([task()]); f.user.confirmation_expires_at=900;
  await f.model.refresh(); await f.model.select(id);
  f.intercept(path=>path==='/auth/identity/mobile/exchange'?(f.user.id=otherAccount, {}):undefined);
  await f.model.command('approve',id); assert.equal(f.model.snapshot().account,null);
  assert.equal(f.seen.some(x=>x.path.endsWith('/approve')),false); f.model.dispose();
  const viewer=fixture(); viewer.user.role='viewer'; viewer.running(); await viewer.model.start();
  await viewer.model.command('reset'); await viewer.model.command('propose');
  assert.equal(viewer.seen.some(x=>x.method==='POST'),false);
  await viewer.model.stop(); assert.match(viewer.model.snapshot().message,/Stop verified/); viewer.model.dispose();
});

test('logout fences late reads, blocks reconnect until its response, and persists explicit sign-out', async () => {
  const f=fixture(); await f.ready(); f.tasks([task()]); await f.model.refresh(); await f.model.select(id);
  const read=deferred(), logout=deferred();
  f.intercept(path=>path==='/tasks'?read.promise:path==='/auth/logout'?logout.promise:undefined);
  const observing=f.model.refresh(); await new Promise(resolve=>setImmediate(resolve));
  const leaving=f.model.logout(); await f.model.connect(true);
  read.resolve({tasks:[task()]}); await observing; logout.resolve({status:'signed_out'}); await leaving;
  assert.equal(f.model.snapshot().account,null); assert.deepEqual(f.model.snapshot().items,[]);
  assert.equal(f.saved.get('robot-signed-out-v1'),'1'); assert.equal(f.saved.has('robot-task-return-v1'),false);
  assert.equal(f.seen.some(x=>x.path==='/auth/identity/start'),false); f.model.dispose();
});

test('a protected deep link restores only the server-authorized exact task without mutation', async () => {
  const f=fixture(); f.tasks([task('completed')]); await f.model.start('onesarobot://tasks/'+id);
  assert.equal(f.model.snapshot().selected.id,id);
  assert.equal(f.seen.some(x=>x.method==='POST'),false);
  await f.model.link('onesarobot://tasks/'+secondId);
  assert.equal(f.model.snapshot().selected,null); assert.match(f.model.snapshot().error,/unavailable to this account/);
  f.model.dispose();
});
