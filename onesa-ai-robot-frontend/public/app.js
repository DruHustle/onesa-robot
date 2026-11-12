import {createInspection} from './inspection.js';
import {RobotClient} from './client.js';
const client = new RobotClient();
const byId = id => document.getElementById(id);
let fastPoll=false,lastIdlePoll=0;
const localSimulator = ['127.0.0.1','localhost','[::1]'].includes(window.location?.hostname);
let identityEnabled=false,identitySession=false,identityOrigin='',identityConfirmationExpires=0,canOperate=true;
let csrf = '', proposalId = '', timer, polling = null, generation=0, observationRevision=0, suspended=false, observationReady=false;
const busyForms=new WeakSet();
const observationForms=[['reset-form','reset-submit'],['proposal-form','proposal-submit'],['approval-form','approve-submit'],['mission-form','mission-submit'],['mission-approval','mission-approve-submit']];
function setObservationReady(ready){observationReady=ready;for(const [formId,buttonId]of observationForms)byId(buttonId).disabled=!ready||!canOperate||(identitySession&&identityConfirmationExpires<=Date.now()/1000)||busyForms.has(byId(formId));}
let administrator=false,permissionRevision=null,permissionRead=null,lastPermissionRead=0;
function operatorNextStep(status,proposal){if(!status||typeof status.stopped!=='boolean'||!(status.active_task===null||typeof status.active_task==='string'))return 'Waiting for a current simulator observation. Stop remains available.';if(status.stopped)return identitySession?'Stopped. Confirm your identity and review the reset; no previous task resumes.':'Stopped. Confirm your password to reset; no previous task resumes.';if(status.active_task)return 'Simulation running. Monitor the result or use Stop simulation.';if(proposal)return 'Review the proposed target and duration, then confirm within 15 seconds or cancel.';return 'Ready. Set a target and duration, then create a proposal.';}
const notice = text => { byId('notice').textContent = text; };
const inspector=createInspection(client,{run,notice});

