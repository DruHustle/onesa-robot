import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const tick=()=>new Promise(resolve=>setImmediate(resolve));
const status={mode:'simulation',physical_actuation:false,stopped:false,position_radians:0.1,active_task:null,sampled_at:1,deployment_environment:'local',capabilities:{edge_telemetry:'not_connected'}};
const tasks={tasks:[{id:'synthetic-unit-task',state:'completed',reason:'operator_approved',created:1,position:0.1}]};
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}

async function fixture(observationDelayMs=0){
 const source=(await readFile(new URL('../public/app.js',import.meta.url),'utf8')).replace(/^import .*$/gm,'');
 const observed=[],nodes=new Map(),windows=new Map(),documents=new Map(),pending={heartbeat:[],status:[],tasks:[]},calls={heartbeat:0,status:0,tasks:0,cancelPending:0};let restoredSession=null;
 const node=id=>{if(!nodes.has(id))nodes.set(id,{id,hidden:false,value:'synthetic-only',textContent:'',children:[],focus(){},addEventListener(){},replaceChildren(...children){this.children=children;},append(child){this.children.push(child);}});return nodes.get(id);};
 function request(operation,value){const result=deferred();pending[operation].push(result);calls[operation]++;if(observationDelayMs&&operation!=='heartbeat')setTimeout(()=>result.resolve(value),observationDelayMs);return result.promise;}
 class RobotClient{
  constructor(){this.pending=new AbortController();}
  me(){return restoredSession?Promise.resolve(restoredSession):Promise.reject({status:401});}
  heartbeat(){const result=request('heartbeat',{});if(observationDelayMs)pending.heartbeat.at(-1).resolve({});return result;}
  status(){return request('status',status);}
  tasks(){return request('tasks',tasks);}
  cancelPending(){calls.cancelPending++;this.pending.abort();this.pending=new AbortController();}
 }
 const context={RobotClient,createInspection:()=>({reset(){},authenticate(){},stale(){},refresh(value){observed.push(value);},hasPending(){return false;}}),document:{hidden:false,addEventListener(event,handler){documents.set(event,handler);},getElementById:node,querySelectorAll:()=>[],createElement:()=>({textContent:'',append(){}})},window:{addEventListener(event,handler){windows.set(event,handler);}},setInterval(){return 1;},clearInterval(){},setTimeout,AbortController,AbortSignal,Date,performance};
 node('history');vm.createContext(context);vm.runInContext(source,context);await tick();
 vm.runInContext("csrf='synthetic-session';client.csrf=csrf;byId('operator').hidden=false;byId('login-form').hidden=true;",context);
 return {calls,pending,nodes,windows,documents,context,observed,action(action){context.controlledAction=action;return vm.runInContext("run(controlledAction,'Synthetic action completed.')",context);},restoreSession(session){restoredSession=session;},refresh:()=>vm.runInContext('refresh()',context)};
}

for(const failed of ['status','tasks'])test(`failed ${failed} observations cannot renew execution supervision`,async()=>{
 const f=await fixture();const refreshing=f.refresh();
 // Release the old heartbeat-first implementation so the regression completes
 // and reports its unsafe renewal instead of hanging behind its first request.
 for(const item of f.pending.heartbeat)item.resolve({});await tick();
 f.pending[failed][0].reject(Object.assign(new Error('Synthetic observation unavailable'),{status:503}));
 if(failed==='status')f.pending.tasks[0].resolve(tasks);else f.pending.status[0].resolve(status);
 await refreshing;
 assert.equal(f.calls.heartbeat,0,'A failed observation must not prolong an active simulation');
 assert.match(f.nodes.get('connection').textContent,/Disconnected/);
});

for(const [label,snapshot,history] of [
 ['physical activation',{...status,physical_actuation:true},tasks],
 ['unlabelled mode',{...status,mode:undefined},tasks],
 ['invalid position',{...status,position_radians:'unknown'},tasks],
 ['invalid history',status,{tasks:[{...tasks.tasks[0],position:null}]}],
])test(`${label} cannot renew supervision or enable controls`,async()=>{
 const f=await fixture();const refreshing=f.refresh();
 f.pending.status[0].resolve(snapshot);f.pending.tasks[0].resolve(history);await refreshing;
 assert.equal(f.calls.heartbeat,0);assert.equal(f.nodes.get('proposal-submit').disabled,true);
 assert.match(f.nodes.get('connection').textContent,/Disconnected/);
});

