import {test} from 'node:test';
import assert from 'node:assert/strict';
import {signIn, CALLBACK, AUTH, stopSimulation} from './session.mjs';
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
  }, api, {stepUp:true});
  assert.deepEqual(result, {signed_in:true});
  assert.deepEqual(seen[0].options.body, {mobile:true, step_up:true});
  assert.equal(seen[1].path, AUTH + '/mobile/exchange');
});


test('Stop can complete before an unrelated operator mutation and verifies the actual latch', async () => {
  let finishMutation;
  const stuckMutation = new Promise(resolve => { finishMutation = resolve; });
  const seen = [];
  const api = (path, options = {}) => {
    seen.push({path, options});
    if (path === '/tasks' && options.method === 'POST') return stuckMutation;
    if (path === '/stop') return {status:'stopped', physical_actuation:false};
    if (path === '/status') return {stopped:true, active_task:null, physical_actuation:false};
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
  const idle = {stopped:true, active_task:null, physical_actuation:false};
  const terminalHistory = {tasks:[{state:'cancelled'},{state:'completed'}]};
  const whole = {connection:'simulated_actuators', stopped:true, active_command:null, pending:null, physical_actuation:false};
  for (const [label, ack, status, history, humanoid, count] of [
    ['missing acknowledgement', {}, undefined, undefined, undefined, 1],
    ['falsified physical flag', {status:'stopped', physical_actuation:true}, undefined, undefined, undefined, 1],
    ['still active', safe, {stopped:true, active_task:'running', physical_actuation:false}, undefined, undefined, 2],
    ['latch not set', safe, {stopped:false, active_task:null, physical_actuation:false}, undefined, undefined, 2],
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
