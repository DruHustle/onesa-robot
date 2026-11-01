import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import AxeBuilder from '@axe-core/playwright';

const schema=JSON.parse(readFileSync(new URL('../../contracts/openapi.json',import.meta.url)));
const definition=Object.values(schema.components.schemas).find(value=>value.properties?.joints?.default?.length===33);
const model={model_id:'onesa-humanoid-33-v1',mode:'simulation',physical_actuation:false,joints:definition.properties.joints.default};
const positions=()=>Object.fromEntries(model.joints.map(j=>[j.name,0]));

async function fixture(page){
 let pending=null,active=null,stops=0,unavailable=false,authenticated=true,stopped=false;
 let heldPath=null,release=null,entered=0,heartbeats=0;
 const observation=()=>({model_id:model.model_id,mode:'simulation',connection:'simulated_actuators',physical_actuation:false,stopped,owns_active:Boolean(active),positions:positions(),pending,active_command:active,history:[]});
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname;let status=200,body={};
  if(path.endsWith('/stop')){stops++;stopped=true;pending=active=null;body={status:'stopped'};}
  else if(!authenticated){status=401;body={detail:'Sign in required'};}
  else if(path.endsWith('/auth/me'))body={csrf:'synthetic-humanoid-session',name:'synthetic-owner',role:'operator'};
  else if(path.endsWith('/auth/logout')){authenticated=false;pending=active=null;}
  else if(path.endsWith('/reset')){stopped=false;body={status:'reset'};}
  else if(path.endsWith('/humanoid/model'))body=model;
  else if(path.endsWith('/humanoid/state')){status=unavailable?503:200;body=observation();}
  else if(path.endsWith('/humanoid/proposals')){
   const request=route.request().postDataJSON();
   pending={model_id:model.model_id,request,revision:0,start:positions(),targets:{...positions(),...request.targets},state:'proposed',plan_sha256:'a'.repeat(64)};
   body=pending;status=201;
  }else if(path.includes('/humanoid/proposals/')&&path.endsWith('/approve')){
   expect(route.request().postDataJSON().expected_plan_sha256).toBe(pending.plan_sha256);
   active=pending.request.command_id;pending=null;body=observation();
  }else if(path.endsWith('/humanoid/heartbeat')){
   expect(route.request().postDataJSON().command_id).toBe(active);heartbeats++;
   body={status:'received'};
  }else if(path.endsWith('/tasks'))body={tasks:[]};
  else if(path.endsWith('/status'))body={mode:'simulation',physical_actuation:false,stopped,position_radians:0,active_task:null,sampled_at:Date.now()/1000,capabilities:{edge_telemetry:'not_connected'},deployment_environment:'local'};
  else if(!path.endsWith('/heartbeat')){status=404;body={detail:'Unexpected fixture route'};}
  if(heldPath&&path.endsWith(heldPath)){
   entered++;
   await new Promise(resolve=>{release=resolve;});
  }
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)}).catch(()=>{});
 });
 await page.goto('/console');
 await expect(page.getByRole('button',{name:'Sign out',exact:true})).toBeVisible();
 await page.locator('#humanoid > summary').click();
 await expect(page.locator('#humanoid-joints input')).toHaveCount(33);
 await expect(page.locator('#humanoid-propose')).toBeEnabled();
 return {
  get stops(){return stops;},get entered(){return entered;},get heartbeats(){return heartbeats;},
  get pending(){return pending;},get active(){return active;},
  fail(){unavailable=true;},loseSession(){authenticated=false;},
  hold(path){heldPath=path;},release(){heldPath=null;release?.();},
  reset(){stopped=false;},
 };
}

async function propose(page){
 await page.locator('#humanoid-propose').click();
 await expect(page.locator('#humanoid-approval')).toBeVisible();
}
async function approve(page){
 await page.getByLabel('Confirm password for humanoid motion').fill('synthetic-confirmation-only');
 await page.locator('#humanoid-approve').click();
 await expect(page.locator('#humanoid-state')).toContainText('Coordinated motion running');
}

