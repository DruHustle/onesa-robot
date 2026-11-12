import {RobotClient} from './client.js';

const client = new RobotClient();
const $ = id => document.getElementById(id);
const panel = $('humanoid');
let epoch = 0, model = null, observation = null, busy = false, reading = false;
let supervising = false, haltRequired = false, stopping = null, reviewedPlan = null;
const label = value => value.replaceAll('_', ' ');
const ns = 'http://www.w3.org/2000/svg';
const identity = [1,0,0,0,1,0,0,0,1];
const rotate = (m, v) => [0,1,2].map(i => m[i*3]*v[0]+m[i*3+1]*v[1]+m[i*3+2]*v[2]);
const multiply = (a,b) => a.map((_,i) => [0,1,2].reduce((n,k) => n+a[Math.floor(i/3)*3+k]*b[k*3+i%3],0));
function rotation([x,y,z], angle) {
  const c=Math.cos(angle),s=Math.sin(angle),t=1-c;
  return [t*x*x+c,t*x*y-s*z,t*x*z+s*y,t*x*y+s*z,t*y*y+c,t*y*z-s*x,t*x*z-s*y,t*y*z+s*x,t*z*z+c];
}
function draw(positions) {
  const frames = new Map([['pelvis',{point:[0,0,0],matrix:identity}]]), nodes=[];
  const project = p => [200-p[1]*330+p[0]*130,205-p[2]*330];
  for (const joint of model.joints) {
    const parent=frames.get(joint.parent),offset=rotate(parent.matrix,joint.origin);
    const point=parent.point.map((v,i)=>v+offset[i]);
    frames.set(joint.name,{point,matrix:multiply(parent.matrix,rotation(joint.axis,positions[joint.name]))});
    const from=project(parent.point),to=project(point);
    const line=document.createElementNS(ns,'line');
    for(const [key,value] of Object.entries({x1:from[0],y1:from[1],x2:to[0],y2:to[1]}))line.setAttribute(key,String(value));
    const circle=document.createElementNS(ns,'circle');
    circle.setAttribute('cx',String(to[0]));circle.setAttribute('cy',String(to[1]));circle.setAttribute('r','4');
    nodes.push(line,circle);
  }
  $('humanoid-links').replaceChildren(...nodes);
}
function controls() {
  const available=Boolean(model&&observation?.connection==='simulated_actuators'&&!busy&&!stopping&&!haltRequired&&!document.hidden&&client.canConfirm());
  const occupied=Boolean(observation?.pending||observation?.active_command);
  $('humanoid-propose').disabled=!available||occupied||observation.stopped;
  $('humanoid-approve').disabled=!available||!observation?.pending||observation.stopped;
  $('humanoid-approval').hidden=!observation?.pending;
  const locked=!available||occupied;
  for(const input of $('humanoid-joints').querySelectorAll('input'))input.disabled=locked;
  $('humanoid-duration').disabled=locked;
  $('humanoid-neutral').disabled=locked;
}
function review(plan) {
  if(reviewedPlan===plan?.plan_sha256)return;
  reviewedPlan=plan?.plan_sha256;
  $('humanoid-password').value='';
  $('humanoid-password-confirmed').checked=false;
  $('humanoid-plan').textContent=plan?`${Object.keys(plan.request.targets).length} joint targets over ${plan.request.duration_seconds} seconds. Confirm the exact start and target values below; Stop to discard this plan.`:'';
  $('humanoid-plan-values').replaceChildren();
  if(!plan)return;
  for(const joint of model.joints) {
    const row=document.createElement('tr');
    for(const text of [label(joint.name),String(plan.start[joint.name]),String(plan.targets[joint.name])]) {
      const cell=document.createElement(row.childElementCount?'td':'th');
      if(cell.tagName==='TH')cell.scope='row';
      cell.textContent=text;row.append(cell);
    }
    $('humanoid-plan-values').append(row);
  }
}
function clear() {
  epoch++;client.cancelPending();client.csrf='';model=observation=null;busy=reading=false;reviewedPlan=null;
  $('humanoid-joints').replaceChildren();$('humanoid-links').replaceChildren();
  $('humanoid-plan').textContent='';$('humanoid-plan-values').replaceChildren();$('humanoid-password').value='';
  $('humanoid-password-confirmed').checked=false;
  $('humanoid-state').textContent='Open this panel while signed in to load the reference model.';
  controls();
}
async function load() {
  if(!panel.open||document.hidden||document.body.dataset.session!=='authenticated')return;
  const current=++epoch;
  try {
    // Observe only after an earlier Stop acknowledgement, including a quick reopen.
    if(stopping)await stopping.catch(()=>{});
    if(current!==epoch)return;
    const session=await client.me();if(current!==epoch)return;client.csrf=session.csrf;client.authenticationMethod=session.authentication_method||'operator';client.canOperate=session.role!=='viewer';client.confirmationExpires=session.confirmation_expires_at||0;
    const value=await client.humanoidModel();if(current!==epoch)return;
    if(value.model_id!=='onesa-humanoid-33-v1'||value.mode!=='simulation'||value.physical_actuation!==false||value.joints.length!==33)throw new Error('Humanoid model could not be verified.');
    model=value;
    $('humanoid-joints').replaceChildren(...model.joints.map(joint=>{
      const row=document.createElement('label'),name=document.createElement('span'),input=document.createElement('input');
      name.textContent=label(joint.name);input.type='number';input.step='0.01';input.min=joint.lower;input.max=joint.upper;input.value='0';input.required=true;input.dataset.joint=joint.name;
      row.append(name,input);return row;
    }));
    await refresh();
  } catch(error) {
    if(current!==epoch)return;
    if(error.status===401)depart();
    else $('humanoid-state').textContent=error.message;
  }
}
function verified(value) {
  if(value.model_id!==model.model_id||value.mode!=='simulation'||value.physical_actuation!==false||typeof value.stopped!=='boolean'||typeof value.owns_active!=='boolean'||!['simulated_actuators','simulator_fault'].includes(value.connection)||!Array.isArray(value.history)||!model.joints.every(j=>Number.isFinite(value.positions?.[j.name])))return false;
  const plan=value.pending;
  return !plan||(plan.model_id===model.model_id&&typeof plan.plan_sha256==='string'&&/^[a-f0-9]{64}$/.test(plan.plan_sha256)&&plan.state==='proposed'&&typeof plan.request?.command_id==='string'&&Number.isFinite(plan.request.duration_seconds)&&model.joints.every(j=>Number.isFinite(plan.start?.[j.name])&&Number.isFinite(plan.targets?.[j.name])));
}
async function refresh(force=false) {
  if(!model||reading||busy&&!force||!panel.open||document.hidden||!client.csrf||stopping)return;
  const current=epoch;reading=true;
  try {
    const value=await client.humanoidState();if(current!==epoch)return;
    if(!verified(value))throw new Error('Humanoid telemetry could not be verified.');
    // Renew only this command after successfully observing it in its owning
    // session. The rest of the console cannot keep unobserved motion alive.
    if(value.connection==='simulated_actuators'&&!value.stopped&&value.active_command&&value.owns_active&&!haltRequired)await client.humanoidHeartbeat(value.active_command);
    if(current!==epoch)return;
    observation=value;
    supervising=Boolean(value.pending||value.owns_active);
    if(!supervising)haltRequired=false;
    draw(value.positions);review(value.pending);
    $('humanoid-state').textContent=haltRequired?'Stop acknowledgement is unavailable; heartbeat paused. Use Stop and verify the result.':value.connection==='simulator_fault'?'Simulated actuator fault · process recovery required':value.stopped?'Stopped · confirm a reset above before proposing motion':value.active_command?'Coordinated motion running · simulated actuators':value.pending?'Proposal ready for review · expires after 15 seconds':value.history.at(-1)?`${value.history.at(-1).state} · ${label(value.history.at(-1).reason)}`:'33 simulated actuators ready';
    controls();
  } catch(error) {
    if(current!==epoch)return;
    if(supervising)haltRequired=true;
    observation=null;$('humanoid-links').replaceChildren();review(null);controls();
    $('humanoid-state').textContent='Telemetry unavailable; heartbeat paused. Stop remains available.';
    if(error.status===401)depart();
  } finally {if(current===epoch)reading=false;}
}
async function mutate(action) {
  if(busy||!observation||stopping||haltRequired)return;
  const current=++epoch;client.cancelPending();reading=false;busy=true;supervising=true;controls();
  try {await action();if(current===epoch)await refresh(true);}
  catch(error){
    if(current!==epoch)return;
    // An interrupted response cannot prove that the server rejected a command.
    observation=null;haltRequired=true;review(null);controls();
    $('humanoid-state').textContent=`${error.message} Use Stop, then refresh the observation.`;
    if(error.status===401)depart();
  }
  finally{if(current===epoch){busy=false;controls();}}
}
$('humanoid-form').addEventListener('submit',event=>{
  event.preventDefault();
  if($('humanoid-propose').disabled)return;
  const targets=Object.fromEntries([...$('humanoid-joints').querySelectorAll('input')].map(input=>[input.dataset.joint,Number(input.value)]));
  void mutate(()=>client.proposeHumanoid({command_id:crypto.randomUUID(),issued_at:Date.now()/1000,duration_seconds:Number($('humanoid-duration').value),targets}));
});
$('humanoid-approval').addEventListener('submit',event=>{
  event.preventDefault();const plan=observation?.pending,password=$('humanoid-password').value,confirmed=Boolean($('humanoid-password-confirmed').checked);$('humanoid-password').value='';$('humanoid-password-confirmed').checked=false;
  if(plan&&!$('humanoid-approve').disabled)void mutate(()=>client.approveHumanoid(plan.request.command_id,{...client.confirmation(password,confirmed),expected_plan_sha256:plan.plan_sha256}));
});
function stop(keepalive=false) {
  if(stopping)return stopping;
  haltRequired=true;
  const stopper=new RobotClient();stopper.csrf=client.csrf;
  stopping=stopper.stop(keepalive).then(value=>{
    if(value.status!=='stopped')throw new Error('Stop acknowledgement could not be verified.');
    supervising=false;
  }).finally(()=>{stopping=null;controls();});
  controls();return stopping;
}
$('humanoid-stop').addEventListener('click',()=>{
  const current=++epoch;client.cancelPending();reading=false;busy=false;
  observation=null;review(null);
  void stop().then(()=>current===epoch&&refresh()).catch(error=>{if(current===epoch)$('humanoid-state').textContent=error.message;});
});
$('humanoid-neutral').addEventListener('click',()=>{for(const input of $('humanoid-joints').querySelectorAll('input'))input.value='0';});
function depart() {
  // Include unresolved mutations and stale observations: aborting a fetch does
  // not undo a proposal or approval that the server has already accepted.
  if(client.csrf&&(supervising||busy||haltRequired))void stop(true).catch(()=>{
    $('notice').textContent='Humanoid Stop could not be confirmed. Its supervision heartbeat is paused; verify the stopped state after reconnecting.';
  });
  clear();
}
panel.addEventListener('toggle',()=>{if(panel.open)void load();else depart();});
new MutationObserver(()=>{depart();if(panel.open)void load();}).observe(document.body,{attributes:true,attributeFilter:['data-session']});
document.addEventListener('visibilitychange',()=>{if(document.hidden)depart();else if(panel.open)void load();});
window.addEventListener('pagehide',depart);
window.addEventListener('pageshow',event=>{if(event.persisted&&panel.open)void load();});
setInterval(()=>void refresh(),500);
controls();
