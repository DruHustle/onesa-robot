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

test('Stop can complete before an unrelated operator mutation and verifies the actual latch', async () => {
  let finishMutation;
  const stuckMutation = new Promise(resolve => { finishMutation = resolve; });
  const seen = [];
  const api = (path, options = {}) => {
    seen.push({path, options});
    if (path === '/tasks') return stuckMutation;
    if (path === '/stop') return {status:'stopped', physical_actuation:false};
    if (path === '/status') return {stopped:true, active_task:null, physical_actuation:false};
    throw Error('Unexpected endpoint');
  };
  const unresolved = api('/tasks', {method:'POST', csrf:'synthetic-csrf'});
  const state = await stopSimulation('synthetic-csrf', api);
  assert.equal(state.stopped, true);
  assert.deepEqual(seen.map(entry => entry.path), ['/tasks','/stop','/status']);
  assert.deepEqual(seen[1].options, {method:'POST', csrf:'synthetic-csrf'});
  finishMutation({id:'synthetic-task'});
  await unresolved;
});

test('Stop fails closed if the backend does not acknowledge the safety state', async () => {
  const safe = {status:'stopped', physical_actuation:false};
  for (const [label, ack, status, count] of [
    ['missing acknowledgement', {}, undefined, 1],
    ['falsified physical flag', {status:'stopped', physical_actuation:true}, undefined, 1],
    ['still active', safe, {stopped:true, active_task:'running', physical_actuation:false}, 2],
    ['latch not set', safe, {stopped:false, active_task:null, physical_actuation:false}, 2],
    ['physical flag uncertain', safe, {stopped:true, active_task:null}, 2],
    ['readback unavailable', safe, new Error('offline'), 2],
  ]) {
    const seen = [];
    const api = async path => {
      seen.push(path);
      if (path === '/stop') return ack;
      if (path === '/status') {
        if (status instanceof Error) throw status;
        return status;
      }
      throw Error('Unexpected endpoint');
    };
    await assert.rejects(stopSimulation('synthetic-csrf', api), undefined, label);
    assert.equal(seen.length, count, label);
    assert.equal(seen[0], '/stop');
  }
  await assert.rejects(stopSimulation('', () => {throw Error('Should not reach API');}), /Sign in/);
});
