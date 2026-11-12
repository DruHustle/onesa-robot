import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

// Execute the reviewed module offline; focus deliberately remains in another
// form, as it can during WebKit button clicks. No backend or credentials enter.
async function fixture() {
 const source=(await readFile(new URL('../public/app.js',import.meta.url),'utf8')).replace(/^import .*$/gm,'');
 const handlers=new Map(),nodes=new Map(),calls={reset:0,stop:0,logout:0,cancel:0,login:0};
 const form={querySelectorAll:()=>[],setAttribute(){},removeAttribute(){}};
 const unrelated={querySelectorAll:()=>[],setAttribute(){},removeAttribute(){}};
 let resolveReset;
 const reset=new Promise(resolve=>resolveReset=resolve);
 const node=id=>{if(!nodes.has(id))nodes.set(id,{id,hidden:false,value:'fixture-only',textContent:'',focus(){},addEventListener(event,handler){handlers.set(id+':'+event,handler);},replaceChildren(){},append(){}});return nodes.get(id);};
 class RobotClient {
  constructor(){this.pending={signal:new AbortController().signal};}
  me(){return Promise.reject({status:401});}
  login(){calls.login++;return Promise.resolve({});}
  reset(){calls.reset++;return reset;}
  stop(){calls.stop++;return Promise.resolve({});}
  logout(){calls.logout++;return Promise.resolve({});}
  cancel(){calls.cancel++;return Promise.resolve({});}
 }
 const context={RobotClient,createInspection:()=>({reset(){},authenticate(){},stale(){},refresh(){},hasPending(){return false;}}),document:{addEventListener(){},getElementById:node,activeElement:{closest:()=>unrelated},querySelectorAll:()=>[],createElement:()=>node('li')},window:{addEventListener(){}},setInterval(){return 1;},clearInterval(){},AbortController,AbortSignal,setTimeout,Date,performance};
 vm.createContext(context);vm.runInContext(source,context);
 function submit(){const event={currentTarget:form,preventDefault(){}};handlers.get('reset-form:submit')(event);event.currentTarget=null;}
 const tick=()=>new Promise(resolve=>setImmediate(resolve));
 return {calls,handlers,nodes,form,context,submit,resolveReset,tick};
}

for(const [button,method] of [['logout','logout'],['stop','stop'],['cancel','cancel']])test(`${button} remains independent of a pending originating form`,async()=>{
 const f=await fixture();f.context.document.activeElement={closest:()=>f.form};f.submit();
 // Focus can move independently of the action whose response remains pending.
 f.context.document.activeElement={closest:()=>f.form};
 f.handlers.get(button+':click')();await f.tick();
 assert.equal(f.calls[method],1);assert.equal(f.calls.reset,1);
 if(button==='logout')assert.equal(f.nodes.get('login-form').hidden,false);
 f.resolveReset({});await f.tick();
});

test('the originating form rejects duplicate submission regardless of focus',async()=>{
 const f=await fixture();f.submit();f.context.document.activeElement={closest:()=>null};f.submit();
 assert.equal(f.calls.reset,1);
 f.resolveReset({});await f.tick();f.submit();await f.tick();
 assert.equal(f.calls.reset,2);
});


test('a journal recovery response stops sign-in before sending credentials', async () => {
 const f = await fixture();
 await f.tick();
 let readinessChecks = 0;
 f.context.fetch = async (url) => {
  assert.equal(url, '/api/v1/ready');
  readinessChecks++;
  return Response.json({detail:'Remote simulation journal requires recovery'}, {status:503});
 };
 f.handlers.get('login-form:submit')({currentTarget:{...f.form,id:'login-form'}, preventDefault(){}});
 await f.tick();
 assert.equal(readinessChecks, 1);
 assert.equal(f.calls.login, 0);
 assert.match(f.nodes.get('notice').textContent, /journal needs recovery.*Contact Onesa support/);
 assert.equal(f.nodes.get('password').value, 'fixture-only');
});