test('whole-body review uses exact immutable targets and preserves input through appearance changes',async({page})=>{
 const state=await fixture(page);
 const elbow=page.getByLabel('left arm elbow',{exact:true});
 await elbow.fill('0.2');await elbow.focus();
 await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});
 await expect(elbow).toHaveValue('0.2');await expect(elbow).toBeFocused();
 await propose(page);
 await expect(page.locator('#humanoid-plan')).toContainText('33 joint targets');
 await expect(page.locator('#humanoid-plan-values tr')).toHaveCount(33);
 await expect(page.getByRole('row').filter({has:page.getByRole('rowheader',{name:'left arm elbow',exact:true})})).toHaveText('left arm elbow00.2');
 await expect(elbow).toBeDisabled();
 await expect(page.locator('#humanoid-neutral')).toBeDisabled();
 await expect(page.locator('#humanoid-duration')).toBeDisabled();
 // Even programmatic editing of the stale draft cannot alter the review snapshot.
 await elbow.evaluate(input=>{input.value='0.7';});
 await expect(page.getByRole('row').filter({has:page.getByRole('rowheader',{name:'left arm elbow',exact:true})})).toHaveText('left arm elbow00.2');
 await approve(page);
 await expect(page.locator('#humanoid-password')).toHaveValue('');
 await expect.poll(()=>state.heartbeats).toBeGreaterThan(0);
 await page.locator('#humanoid-stop').click();
 await expect.poll(()=>state.stops).toBe(1);
 await expect(page.locator('#humanoid-state')).toContainText('Stopped');
 await expect(page.locator('#humanoid-propose')).toBeDisabled();
 await page.setViewportSize({width:320,height:900});
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),{message:'Mobile layout must settle within the viewport'}).toBe(true);
 expect((await new AxeBuilder({page}).include('#humanoid').withTags(['wcag2a','wcag2aa','wcag21aa']).analyze()).violations).toEqual([]);
 await page.getByRole('button',{name:'Sign out',exact:true}).click();
 await expect(page.locator('#humanoid-joints input')).toHaveCount(0);
});

test('telemetry failure pauses supervision and closing still stops unresolved motion',async({page})=>{
 const state=await fixture(page);
 await propose(page);await approve(page);
 state.fail();
 await expect(page.locator('#humanoid-state')).toContainText('Telemetry unavailable');
 await expect(page.locator('#humanoid-propose')).toBeDisabled();
 const heartbeats=state.heartbeats;
 await page.locator('#humanoid > summary').click();
 await expect.poll(()=>state.stops).toBe(1);
 await expect(page.locator('#humanoid-joints input')).toHaveCount(0);
 expect(state.heartbeats).toBe(heartbeats);
 expect(state.active).toBeNull();
});

test('closing during an unresolved proposal sends independent Stop and discards a late response',async({page})=>{
 const state=await fixture(page);state.hold('/humanoid/proposals');
 await page.locator('#humanoid-propose').click();
 await expect.poll(()=>state.entered).toBe(1);
 try {
  await page.locator('#humanoid > summary').click();
  await expect.poll(()=>state.stops).toBe(1);
  expect(state.pending).toBeNull();
 } finally {state.release();}
 await expect(page.locator('#humanoid-joints input')).toHaveCount(0);
 await page.locator('#humanoid > summary').click();
 await expect(page.locator('#humanoid-state')).toContainText('Stopped');
 await expect(page.locator('#humanoid-approval')).toBeHidden();
});

test('Stop remains independent while approval acknowledgement is pending',async({page})=>{
 const state=await fixture(page);await propose(page);state.hold('/approve');
 await page.getByLabel('Confirm password for humanoid motion').fill('synthetic-confirmation-only');
 await page.locator('#humanoid-approve').click();
 await expect.poll(()=>state.entered).toBe(1);
 try {
  await page.locator('#humanoid-stop').click();
  await expect.poll(()=>state.stops).toBe(1);
  await expect(page.locator('#humanoid-state')).toContainText('Stopped');
  expect(state.active).toBeNull();
 } finally {state.release();}
 await expect(page.locator('#humanoid-password')).toHaveValue('');
 await expect(page.locator('#humanoid-approval')).toBeHidden();
});

test('page departure requests Stop while session loss clears the protected controls',async({page})=>{
 const state=await fixture(page);await propose(page);await approve(page);
 await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
 await expect.poll(()=>state.stops).toBe(1);
 await expect(page.locator('#humanoid-joints input')).toHaveCount(0);
 // A fresh visit must observe stopped state, never silently approve or resume.
 await page.reload();
 await page.locator('#humanoid > summary').click();
 await expect(page.locator('#humanoid-state')).toContainText('Stopped');
 state.reset();
 await expect(page.locator('#humanoid-propose')).toBeEnabled();
 await propose(page);
 state.loseSession();
 await expect(page.locator('#humanoid-joints input')).toHaveCount(0);
 await expect(page.locator('#humanoid-password')).toHaveValue('');
});
