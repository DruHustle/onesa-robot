import {createInspection} from './inspection.js';
import {RobotClient} from './client.js';
const client = new RobotClient();
const byId = id => document.getElementById(id);
let fastPoll=false,lastIdlePoll=0;
let csrf = '', proposalId = '', timer, polling = null, generation=0, observationRevision=0, suspended=false, observationReady=false;
const busyForms=new WeakSet();
const observationForms=[['reset-form','reset-submit'],['proposal-form','proposal-submit'],['approval-form','approve-submit'],['mission-form','mission-submit'],['mission-approval','mission-approve-submit']];
function setObservationReady(ready){observationReady=ready;for(const [formId,buttonId]of observationForms)byId(buttonId).disabled=!ready||busyForms.has(byId(formId));}
let administrator=false,permissionRevision=null,permissionRead=null,lastPermissionRead=0;
function operatorNextStep(status,proposal){if(!status||typeof status.stopped!=='boolean'||!(status.active_task===null||typeof status.active_task==='string'))return 'Waiting for a current simulator observation. Stop remains available.';if(status.stopped)return 'Stopped. Confirm your password to reset; no previous task resumes.';if(status.active_task)return 'Simulation running. Monitor the result or use Stop simulation.';if(proposal)return 'Review the proposed target and duration, then confirm within 15 seconds or cancel.';return 'Ready. Set a target and duration, then create a proposal.';}
const notice = text => { byId('notice').textContent = text; };
const inspector=createInspection(client,{run,notice});

