import {test,expect} from '@playwright/test';
import {loopbackFrontend} from './loopback-frontend.mjs';

async function identityFixture(page,{role='operator',signedIn=false,fresh=true,globalSignout=true,inspection=false}={}){
 const server=await loopbackFrontend('http://localhost:3431',()=>false);
 const writes=[];let stopped=true;
 const profile={id:'synthetic-profile',name:'Synthetic test profile',version:'1.0.0',capabilities:[]};
 const asset={id:'pump-p-101',context_kind:'synthetic',context_version:'1.0.0',source:'synthetic fixture',measures:[]};
 const mission={id:'00000000-0000-4000-8000-000000000001',state:'proposed',record:{plan_sha256:'a'.repeat(64),outcome:'awaiting_approval',elapsed_seconds:0,events:[],observations:[],telemetry:[],findings:[],plan:{version:'1.0.0',scenario_version:'synthetic/v1',title:'Synthetic inspection review',created_at:Date.now()/1000,profile,asset,steps:[],scenario:'normal',seed:101,start_radians:0,target_radians:0.1,duration_seconds:6,timeout_seconds:10}},report:{execution_complete:false,limitations:[],metrics:{scope:'synthetic fixture',elapsed_seconds:0,accuracy_against_scenario:null,interventions:0,recovery:'explicit_new_run_required'}}};
 await page.route('**/api/v1/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname;
  let status=200,body={};
  if(request.method()==='POST')writes.push({path,body:request.postData()?request.postDataJSON():null});
  if(path==='/api/v1/auth/identity/config')body={enabled:true,account_url:'https://account.example.test/account'};
  else if(path==='/api/v1/auth/me'){
   status=signedIn?200:401;
   body=signedIn?{csrf:'synthetic-csrf',name:'Synthetic Onesa User',role,authentication_method:'onesa',confirmation_expires_at:fresh?Date.now()/1000+240:null,global_administrator:role==='administrator'}:{detail:'Sign in required'};
  }else if(path==='/api/v1/status')body={mode:'simulation',physical_actuation:false,stopped,position_radians:0,active_task:null,sampled_at:Date.now()/1000,capabilities:{edge_telemetry:'not_connected'},deployment_environment:'local'};
  else if(path==='/api/v1/tasks')body={tasks:[]};
  else if(path==='/api/v1/heartbeat')body={status:'observed'};
  else if(path==='/api/v1/reset'){stopped=false;body={status:'ready',physical_actuation:false};}
  else if(path==='/api/v1/stop'){stopped=true;body={status:'stopped',physical_actuation:false};}
  else if(path==='/api/v1/auth/identity/start')body={authorization_url:'https://account.example.test/api/v1/identity/auth?state=synthetic-state'};
  else if(path==='/api/v1/auth/logout'){signedIn=false;body={status:'signed_out',physical_actuation:false,identity_signed_out:globalSignout};}
  else if(path==='/api/v1/admin/physical-operation')body={permission:'blocked',revision:0,expires_at:null,controller_connection:'not_connected',controller_readiness:'not_ready',physical_operation:'inactive',physical_actuation:false,sampled_at:Date.now()/1000};
  else if(inspection&&path==='/api/v1/missions/catalog')body={version:'1.0.0',mode:'simulation',physical_actuation:false,assets:[asset],robots:[profile],skills:[],scenarios:[{id:'normal',label:'Synthetic normal scenario'}],asset_context_import:'disabled'};
  else if(inspection&&path==='/api/v1/missions')body=request.method()==='POST'?mission:{missions:[]};
  else if(inspection&&path===`/api/v1/missions/${mission.id}/approve`){mission.state='running';mission.record.outcome='running';body=mission;}
  else if(inspection&&path===`/api/v1/missions/${mission.id}`)body=mission;
  else{status=404;body={detail:'Synthetic route not selected'};}
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.route('https://account.example.test/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><title>Onesa account fixture</title><h1>Onesa account sign-in</h1>'}));
 return {server,writes};
}

for(const width of [390,768,1280])test(`one-account sign-in is accessible at ${width}px`,async({page})=>{
 const fixture=await identityFixture(page);
 try{
  await page.setViewportSize({width,height:850});await page.goto(fixture.server.origin+'/console');
  await expect(page.getByRole('button',{name:'Continue with Onesa',exact:true})).toBeVisible();
  await expect(page.locator('#login-form')).toBeHidden();
  await expect(page.getByRole('link',{name:'Manage your account and product access'})).toHaveAttribute('href','https://account.example.test/account');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.getByRole('button',{name:'Continue with Onesa',exact:true}).focus();
  await expect(page.getByRole('button',{name:'Continue with Onesa',exact:true})).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading',{name:'Onesa account sign-in'})).toBeVisible();
  expect(fixture.writes).toEqual([{path:'/api/v1/auth/identity/start',body:{step_up:false}}]);
 }finally{await fixture.server.close();}
});

