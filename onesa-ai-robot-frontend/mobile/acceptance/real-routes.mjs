// Driven only by test_native_operator.py with a disposable loopback application.
// The provider is synthetic; this is not installed-device or hosted acceptance.
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {setTimeout as wait} from 'node:timers/promises';
import {createOperator} from '../operator.mjs';

const origin = new URL(process.argv[2]);
assert.equal(origin.hostname, '127.0.0.1');
assert.equal(origin.protocol, 'http:');
const control = process.env.ONESA_SYNTHETIC_NATIVE_CONTROL;
assert.ok(control);
const cookies = new Map(), storage = new Map(), requests = [];
let offline = false, holdProposal = null;
async function request(path, {method='GET', body, csrf} = {}, browser = false) {
  const response = await fetch(new URL(path, origin), {method, redirect:'manual',
    headers:{Origin:origin.origin, ...(browser ? {} : {Cookie:[...cookies].map(([k,v])=>k+'='+v).join('; ')}),
      ...(body ? {'Content-Type':'application/json'} : {}), ...(csrf ? {'X-CSRF-Token':csrf} : {})},
    ...(body ? {body:JSON.stringify(body)} : {}), signal:AbortSignal.timeout(10000)});
  if (!browser) for (const cookie of response.headers.getSetCookie()) {
    const [pair] = cookie.split(';'); const index = pair.indexOf('=');
    const name = pair.slice(0,index), value = pair.slice(index+1);
    if (/max-age=0/i.test(cookie)) cookies.delete(name); else cookies.set(name,value);
  }
  return response;
}
async function api(path, options = {}) {
  if (offline) throw Error('Synthetic offline transport');
  requests.push({path,method:options.method||'GET'});
  const response = await request('/api/v1'+path,options);
  if (!response.ok) throw Object.assign(Error('Synthetic service denial'),{status:response.status});
  const result = await response.json();
  if (path === '/tasks' && options.method === 'POST' && holdProposal) await holdProposal;
  return result;
}
async function fixture(action, values = {}) {
  const response = await fetch(new URL('/_synthetic_native',origin), {method:'POST',
    headers:{Origin:origin.origin,'Content-Type':'application/json','X-Synthetic-Control':control},
    body:JSON.stringify({action,...values}),signal:AbortSignal.timeout(10000)});
  assert.equal(response.status,200); return response.json();
}
async function openBrowser(url) {
  const authorization = new URL(url);
  assert.equal(authorization.origin,'https://auth.onesa.eu'); assert.equal(authorization.pathname,'/authorize');
  const parameters = Object.fromEntries(authorization.searchParams);
  const {code} = await fixture('mint',{parameters});
  const callback = '/api/v1/auth/identity/callback?'+new URLSearchParams({state:parameters.state,code});
  // The browser has a distinct cookie jar; redemption remains in the app jar.
  const response = await request(callback,{},true);
  assert.equal(response.status,303);
  return {type:'success',url:response.headers.get('location')};
}
const create = () => createOperator({api,openBrowser,uuid:randomUUID,
  storage:{get:async key=>storage.get(key),set:async(key,value)=>{storage.set(key,value);},remove:async key=>{storage.delete(key);}}});
let model = create();
try {
  await model.start(); assert.equal(model.snapshot().account,null);
  await model.connect(true); assert.ok(model.snapshot().canReset);
  const stable = model.snapshot().account.id;
  await model.command('reset'); // Real same-account MFA, without replaying reset.
  assert.equal(requests.filter(x=>x.path==='/reset').length,0);
  assert.ok(model.snapshot().account, model.snapshot().error);
  assert.equal(model.snapshot().account.id,stable);
  await model.command('reset'); assert.ok(model.snapshot().canPropose);
  await model.command('propose'); const first = model.snapshot().selected.id;
  await model.command('approve',first);
  await wait(2300); await model.refresh();
  assert.equal(model.snapshot().selected.state,'completed');
  assert.ok(requests.filter(x=>x.path==='/heartbeat').length>=2);
  model.dispose(); model=create(); await model.start();
  assert.equal(model.snapshot().account.id,stable); assert.equal(model.snapshot().selected.id,first);
  await model.command('propose'); const backgroundTask=model.snapshot().selected.id;
  await model.command('approve',backgroundTask); model.foregroundChanged(false);
  const heartbeats=requests.filter(x=>x.path==='/heartbeat').length;
  await wait(1250); assert.equal(requests.filter(x=>x.path==='/heartbeat').length,heartbeats);
  model.foregroundChanged(true); await model.refresh();
  assert.equal(model.snapshot().selected.state,'cancelled'); assert.ok(model.snapshot().canReset);
  await model.command('reset');
  let release; holdProposal=new Promise(resolve=>{release=resolve;});
  const pending=model.command('propose'); await wait(100);
  await model.stop(); assert.match(model.snapshot().message,/Stop verified/);
  release(); await pending; holdProposal=null;
  assert.equal(model.snapshot().canPropose,false);
  await model.command('reset'); await model.command('propose');
  const offlineTask=model.snapshot().selected.id; await model.command('approve',offlineTask);
  offline=true; await model.refresh(); assert.equal(model.snapshot().available,false);
  const posts=requests.filter(x=>x.method==='POST').length;
  await model.command('propose'); assert.equal(requests.filter(x=>x.method==='POST').length,posts);
  await model.stop(); assert.match(model.snapshot().error,/Stop not verified/);
  await wait(1250); offline=false; await model.refresh();
  assert.equal(model.snapshot().selected.state,'cancelled');
  await fixture('expire'); await model.refresh();
  assert.equal(model.snapshot().account,null); assert.deepEqual(model.snapshot().items,[]);
  await model.connect(true); assert.equal(model.snapshot().account.id,stable);
  assert.equal(model.snapshot().selected.id,offlineTask);
  await model.logout(); assert.equal(model.snapshot().account,null);
  await fixture('viewer'); await model.connect(true); assert.equal(model.snapshot().account.role,'viewer');
  const before=requests.filter(x=>x.method==='POST').length;
  await model.command('reset'); await model.command('propose');
  assert.equal(requests.filter(x=>x.method==='POST').length,before);
  await model.stop(); assert.match(model.snapshot().message,/Stop verified/);
  await model.logout();
  assert.equal((await request('/api/v1/auth/me')).status,401);
  console.log('PASS native controller real-route software acceptance');
} finally { model.dispose(); }