function sessionPresentation(authenticated){
 document.body?.setAttribute('data-session',authenticated?'authenticated':'signed-out');
 const heading=byId('console-title'),description=byId('console-description');
 if(heading)heading.textContent=authenticated?'Industrial inspection':'Operator Console';
 if(description)description.textContent=authenticated?'Plan, supervise and review a synthetic pump inspection. No physical device is connected.':'Use your approved operator account to access development simulation.';
}
function signedOut({preserveInitialSignIn=false}={}) {inspector.reset();fastPoll=false;lastIdlePoll=0;sessionPresentation(false);setObservationReady(false);generation++; clearInterval(timer);csrf='';client.csrf='';proposalId='';administrator=false;permissionRevision=null;permissionRead=null;lastPermissionRead=0;byId('administrator').hidden=true;byId('physical-permission-state').textContent='Unavailable';byId('physical-operation-state').textContent='Unavailable';byId('operator').hidden=true;byId('login-form').hidden=false;byId('approval-form').hidden=true;byId('history').replaceChildren();for(const field of document.querySelectorAll('input[type="password"]'))if(!preserveInitialSignIn||field.id!=='password')field.value=''; }
function renderPhysicalPermission(value){
 if(!['allowed','blocked'].includes(value?.permission)||!Number.isSafeInteger(value.revision)||value.revision<0||value.controller_connection!=='not_connected'||value.controller_readiness!=='not_ready'||value.physical_operation!=='inactive'||value.physical_actuation!==false)throw new Error('Physical-operation state could not be verified.');
 if(permissionRevision!==null&&value.revision<permissionRevision)return;
 permissionRevision=value.revision;
 byId('physical-permission-state').textContent=value.permission==='allowed'?'Allowed for this administrator session':'Blocked by administrator';
 byId('physical-controller-state').textContent='Not connected · hardware integration under development';
 byId('physical-operation-state').textContent='Inactive · controller not connected';
 byId('physical-permission-expiry').textContent=value.expires_at?new Date(value.expires_at*1000).toLocaleString():'No active permission';
}
async function readPhysicalPermission(){
 if(!administrator||suspended||permissionRead)return;
 const owner={},current=generation;permissionRead=owner;lastPermissionRead=performance.now();
 try{const value=await client.physicalPermission();if(current===generation&&administrator&&!suspended)renderPhysicalPermission(value);}
 catch(error){if(current!==generation||!administrator||suspended)return;permissionRevision=null;byId('physical-permission-state').textContent='Unavailable · last observation is stale';byId('physical-operation-state').textContent='Unknown · current state unavailable';if(error.status===401)signedOut();}
 finally{if(permissionRead===owner)permissionRead=null;}
}
function setAdministrator(session){administrator=session.role==='administrator';permissionRevision=null;permissionRead=null;lastPermissionRead=0;byId('administrator').hidden=!administrator;if(administrator)void readPhysicalPermission();}
function verifiedObservation(status,tasks){
 return status?.mode==='simulation'&&status.physical_actuation===false&&typeof status.stopped==='boolean'&&
  Number.isFinite(status.position_radians)&&Number.isFinite(status.sampled_at)&&
  (status.active_task===null||typeof status.active_task==='string')&&typeof status.capabilities?.edge_telemetry==='string'&&
  Array.isArray(tasks)&&tasks.every(task=>typeof task?.id==='string'&&['proposed','running','completed','cancelled'].includes(task.state)&&typeof task.reason==='string'&&Number.isFinite(task.created)&&Number.isFinite(task.position));
}
async function refresh(force=true) {
  if(document.hidden||!force&&!fastPoll&&performance.now()-lastIdlePoll<3000)return;
  if(suspended || !csrf) return;
  if(polling){
    if(!force)return;
    const previous=polling;await previous.settled;
    if(suspended || document.hidden || !csrf)return;
    if(previous.generation===generation && previous.revision===observationRevision)return;
    return refresh(true);
  }
  lastIdlePoll=performance.now();
  const owner={generation,revision:observationRevision};
  owner.settled=new Promise(resolve=>{owner.finish=resolve;});
  polling=owner;const current=generation,revision=observationRevision,observations=[];
  try {
    observations.push(client.status());observations.push(client.tasks());
    const [status,{tasks}]=await Promise.all(observations);
    if(suspended || current!==generation || revision!==observationRevision) return;
    if(!verifiedObservation(status,tasks))throw new Error('Simulator observation could not be verified.');
    // A reachable heartbeat endpoint is not operator supervision. Renew only
    // after both current reads succeed; failed or obsolete telemetry must let
    // the independent backend watchdog stop an unobserved simulation.
    await client.heartbeat();
    if(suspended || current!==generation || revision!==observationRevision) return;
    if(proposalId){const proposed=tasks.find(task=>task.id===proposalId);if(proposed&&proposed.state!=='proposed'){proposalId='';byId('approval-form').hidden=true;byId('approve-password').value='';}}
    fastPoll=Boolean(status.active_task)||Boolean(proposalId)||inspector.hasPending();
    byId('next-step').textContent=status.stopped?operatorNextStep(status,proposalId):status.active_task?'Simulation running. Monitor the inspection below or use Stop simulation.':proposalId?operatorNextStep(status,proposalId):inspector.guidance?.(status)||'Ready. Choose an inspection scenario, review its plan, then approve.';
    byId('connection').textContent='Connected to software simulator';
    byId('reset-form').hidden=!status.stopped;
    byId('stop-state').textContent=status.stopped?'Stopped — password-confirmed reset required':'Ready for a simulation proposal';
    byId('position').textContent=Number(status.position_radians).toFixed(4);
    byId('active').textContent=status.active_task || 'None';
    byId('sampled-at').textContent=Number.isFinite(status.sampled_at)?new Date(status.sampled_at*1000).toLocaleString(undefined,{timeZoneName:'short'}):'Unavailable · simulator API update required';
    byId('edge-state').textContent=status.capabilities.edge_telemetry==='simulation_connected'?'Synthetic edge telemetry received':'Disconnected · no fresh synthetic edge telemetry';
    byId('environment').textContent=status.deployment_environment==='local'?'DEVELOPMENT':['staging','production'].includes(status.deployment_environment)?status.deployment_environment.toUpperCase():'ENVIRONMENT UNAVAILABLE';
    byId('environment').hidden=false;
    byId('history').replaceChildren(...tasks.map(task=>{const li=document.createElement('li');li.textContent=`${task.state} · ${task.id} · ${task.reason} · ${new Date(task.created*1000).toLocaleString()} · ${Number(task.position).toFixed(4)} rad`;if(task.state==='running'){const button=document.createElement('button');button.textContent='Cancel running task';button.addEventListener('click',()=>run(()=>client.cancel(task.id),'Task cancelled; simulation stopped.'));li.append(button);}return li;}));
    if(!tasks.length){const li=document.createElement('li');li.textContent='No simulation tasks yet. Reset the stop state to create a proposal.';byId('history').append(li);}
    setObservationReady(true);
    void inspector.refresh(status);
    if(administrator&&performance.now()-lastPermissionRead>5000)void readPhysicalPermission();
  } catch(error) {if(current!==generation) return;if(error.status===401){signedOut();notice(error.message);return;}if(revision!==observationRevision)return;setObservationReady(false);byId('reset-form').hidden=false;inspector.stale();byId('stop-state').textContent='Unavailable — current observation could not be verified';byId('connection').textContent='Disconnected — telemetry is stale; the gateway watchdog stops running simulation tasks.';byId('position').textContent='Stale · latest response unavailable';byId('next-step').textContent=operatorNextStep(null,'');notice(error.message);}
  finally {await Promise.allSettled(observations);if(polling===owner)polling=null;owner.finish();}
}
const unavailableSignIn='Simulator is unavailable. Your sign-in entries are retained. Wait a moment, then select Sign in again.';
async function waitForSimulator() {
  const lifecycle=client.pending.signal,deadline=performance.now()+20000;
  notice('Checking simulator readiness… Your sign-in entries stay on this page.');
  while(!suspended && !lifecycle.aborted && performance.now()<deadline) {
    try {
      // A protected same-origin preview needs its access cookie before login.
      const response=await fetch('/api/v1/ready',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.any([lifecycle,AbortSignal.timeout(Math.ceil(Math.max(1,Math.min(4000,deadline-performance.now()))))])});
      if(response.ok) {
        let state;try{state=await response.json();}catch{throw new Error('Simulator readiness could not be verified. Try signing in after service recovery.');}
        if(state?.status==='ready'&&state.mode==='simulation'&&state.physical_actuation===false)return;
        throw new Error('Simulator readiness could not be verified. Try signing in after service recovery.');
      }
      if(![502,503,504].includes(response.status))throw new Error('Simulator readiness request was rejected. Try signing in after service recovery.');
    } catch(error) {
      if(lifecycle.aborted || suspended)throw error;
      if(!['AbortError','TimeoutError','TypeError'].includes(error.name))throw error;
    }
    await new Promise(resolve=>setTimeout(resolve,Math.min(500,Math.max(0,deadline-performance.now()))));
  }
  const error=new Error(unavailableSignIn);Object.assign(error,{status:503});throw error;
}
async function run(action, success, form=null,expectedGeneration=null) {
  if(suspended)return;
  if(form&&busyForms.has(form))return;
  if(form&&observationForms.some(([id])=>id===form.id)&&!observationReady){notice('Wait for a current simulator observation before changing simulation state.');return;}
  const controls=form?[...form.querySelectorAll('button[type="submit"]')]:[];
  if(form){busyForms.add(form);form.setAttribute('aria-busy','true');controls.forEach(control=>control.disabled=true);}
  try {await action();if(suspended||expectedGeneration!==null&&expectedGeneration!==generation)return;observationRevision++;setObservationReady(false);inspector.stale();notice(success);await refresh();}
  catch(error){if(suspended||expectedGeneration!==null&&expectedGeneration!==generation)return;if(error.status===401) signedOut();notice(form?.id==='login-form'&&([502,503,504].includes(error.status)||['AbortError','TimeoutError','TypeError'].includes(error.name))?unavailableSignIn:error.message);}
  finally{if(form){busyForms.delete(form);form.removeAttribute('aria-busy');controls.forEach(control=>control.disabled=observationForms.some(([id])=>id===form.id)&&!observationReady);}}
}
byId('login-form').addEventListener('submit',event=>{
  event.preventDefault();
  if(suspended||busyForms.has(event.currentTarget))return;
  // Capture this deliberate submission before readiness or restoration settles.
  // Its generation supersedes the anonymous bootstrap read, not another account.
  const credentials={name:byId('name').value,password:byId('password').value},current=++generation;
  run(async()=>{
    await waitForSimulator();
    if(suspended||current!==generation)return;
    const session=await client.login(credentials);
    if(suspended||current!==generation)return;
    byId('password').value='';
    csrf=session.csrf;client.csrf=csrf;setAdministrator(session);byId('login-form').hidden=true;byId('operator').hidden=false;sessionPresentation(true);inspector.authenticate();timer=setInterval(()=>refresh(false),500);
  },'Signed in.',event.currentTarget,current);
});
byId('physical-permission-form').addEventListener('submit',event=>{event.preventDefault();const password=byId('physical-permission-password').value,revision=permissionRevision,current=generation;byId('physical-permission-password').value='';run(async()=>{if(!administrator||revision===null)throw new Error('Refresh and review the current administrator permission before allowing operation.');const value=await client.allowPhysical({password,expected_revision:revision});if(current===generation&&administrator&&!suspended)renderPhysicalPermission(value);},'Permission request completed. The current confirmed state is shown in Robot administration; no movement started.',event.currentTarget,current);});
byId('block-physical-permission').addEventListener('click',()=>{const current=generation;run(async()=>{const value=await client.blockPhysical();if(current===generation&&administrator&&!suspended)renderPhysicalPermission(value);},'Administrator permission blocked.',null,current);});
byId('reset-form').addEventListener('submit',event=>{event.preventDefault();const password=byId('reset-password').value;byId('reset-password').value='';run(()=>client.reset({password}),'Simulation stop reset.',event.currentTarget);});
byId('proposal-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{const target=Number(byId('target').value),duration=Number(byId('duration').value);const data=await client.propose({command_id:crypto.randomUUID(),issued_at:Date.now()/1000,target_radians:target,duration_seconds:duration});proposalId=data.id;byId('proposal-summary').textContent=`Target ${target} radians over ${duration} seconds. Review this synthetic movement before confirming. Approval expires after 15 seconds.`;byId('proposed-id').textContent=proposalId;byId('approval-form').hidden=false;byId('approve-password').focus();},'Proposal created. Confirm within 15 seconds.',event.currentTarget);});
byId('approval-form').addEventListener('submit',event=>{event.preventDefault();const password=byId('approve-password').value;byId('approve-password').value='';run(async()=>{await client.approve(proposalId,{password});byId('approval-form').hidden=true;proposalId='';},'Simulation running; heartbeat must remain connected.',event.currentTarget);});
byId('cancel').addEventListener('click',()=>run(async()=>{await client.cancel(proposalId);byId('approval-form').hidden=true;proposalId='';},'Proposal cancelled.'));
byId('stop').addEventListener('click',()=>run(()=>client.stop(),'Simulation stopped. Reset requires confirmation.'));
byId('logout').addEventListener('click',()=>run(async()=>{await client.logout();signedOut();},'Signed out; this session cannot authorize further tasks.'));
// No localStorage/sessionStorage, token URLs or persistence of operator credentials.

function restoreSession({initial=false}={}){const current=++generation;return client.me().then(session=>{if(current!==generation)return;csrf=session.csrf;client.csrf=csrf;setAdministrator(session);byId('login-form').hidden=true;byId('operator').hidden=false;sessionPresentation(true);inspector.authenticate();timer=setInterval(()=>refresh(false),500);return refresh();}).catch(error=>{if(current!==generation)return;if(error.status===401)signedOut({preserveInitialSignIn:initial});else if(error.name!=='AbortError')notice(error.message);});}
function suspendRequests(){inspector.stale();setObservationReady(false);suspended=true;generation++;clearInterval(timer);client.cancelPending();polling=null;}
window.addEventListener('beforeunload',suspendRequests);
window.addEventListener('pagehide',suspendRequests);
window.addEventListener('pageshow',event=>{if(event.persisted){suspended=false;restoreSession();}});
setObservationReady(false);
byId('login-submit').disabled=false;
// Keep deliberate masked entries while the initial anonymous lookup settles.
// Logout, explicit rejection and later session expiry still clear all passwords.
restoreSession({initial:true});

document.addEventListener('visibilitychange',()=>{if(document.hidden)suspendRequests();else if(suspended){suspended=false;restoreSession();}});