test('both observations finish before execution supervision is renewed',async()=>{
 const f=await fixture();const refreshing=f.refresh();
 assert.equal(f.calls.heartbeat,0);assert.equal(f.calls.status,1);assert.equal(f.calls.tasks,1);
 assert.equal(f.calls.status,1);assert.equal(f.calls.tasks,1);
 f.pending.tasks[0].resolve(tasks);await tick();assert.equal(f.nodes.get('history').children.length,0);assert.equal(f.calls.heartbeat,0);
 f.pending.status[0].resolve(status);await tick();assert.equal(f.calls.heartbeat,1);
 f.pending.heartbeat[0].resolve({});await refreshing;
 assert.match(f.nodes.get('history').children[0].textContent,/completed/);
 assert.deepEqual(f.calls,{heartbeat:1,status:1,tasks:1,cancelPending:0});
});

test('controlled observation latency is paid once per refresh rather than once per read',async t=>{
 const f=await fixture(1200);const started=performance.now();const refreshing=f.refresh();await refreshing;
 const elapsed=Math.round(performance.now()-started);t.diagnostic('Controlled 1200ms status/tasks observation latency: '+elapsed+'ms total');
 assert.ok(elapsed<2000,'independent reads must not add their latency sequentially');
 assert.match(f.nodes.get('history').children[0].textContent,/completed/);
 assert.deepEqual(f.calls,{heartbeat:1,status:1,tasks:1,cancelPending:0});
});

test('a late unauthorized observation cannot revoke a replacement session',async()=>{
 const f=await fixture();const refreshing=f.refresh();await tick();
 f.pending.status[0].resolve(status);await tick();
 vm.runInContext("signedOut();generation++;csrf='replacement-synthetic-session';client.csrf=csrf;byId('operator').hidden=false;byId('login-form').hidden=true;notice('Replacement session');",f.context);
 f.pending.tasks[0].reject(Object.assign(new Error('Previous synthetic session'),{status:401}));await refreshing;
 assert.equal(f.nodes.get('operator').hidden,false);assert.equal(f.nodes.get('login-form').hidden,true);assert.equal(f.nodes.get('notice').textContent,'Replacement session');assert.equal(f.nodes.get('history').children.length,0);
});

test('navigation cancels the observation lifecycle and prevents a new polling request',async()=>{
 const f=await fixture();const refreshing=f.refresh();await tick();
 f.pending.status[0].resolve(status);await tick();
 f.windows.get('pagehide')();f.pending.tasks[0].reject(Object.assign(new Error('Synthetic navigation'),{name:'AbortError'}));await refreshing;await f.refresh();
 assert.equal(f.calls.cancelPending,1);assert.equal(f.calls.heartbeat,0);assert.equal(f.nodes.get('history').children.length,0);
});

test('a fast read failure retains one polling batch until its other read settles',async()=>{
 const f=await fixture();const refreshing=f.refresh();await tick();
 f.pending.status[0].reject(Object.assign(new Error('Synthetic read unavailable'),{status:503}));await tick();
 assert.match(f.nodes.get('connection').textContent,/Disconnected/);
 const duplicate=f.refresh();await tick();assert.equal(f.calls.heartbeat,0);assert.equal(f.calls.status,1);assert.equal(f.calls.tasks,1);
 f.pending.tasks[0].resolve(tasks);await refreshing;await duplicate;
 const next=f.refresh();assert.equal(f.calls.heartbeat,0);f.pending.status[1].resolve(status);f.pending.tasks[1].resolve(tasks);await tick();f.pending.heartbeat[0].resolve({});await next;
});