function sessionPresentation(authenticated){
 document.body?.setAttribute('data-session',authenticated?'authenticated':'signed-out');
 const heading=byId('console-title'),description=byId('console-description');
 if(heading)heading.textContent=authenticated?'Industrial inspection':'Operator Console';
 if(description)description.textContent=authenticated?'Plan, supervise and review a synthetic pump inspection. No physical device is connected.':'Use your approved operator account to access development simulation.';
}
function signedOut({preserveInitialSignIn=false}={}) {setIdentitySession({});inspector.reset();fastPoll=false;lastIdlePoll=0;sessionPresentation(false);setObservationReady(false);generation++; clearInterval(timer);csrf='';client.csrf='';proposalId='';setAdministrator({});byId('operator').hidden=true;byId('login-form').hidden=identityEnabled||!localSimulator;if(byId('onesa-sign-in'))byId('onesa-sign-in').hidden=!(identityEnabled||!localSimulator);byId('approval-form').hidden=true;byId('history').replaceChildren();for(const field of document.querySelectorAll('input[type="password"]'))if(!preserveInitialSignIn||field.id!=='password')field.value=''; }
function setIdentitySession(session){
 identitySession=session.authentication_method==='onesa';canOperate=session.role!=='viewer';
 identityConfirmationExpires=Number(session.confirmation_expires_at)||0;
 client.authenticationMethod=identitySession?'onesa':'operator';client.canOperate=canOperate;client.confirmationExpires=identityConfirmationExpires;
 if(byId('onesa-session'))byId('onesa-session').hidden=!identitySession;
 if(byId('onesa-session-role'))byId('onesa-session-role').textContent=identitySession?`${session.name} · ${session.global_administrator?'Onesa administrator · all applications':session.role==='administrator'?'Robot administrator':session.role==='viewer'?'Viewer · read only':'Robot operator'}`:'';
 if(byId('logout'))byId('logout').textContent=identitySession?'Sign out of Onesa':'Sign out';
 for(const id of ['reset-password','mission-password','humanoid-password','approve-password','physical-permission-password']){
  const input=byId(id),label=document.querySelector?.(`label[for="${id}"]`),check=byId(id+'-confirmed'),wrapper=byId(id+'-identity-label');
  if(input){input.hidden=identitySession;input.required=!identitySession;input.value='';}
  if(label)label.hidden=identitySession;
  if(wrapper)wrapper.hidden=!identitySession;
  if(check){check.required=identitySession;check.checked=false;}
 }
 updateIdentityConfirmation();
}
function updateIdentityConfirmation(){
 if(!identitySession)return;
 const fresh=identityConfirmationExpires>Date.now()/1000;
 const status=byId('onesa-confirmation-status');
 if(status)status.textContent=!canOperate?'Your account can view Robot. Ask an administrator for operator access.':fresh?'Identity confirmed with MFA. You can review and confirm operations for five minutes.':'Confirm your Onesa identity with MFA before preparing an operation. Stop and sign-out stay available.';
 if(byId('onesa-confirm-identity'))byId('onesa-confirm-identity').hidden=!canOperate;
}
function readConfirmation(id){
 const input=byId(id),check=byId(id+'-confirmed');
 const value=identitySession?(check?.checked?{confirmed:true}:{}):{password:input.value};
 input.value='';if(check)check.checked=false;return value;
}
async function startIdentity(stepUp=false){
 const button=byId(stepUp?'onesa-confirm-identity':'onesa-sign-in-button');
 if(button?.disabled)return;
 if(button)button.disabled=true;
 try{
  const result=await client.beginIdentity(stepUp),target=new URL(result.authorization_url);
  if(target.origin!==identityOrigin||target.pathname!=='/api/v1/identity/auth')throw new Error('Onesa sign-in could not be verified.');
  window.location.assign(target.href);
 }catch(error){notice(error.message);if(button)button.disabled=false;}
}
async function configureIdentity(){
 const localForm=byId('login-form'),central=byId('onesa-sign-in'),button=byId('onesa-sign-in-button');
 localForm.hidden=!localSimulator;
 if(!localSimulator){central.hidden=false;button.disabled=true;}
 if(typeof client.identityConfig!=='function')return;
 try{
  const value=await client.identityConfig();identityEnabled=value.enabled===true;
  if(!identityEnabled&&!localSimulator)notice('Shared sign-in is not connected. Reload to retry or open Company Portal.');
  if(identityEnabled){
   const account=new URL(value.account_url);identityOrigin=account.origin;button.disabled=false;
   const link=byId('onesa-account-link');if(link){link.href=account.href;link.hidden=false;}
   byId('login-form').hidden=true;byId('onesa-sign-in').hidden=Boolean(csrf);
   if(!csrf)byId('console-description').textContent='Sign in once with Onesa. Your approved Robot access is ready here.';
  }
  if(window.location?.search){
   const outcome=new URLSearchParams(window.location.search).get('identity');
   const messages={access:'Robot access is not enabled for this account. Manage your product access from your Onesa account.',unavailable:'Onesa sign-in is temporarily unavailable. Please try again.',failed:'Sign-in could not be completed. Please start again.'};
   if(messages[outcome]){notice(messages[outcome]);window.history.replaceState(null,'','/console');}
  }
 }catch{notice('Sign-in settings are temporarily unavailable. Refresh this page to try again.');}
}
byId('onesa-sign-in-button')?.addEventListener('click',()=>void startIdentity());
byId('onesa-confirm-identity')?.addEventListener('click',()=>void startIdentity(true));
void configureIdentity();
function updatePermissionControls() {
 const form=byId('physical-permission-form');
 byId('physical-permission-allow').disabled=!administrator || permissionRevision===null ||
  busyForms.has(form) || (identitySession && identityConfirmationExpires<=Date.now()/1000);
}
function clearPermissionReview() {
 byId('physical-permission-password').value='';
 byId('physical-permission-password-confirmed').checked=false;
}
function clearPhysicalPermission() {
 permissionRead=null;
 permissionRevision=null;
 clearPermissionReview();
 for(const id of ['physical-permission-state','physical-controller-state','physical-operation-state','physical-permission-expiry']){
  byId(id).textContent='Unavailable';
 }
 updatePermissionControls();
}
function renderPhysicalPermission(value) {
 if(!['allowed','blocked'].includes(value?.permission) || !Number.isSafeInteger(value.revision) || value.revision<0 ||
    !Number.isFinite(value.sampled_at) || (value.permission==='allowed'
      ? !Number.isFinite(value.expires_at) || value.expires_at<=value.sampled_at
      : value.expires_at!==null) ||
    value.controller_connection!=='not_connected' || value.controller_readiness!=='not_ready' ||
    value.physical_operation!=='inactive' || value.physical_actuation!==false){
  throw new Error('Physical-operation state could not be verified.');
 }
 if(permissionRevision!==null && value.revision<permissionRevision){
  throw new Error('Physical-permission revision moved backwards.');
 }
 if(permissionRevision!==value.revision)clearPermissionReview();
 permissionRevision=value.revision;
 byId('physical-permission-state').textContent=value.permission==='allowed'?'Allowed for the issuing administrator session':'Blocked';
 byId('physical-controller-state').textContent='Not connected · hardware integration under development';
 byId('physical-operation-state').textContent='Inactive · controller not connected';
 byId('physical-permission-expiry').textContent=value.expires_at?new Date(value.expires_at*1000).toLocaleString():'No active permission';
 updatePermissionControls();
}
async function readPhysicalPermission() {
 if(!administrator || suspended || permissionRead)return;
 const owner={};
 permissionRead=owner;
 lastPermissionRead=performance.now();
 try{
  const value=await client.physicalPermission();
  if(permissionRead===owner)renderPhysicalPermission(value);
 }catch(error){
  if(permissionRead!==owner)return;
  clearPhysicalPermission();
  if(error.status===401)signedOut();
 }finally{
  if(permissionRead===owner)permissionRead=null;
 }
}
function setAdministrator(session) {
 administrator=session.role==='administrator';
 clearPhysicalPermission();
 lastPermissionRead=0;
 byId('administrator').hidden=!administrator;
 if(administrator)void readPhysicalPermission();
}
async function changePhysicalPermission(change,current) {
 // A mutation can reach the server even if its response is lost. Re-read the
 // current permission after it settles, including when another session blocked it.
 clearPhysicalPermission();
 try{
  await change();
 }finally{
  if(current===generation && administrator && !suspended){
   permissionRead=null;
   await readPhysicalPermission();
  }
 }
}
function verifiedObservation(status,tasks){
 return status?.mode==='simulation'&&status.physical_actuation===false&&typeof status.stopped==='boolean'&&
  Number.isFinite(status.position_radians)&&Number.isFinite(status.sampled_at)&&
  (status.active_task===null||typeof status.active_task==='string')&&typeof status.capabilities?.edge_telemetry==='string'&&
  Array.isArray(tasks)&&tasks.every(task=>typeof task?.id==='string'&&['proposed','running','completed','cancelled'].includes(task.state)&&typeof task.reason==='string'&&Number.isFinite(task.created)&&Number.isFinite(task.position));
}
async function refresh(force=true) {
  if(document.hidden||!force&&!fastPoll&&performance.now()-lastIdlePoll<3000)return;
  if(suspended || !csrf) return;
  updateIdentityConfirmation();
  if(administrator){
    updatePermissionControls();
    if(performance.now()-lastPermissionRead>5000)void readPhysicalPermission();
  }
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
    byId('stop-state').textContent=status.stopped?identitySession?'Stopped — identity-confirmed reset required':'Stopped — password-confirmed reset required':'Ready for a simulation proposal';
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
      if (response.status === 503 && response.headers.get('content-type')?.includes('application/json')) {
        const problem = await response.json().catch(() => null);
        if (problem?.detail === 'Remote simulation journal requires recovery') {
          throw new Error('The simulation journal needs recovery before you can sign in. Contact Onesa support. Your sign-in details have not been sent.');
        }
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
  finally{if(form){busyForms.delete(form);form.removeAttribute('aria-busy');controls.forEach(control=>control.disabled=observationForms.some(([id])=>id===form.id)&&!observationReady);if(form.id==='physical-permission-form')updatePermissionControls();}}
}
byId('login-form').addEventListener('submit',event=>{
  event.preventDefault();
  if(!localSimulator||identityEnabled||suspended||busyForms.has(event.currentTarget))return;
  // Capture this deliberate submission before readiness or restoration settles.
  // Its generation supersedes the anonymous bootstrap read, not another account.
  const credentials={name:byId('name').value,password:byId('password').value},current=++generation;
  run(async()=>{
    await waitForSimulator();
    if(suspended||current!==generation)return;
    const session=await client.login(credentials);
    if(suspended||current!==generation)return;
    byId('password').value='';
    csrf=session.csrf;client.csrf=csrf;setIdentitySession(session);setAdministrator(session);byId('login-form').hidden=true;byId('operator').hidden=false;if(byId('onesa-sign-in'))byId('onesa-sign-in').hidden=true;sessionPresentation(true);inspector.authenticate();timer=setInterval(()=>refresh(false),500);
  },'Signed in.',event.currentTarget,current);
});
byId('physical-permission-form').addEventListener('submit',event=>{
 event.preventDefault();
 if(byId('physical-permission-allow').disabled)return;
 const current=generation,revision=permissionRevision,confirmation=readConfirmation('physical-permission-password');
 run(()=>changePhysicalPermission(()=>client.allowPhysical({...confirmation,expected_revision:revision}),current),
  'Permission request completed.',event.currentTarget,current);
});
byId('block-physical-permission').addEventListener('click',()=>{
 const current=generation;
 run(()=>changePhysicalPermission(()=>client.blockPhysical(),current),'Block request completed.',null,current);
});
byId('reset-form').addEventListener('submit',event=>{event.preventDefault();const confirmation=readConfirmation('reset-password');run(()=>client.reset(confirmation),'Simulation stop reset.',event.currentTarget);});
byId('proposal-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{const target=Number(byId('target').value),duration=Number(byId('duration').value);const data=await client.propose({command_id:crypto.randomUUID(),issued_at:Date.now()/1000,target_radians:target,duration_seconds:duration});proposalId=data.id;byId('proposal-summary').textContent=`Target ${target} radians over ${duration} seconds. Review this synthetic movement before confirming. Approval expires after 15 seconds.`;byId('proposed-id').textContent=proposalId;byId('approval-form').hidden=false;byId(identitySession?'approve-password-confirmed':'approve-password').focus();},'Proposal created. Confirm within 15 seconds.',event.currentTarget);});
byId('approval-form').addEventListener('submit',event=>{event.preventDefault();const confirmation=readConfirmation('approve-password');run(async()=>{await client.approve(proposalId,confirmation);byId('approval-form').hidden=true;proposalId='';},'Simulation running; heartbeat must remain connected.',event.currentTarget);});
byId('cancel').addEventListener('click',()=>run(async()=>{await client.cancel(proposalId);byId('approval-form').hidden=true;proposalId='';},'Proposal cancelled.'));
byId('stop').addEventListener('click',()=>run(()=>client.stop(),'Simulation stopped. Reset requires confirmation.'));
byId('logout').addEventListener('click',()=>run(async()=>{const result=await client.logout();signedOut();if(result.identity_signed_out===false)throw new Error('Signed out of Robot. Open your Onesa account to finish signing out of other applications.');},identitySession?'Signed out of Onesa. Other applications will require sign-in.':'Signed out; this session cannot authorize further tasks.'));
// No localStorage/sessionStorage, token URLs or persistence of operator credentials.

function restoreSession({initial=false}={}){const current=++generation;return client.me().then(session=>{if(current!==generation)return;csrf=session.csrf;client.csrf=csrf;setIdentitySession(session);setAdministrator(session);byId('login-form').hidden=true;byId('operator').hidden=false;if(byId('onesa-sign-in'))byId('onesa-sign-in').hidden=true;sessionPresentation(true);inspector.authenticate();timer=setInterval(()=>refresh(false),500);return refresh();}).catch(error=>{if(current!==generation)return;if(error.status===401)signedOut({preserveInitialSignIn:initial});else if(error.name!=='AbortError')notice(error.message);});}
function suspendRequests(){clearPhysicalPermission();inspector.stale();setObservationReady(false);suspended=true;generation++;clearInterval(timer);client.cancelPending();polling=null;}
window.addEventListener('beforeunload',suspendRequests);
window.addEventListener('pagehide',suspendRequests);
window.addEventListener('pageshow',event=>{if(event.persisted){suspended=false;restoreSession();}});
setObservationReady(false);
byId('login-submit').disabled=false;
const entryFallback=byId('account-entry-fallback');
if(entryFallback)entryFallback.hidden=true;
// Keep deliberate masked entries while the initial anonymous lookup settles.
// Logout, explicit rejection and later session expiry still clear all passwords.
restoreSession({initial:true});

document.addEventListener('visibilitychange',()=>{if(document.hidden)suspendRequests();else if(suspended){suspended=false;restoreSession();}});
