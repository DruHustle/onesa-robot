test('native API origin defaults to the production Robot origin', () => {
  assert.equal(resolveApiOrigin(undefined), 'https://robot.onesa.eu');
  assert.equal(resolveApiOrigin(''), 'https://robot.onesa.eu');
  assert.equal(ORIGIN, resolveApiOrigin(process.env.EXPO_PUBLIC_ROBOT_API_ORIGIN));
});

test('CI native release candidates are pinned to the approved staging API', {skip: process.env.CI !== '1'}, () => {
  const staging = 'https://onesa-ai-robot-backend-staging.onrender.com';
  assert.equal(process.env.EXPO_PUBLIC_ROBOT_API_ORIGIN, staging);
  assert.equal(ORIGIN, staging);
});

test('native API origin supports the exact production and staging services', () => {
  assert.equal(resolveApiOrigin('https://robot.onesa.eu/'), 'https://robot.onesa.eu');
  assert.equal(
    resolveApiOrigin('https://onesa-ai-robot-backend-staging.onrender.com'),
    'https://onesa-ai-robot-backend-staging.onrender.com',
  );
});

test('native API origin rejects insecure, path-bearing and off-domain overrides', () => {
  for (const value of [
    'http://robot-staging.onesa.eu',
    'https://robot.onesa.eu/api/v1',
    'https://robot.onesa.eu?tenant=other',
    'https://robot.onesa.eu#fragment',
    'https://operator@robot.onesa.eu',
    'https://robot.onesa.eu.evil.example',
    'https://example.com',
    'https://robot-staging.onesa.eu:8443',
    'not-an-origin',
  ]) {
    assert.throws(() => resolveApiOrigin(value), /HTTPS.*Onesa origin/);
  }
});

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {call, signIn, CALLBACK, AUTH, stopSimulation, resetSimulation, resolveApiOrigin, ORIGIN} from './session.mjs';
const state = 'native_' + 'A'.repeat(43);
const url = 'https://auth.onesa.eu/authorize?state=' + state + '&code_challenge_method=S256';
for (const [scenario, value] of Object.entries({correct:CALLBACK+'?state='+state+'&code=fixture', stale:CALLBACK+'?state=wrong&code=fixture', foreign:'wrong://account/callback?state='+state+'&code=fixture', duplicate:CALLBACK+'?state='+state+'&code=fixture&code=other', cancelled:CALLBACK+'?state='+state+'&error=cancelled'})) {
  test(scenario + ' system-browser return', async () => {
    const paths=[];
    const api=async (path,opts)=>{
      paths.push(path);
      if(path===AUTH+'/start') return {authorize_url:url};
      assert.deepEqual(opts.body,{state,code:'fixture'});
      return {ok:true};
    };
    const result=signIn(async(authorize,back)=>{assert.equal(authorize,url);assert.equal(back,CALLBACK);return {type:'success',url:value};},api);
    if(scenario==='correct'){assert.deepEqual(await result,{ok:true});assert.equal(paths.length,2);}
    else {await assert.rejects(result);assert.equal(paths.length,1);}
  });
}

test('native sign-in supports same-account MFA step-up', async () => {
  const state = 'native_' + 'B'.repeat(43);
  const url = 'https://auth.onesa.eu/authorize?state=' + state +
    '&code_challenge_method=S256&prompt=login&max_age=0&acr_values=urn%3Aonesa%3Amfa';
  const seen = [];
  const api = async (path, options) => {
    seen.push({path, options});
    if (path === AUTH + '/start') return {authorization_url:url};
    assert.deepEqual(options.body, {state, code:'mfa-code'});
    return {signed_in:true};
  };
  const result = await signIn(async (authorize, callback) => {
    assert.equal(authorize, url);
    assert.equal(callback, CALLBACK);
    return {type:'success', url:CALLBACK+'?state='+state+'&code=mfa-code'};
  }, api, {stepUp:true, csrf:'synthetic-session-csrf'});
  assert.deepEqual(result, {signed_in:true});
  assert.deepEqual(seen[0].options.body, {mobile:true, step_up:true});
  assert.equal(seen[0].options.csrf, 'synthetic-session-csrf');
  assert.equal(seen[1].path, AUTH + '/mobile/exchange');
});