test('an obsolete polling batch cannot clear ownership of a restored-page batch',async()=>{
 const f=await fixture();const old=f.refresh();await tick();
 f.windows.get('pagehide')();f.restoreSession({csrf:'restored-synthetic-session'});f.windows.get('pageshow')({persisted:true});await tick();
 assert.equal(f.calls.heartbeat,0);assert.equal(f.calls.tasks,2);
 f.pending.status[0].resolve(status);f.pending.tasks[0].resolve(tasks);await old;
 const duplicate=f.refresh();await tick();assert.equal(f.calls.heartbeat,0);assert.equal(f.calls.status,2);assert.equal(f.calls.tasks,2);
 f.pending.status[1].resolve(status);f.pending.tasks[1].resolve(tasks);await tick();f.pending.heartbeat[0].resolve({});await duplicate;
 assert.equal(f.nodes.get('operator').hidden,false);assert.equal(f.nodes.get('login-form').hidden,true);
});


test('operator guidance follows verified stop/running/proposal state and does not imply movement from readiness',async()=>{
 const f=await fixture();
 for(const [status,proposal,expected] of [[null,'','Waiting'],[{},'','Waiting'],[{stopped:true,active_task:null},'','Stopped'],[{stopped:false,active_task:'owned-synthetic'},'','Simulation running'],[{stopped:false,active_task:null},'owned-proposal','Review'],[{stopped:false,active_task:null},'','Ready']]){
  f.context.observed=status;f.context.proposal=proposal;assert.ok(vm.runInContext('operatorNextStep(observed,proposal)',f.context).startsWith(expected));
 }
 assert.equal(f.calls.heartbeat,0);assert.equal(f.calls.status,0);assert.equal(f.calls.tasks,0);
});

 test('hidden documents cancel observation ownership and make no further polls',async()=>{const f=await fixture();f.context.document.hidden=true;f.documents.get('visibilitychange')();await f.refresh();assert.equal(f.calls.cancelPending,1);assert.equal(f.calls.heartbeat,0);assert.equal(f.calls.status,0);assert.equal(f.calls.tasks,0);});

test('returning to a hidden page with expired authority clears authenticated presentation',async()=>{const f=await fixture();f.context.document.hidden=true;f.documents.get('visibilitychange')();f.context.document.hidden=false;f.documents.get('visibilitychange')();await tick();assert.equal(f.nodes.get('operator').hidden,true);assert.equal(f.nodes.get('login-form').hidden,false);assert.equal(f.calls.heartbeat,0);});

async function settleControlledReads(f) {
 for (let turn=0;turn<3;turn++) {
  for (const item of f.pending.heartbeat) item.resolve({});
  for (const item of f.pending.status) item.resolve(status);
  for (const item of f.pending.tasks) item.resolve(tasks);
  await tick();
 }
}

test('a completed action waits for a fresh snapshot instead of accepting an older in-flight poll',async()=>{
 const f=await fixture(),old=f.refresh();
 await tick();
 let completed=false;
 const action=f.action(async()=>{}).then(()=>{completed=true;});
 try {
  await tick();assert.equal(completed,false,'The action must wait for its post-action observation');
  f.pending.status[0].resolve({...status,stopped:true});f.pending.tasks[0].resolve(tasks);
  await old;await tick();
  assert.equal(f.observed.length,0,'A pre-action observation must not restore command readiness');
  assert.equal(f.calls.heartbeat,0);
  f.pending.status[1].resolve(status);f.pending.tasks[1].resolve(tasks);await tick();
  f.pending.heartbeat[0].resolve({});await action;
  assert.equal(completed,true);assert.deepEqual(f.observed.map(value=>value.stopped),[false]);
  assert.equal(f.calls.status,2);assert.equal(f.calls.tasks,2);
 } finally {await settleControlledReads(f);await Promise.allSettled([old,action]);}
});

test('a current-session authorization failure still signs out during a post-action handoff',async()=>{
 const f=await fixture(),old=f.refresh();
 await tick();
 const action=f.action(async()=>{});await tick();
 try {
  f.pending.status[0].reject(Object.assign(new Error('Synthetic expired authority'),{status:401}));
  f.pending.tasks[0].resolve(tasks);await old;await action;
  assert.equal(f.nodes.get('operator').hidden,true);assert.equal(f.nodes.get('login-form').hidden,false);
  assert.equal(f.calls.heartbeat,0,'Expired authority must not renew execution supervision');
 } finally {await settleControlledReads(f);await Promise.allSettled([old,action]);}
});