test('identity operations require explicit review without sharing a password',async({page})=>{
 const fixture=await identityFixture(page,{signedIn:true});
 try{
  await page.goto(fixture.server.origin+'/console');
  await expect(page.locator('#onesa-session-role')).toContainText('Robot operator');
  await expect(page.locator('#reset-password')).toBeHidden();
  await expect(page.locator('#reset-password-confirmed')).toBeVisible();
  await expect(page.locator('#reset-submit')).toBeEnabled();
  await page.locator('#reset-submit').click();
  expect(fixture.writes.filter(value=>value.path==='/api/v1/reset')).toEqual([]);
  await page.getByLabel('I reviewed the stopped state and confirm this reset.').check();
  await page.locator('#reset-submit').click();
  await expect.poll(()=>fixture.writes.filter(value=>value.path==='/api/v1/reset')).toEqual([{path:'/api/v1/reset',body:{confirmed:true}}]);
 }finally{await fixture.server.close();}
});

test('stale identity offers MFA confirmation without replaying an operation',async({page})=>{
 const fixture=await identityFixture(page,{signedIn:true,fresh:false});
 try{
  await page.goto(fixture.server.origin+'/console');
  await expect(page.locator('#onesa-confirmation-status')).toContainText('Confirm your Onesa identity with MFA');
  await expect(page.locator('#reset-submit')).toBeDisabled();
  await page.getByRole('button',{name:'Confirm identity with MFA',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Onesa account sign-in'})).toBeVisible();
  expect(fixture.writes.filter(value=>value.path!=='/api/v1/heartbeat')).toEqual([{path:'/api/v1/auth/identity/start',body:{step_up:true}}]);
 }finally{await fixture.server.close();}
});

test('inspection review explains central MFA and sends explicit exact-plan approval',async({page})=>{
 const fixture=await identityFixture(page,{signedIn:true,inspection:true});
 try{
  await page.goto(fixture.server.origin+'/console');
  await page.getByLabel('I reviewed the stopped state and confirm this reset.').check();
  await page.locator('#reset-submit').click();
  await expect(page.locator('#mission-submit')).toBeEnabled();
  await page.locator('#mission-submit').click();
  await expect(page.locator('#mission-pending')).toContainText('current Onesa MFA session');
  await expect(page.locator('#mission-pending')).not.toContainText('Password');
  await expect(page.locator('#mission-password')).toBeHidden();
  await expect(page.locator('#mission-password-confirmed')).toBeFocused();
  await page.getByLabel('I reviewed this exact inspection plan and confirm approval.').check();
  await page.locator('#mission-approve-submit').click();
  await expect.poll(()=>fixture.writes.find(value=>value.path.endsWith('/approve'))?.body).toEqual({confirmed:true,expected_plan_sha256:'a'.repeat(64)});
 }finally{await fixture.server.close();}
});

test('viewer keeps a read-only workspace with Stop and sign-out available',async({page})=>{
 const fixture=await identityFixture(page,{signedIn:true,role:'viewer'});
 try{
  await page.goto(fixture.server.origin+'/console');
  await expect(page.locator('#onesa-session-role')).toContainText('Viewer · read only');
  await expect(page.locator('#reset-submit')).toBeDisabled();
  await expect(page.locator('#proposal-submit')).toBeDisabled();
  await expect(page.locator('#administrator')).toBeHidden();
  await expect(page.getByRole('button',{name:'Stop simulation',exact:true})).toBeEnabled();
  await expect(page.getByRole('button',{name:'Sign out of Onesa',exact:true})).toBeEnabled();
 }finally{await fixture.server.close();}
});

test('global administrator is visible but hardware remains unconnected',async({page})=>{
 const fixture=await identityFixture(page,{signedIn:true,role:'administrator'});
 try{
  await page.goto(fixture.server.origin+'/console');
  await expect(page.locator('#onesa-session-role')).toContainText('Onesa administrator · all applications');
  await expect(page.locator('#administrator')).toBeVisible();
  await expect(page.locator('#physical-controller-state')).toContainText('Not connected');
  await expect(page.locator('#physical-permission-password')).toBeHidden();
  await expect(page.locator('#physical-permission-password-confirmed')).toBeVisible();
 }finally{await fixture.server.close();}
});

test('an unavailable global logout reports local sign-out honestly',async({page})=>{
 const fixture=await identityFixture(page,{signedIn:true,globalSignout:false});
 try{
  await page.goto(fixture.server.origin+'/console');
  await page.getByRole('button',{name:'Sign out of Onesa',exact:true}).click();
  await expect(page.locator('#operator')).toBeHidden();
  await expect(page.locator('#notice')).toContainText('Signed out of Robot. Open your Onesa account');
  await expect(page.getByRole('button',{name:'Continue with Onesa',exact:true})).toBeVisible();
 }finally{await fixture.server.close();}
});

test('missing product access has a clear recovery route and removes callback state',async({page})=>{
 const fixture=await identityFixture(page);
 try{
  await page.goto(fixture.server.origin+'/console?identity=access');
  await expect(page.locator('#notice')).toContainText('Robot access is not enabled');
  await expect(page.getByRole('link',{name:'Manage your account and product access'})).toBeVisible();
  await expect(page).toHaveURL(fixture.server.origin+'/console');
 }finally{await fixture.server.close();}
});