test('native requests abort stalled transport and body reads without delaying Stop or replaying commands', {timeout:30000}, async () => {
  const originalFetch = globalThis.fetch;
  const seen = [];
  let finishBody;
  let pending;
  globalThis.fetch = async (url, options) => {
    const path = new URL(url).pathname.replace('/api/v1', '');
    assert.ok(options.signal instanceof AbortSignal);
    assert.equal(options.signal.aborted, false);
    seen.push({path, options});
    if (path === '/tasks' && options.method === 'POST') {
      return new Promise((_, reject) => options.signal.addEventListener('abort', () => {
        reject(new DOMException('Synthetic transport cancelled', 'AbortError'));
      }, {once:true}));
    }
    if (path === '/stalled-body') {
      return {ok:true, status:200, json:() => new Promise(resolve => {finishBody = resolve;})};
    }
    if (path === '/denied') return {ok:false, status:403};
    const values = {
      '/stop': {status:'stopped', physical_actuation:false},
      '/status': {mode:'simulation', stopped:true, active_task:null, physical_actuation:false},
      '/tasks': {tasks:[{state:'cancelled'}]},
      '/humanoid/state': {connection:'simulated_actuators', stopped:true, active_command:null, pending:null, physical_actuation:false},
    };
    assert.ok(Object.hasOwn(values, path));
    return {ok:true, status:200, json:async () => values[path]};
  };
  try {
    const mutation = call('/tasks', {method:'POST', csrf:'synthetic-csrf', body:{confirmed:true}});
    const body = call('/stalled-body');
    pending = Promise.allSettled([mutation, body]);
    await assert.rejects(call('/denied'), error => error.status === 403);
    const stopped = await stopSimulation('synthetic-csrf');
    assert.equal(stopped.stopped, true);
    assert.deepEqual(seen.map(entry => entry.path), ['/tasks','/stalled-body','/denied','/stop','/status','/tasks','/humanoid/state']);
    assert.equal(new Set(seen.map(entry => entry.options.signal)).size, seen.length);
    assert.ok(seen.filter(entry => entry.path !== '/denied').every(entry => !entry.options.signal.aborted));
    assert.equal(seen[2].options.signal.aborted, true);
    for (const outcome of await pending) {
      assert.equal(outcome.status, 'rejected');
      assert.match(outcome.reason.message, /did not respond.*Refresh/);
    }
    assert.ok(seen.slice(0, 2).every(entry => entry.options.signal.aborted));
    assert.ok(seen.slice(3).every(entry => !entry.options.signal.aborted));
    finishBody({status:'late-success'});
    await assert.rejects(body, /did not respond.*Refresh/);
    assert.equal(seen.length, 7);
  } finally {
    if (pending) await pending;
    globalThis.fetch = originalFetch;
  }
});


test('Stop can complete before an unrelated operator mutation and verifies the actual latch', async () => {
  let finishMutation;
  const stuckMutation = new Promise(resolve => { finishMutation = resolve; });
  const seen = [];
  const api = (path, options = {}) => {
    seen.push({path, options});
    if (path === '/tasks' && options.method === 'POST') return stuckMutation;
    if (path === '/stop') return {status:'stopped', physical_actuation:false};
    if (path === '/status') return {mode:'simulation', stopped:true, active_task:null, physical_actuation:false};
    if (path === '/tasks') return {tasks:[{state:'cancelled'}]};
    if (path === '/humanoid/state') return {connection:'simulated_actuators', stopped:true, active_command:null, pending:null, physical_actuation:false};
    throw Error('Unexpected endpoint');
  };
  const unresolved = api('/tasks', {method:'POST', csrf:'synthetic-csrf'});
  const state = await stopSimulation('synthetic-csrf', api);
  assert.equal(state.stopped, true);
  assert.deepEqual(seen.map(entry => entry.path), ['/tasks','/stop','/status','/tasks','/humanoid/state']);
  assert.deepEqual(seen[1].options, {method:'POST', csrf:'synthetic-csrf'});
  finishMutation({id:'synthetic-task'});
  await unresolved;
});

test('Stop fails closed if the backend does not acknowledge the safety state', async () => {
  const safe = {status:'stopped', physical_actuation:false};
  const idle = {mode:'simulation', stopped:true, active_task:null, physical_actuation:false};
  const terminalHistory = {tasks:[{state:'cancelled'},{state:'completed'}]};
  const whole = {connection:'simulated_actuators', stopped:true, active_command:null, pending:null, physical_actuation:false};
  for (const [label, ack, status, history, humanoid, count] of [
    ['missing acknowledgement', {}, undefined, undefined, undefined, 1],
    ['falsified physical flag', {status:'stopped', physical_actuation:true}, undefined, undefined, undefined, 1],
    ['still active', safe, {stopped:true, active_task:'running', physical_actuation:false}, undefined, undefined, 2],
    ['latch not set', safe, {stopped:false, active_task:null, physical_actuation:false}, undefined, undefined, 2],
    ['unexpected operating mode', safe, {...idle, mode:'live'}, undefined, undefined, 2],
    ['missing operating mode', safe, {stopped:true, active_task:null, physical_actuation:false}, undefined, undefined, 2],
    ['physical flag uncertain', safe, {stopped:true, active_task:null}, undefined, undefined, 2],
    ['status readback unavailable', safe, new Error('offline'), undefined, undefined, 2],
    ['history still contains a running task', safe, idle, {tasks:[{state:'running'}]}, undefined, 3],
    ['history still contains a proposal', safe, idle, {tasks:[{state:'proposed'}]}, undefined, 3],
    ['history missing', safe, idle, {}, undefined, 3],
    ['history unavailable', safe, idle, new Error('journal unavailable'), undefined, 3],
    ['whole-body active', safe, idle, terminalHistory, {...whole,active_command:'in-flight'}, 4],
    ['whole-body pending', safe, idle, terminalHistory, {...whole,pending:{id:'proposed'}}, 4],
    ['whole-body fault', safe, idle, terminalHistory, new Error('fault'), 4],
    ['faulted humanoid readback', safe, idle, terminalHistory, {...whole, connection:'simulator_fault'}, 4],
    ['missing humanoid connection', safe, idle, terminalHistory, {...whole, connection:undefined}, 4],
    ['whole-body physical flag uncertain', safe, idle, terminalHistory, {...whole,physical_actuation:undefined}, 4],
  ]) {
    const seen = [];
    const api = async path => {
      seen.push(path);
      if (path === '/stop') return ack;
      if (path === '/status') {
        if (status instanceof Error) throw status;
        return status;
      }
      if (path === '/tasks') {
        if (history instanceof Error) throw history;
        return history;
      }
      if (path === '/humanoid/state') {
        if (humanoid instanceof Error) throw humanoid;
        return humanoid;
      }
      throw Error('Unexpected endpoint');
    };
    await assert.rejects(stopSimulation('synthetic-csrf', api), undefined, label);
    assert.equal(seen.length, count, label);
    assert.equal(seen[0], '/stop');
  }
  await assert.rejects(stopSimulation('', () => {throw Error('Should not reach API');}), /Sign in/);
});

test('reset verifies acknowledgement, legacy history and independent humanoid state', async () => {
  const seen = [];
  const api = async (path, options = {}) => {
    seen.push({path, options});
    if (path === '/reset') return {status:'reset', physical_actuation:false};
    if (path === '/status') return {mode:'simulation', stopped:false, active_task:null, physical_actuation:false};
    if (path === '/tasks') return {tasks:[{state:'completed'},{state:'cancelled'}]};
    if (path === '/humanoid/state') return {
      connection:'simulated_actuators', stopped:false, active_command:null,
      pending:null, physical_actuation:false
    };
    throw Error('Unexpected endpoint');
  };
  const status = await resetSimulation('synthetic-csrf', api);
  assert.equal(status.stopped, false);
  assert.deepEqual(seen.map(item=>item.path), ['/reset','/status','/tasks','/humanoid/state']);
  assert.deepEqual(seen[0].options, {
    method:'POST', csrf:'synthetic-csrf', body:{confirmed:true}
  });
});

test('reset fails closed on an unverified acknowledgement or any active/faulted view', async () => {
  const ack = {status:'reset', physical_actuation:false};
  const status = {mode:'simulation', stopped:false, active_task:null, physical_actuation:false};
  const history = {tasks:[{state:'completed'},{state:'cancelled'}]};
  const humanoid = {
    connection:'simulated_actuators', stopped:false, active_command:null,
    pending:null, physical_actuation:false
  };
  const cases = [
    ['missing acknowledgement', {}, status, history, humanoid, 1],
    ['physical actuation uncertain', {status:'reset'}, status, history, humanoid, 1],
    ['wrong mode', ack, {...status,mode:'live'}, history, humanoid, 2],
    ['stop latch remains set', ack, {...status,stopped:true}, history, humanoid, 2],
    ['legacy task still active', ack, {...status,active_task:'running'}, history, humanoid, 2],
    ['physical flag enabled', ack, {...status,physical_actuation:true}, history, humanoid, 2],
    ['history still proposed', ack, status, {tasks:[{state:'proposed'}]}, humanoid, 3],
    ['history still running', ack, status, {tasks:[{state:'running'}]}, humanoid, 3],
    ['humanoid command active', ack, status, history, {...humanoid,active_command:'running'}, 4],
    ['humanoid proposal pending', ack, status, history, {...humanoid,pending:{id:'pending'}}, 4],
    ['humanoid faulted', ack, status, history, {...humanoid,connection:'simulator_fault'}, 4],
    ['humanoid stop latch remains set', ack, status, history, {...humanoid,stopped:true}, 4],
    ['humanoid physical state uncertain', ack, status, history, {...humanoid,physical_actuation:undefined}, 4],
  ];
  for (const [label, reply, current, tasks, whole, count] of cases) {
    const seen = [];
    const api = async path => {
      seen.push(path);
      if (path === '/reset') return reply;
      if (path === '/status') return current;
      if (path === '/tasks') return tasks;
      if (path === '/humanoid/state') return whole;
      throw Error('Unexpected endpoint');
    };
    await assert.rejects(resetSimulation('synthetic-csrf', api), undefined, label);
    assert.equal(seen.length, count, label);
  }
  let called = false;
  await assert.rejects(
    resetSimulation('', async () => { called = true; }),
    /Sign in before resetting/
  );
  assert.equal(called, false);
});

test('native MFA step-up refuses to start without the current session CSRF proof', async () => {
  let called = false;
  await assert.rejects(
    signIn(async () => ({type:'cancel'}), async () => { called = true; }, {stepUp:true}),
    /Refresh your operator session/
  );
  assert.equal(called, false);
});
