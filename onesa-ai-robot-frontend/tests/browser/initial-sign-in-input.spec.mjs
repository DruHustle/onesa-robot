import {test,expect} from '@playwright/test';

for(const accepted of [true,false])test(`initial anonymous lookup preserves entries typed before submission; explicit sign-in ${accepted?'succeeds':'is rejected'}`,async({page})=>{
 let release,started;
 const restored=new Promise(resolve=>release=resolve),lookupStarted=new Promise(resolve=>started=resolve);
 let logins=0,session=false;const mutations=[];
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname;let status=200,body={};
  if(path==='/api/v1/auth/me'){started();await restored;status=401;body={detail:'Synthetic anonymous lookup'};}
  else if(path==='/api/v1/ready')body={status:'ready',mode:'simulation',physical_actuation:false};
  else if(path==='/api/v1/auth/login'){
   logins++;expect(route.request().postDataJSON()).toEqual({name:'synthetic-owner',password:'synthetic-typed-before-lookup'});
   session=accepted;status=accepted?200:401;body=accepted?{csrf:'synthetic-csrf',name:'synthetic-owner',role:'operator'}:{detail:'Synthetic credentials rejected'};
  }else if(path==='/api/v1/auth/logout'){session=false;body={status:'signed_out',physical_actuation:false};}
  else if(path==='/api/v1/status')body={mode:'simulation',physical_actuation:false,stopped:true,position_radians:0,active_task:null,sampled_at:Date.now()/1000,capabilities:{edge_telemetry:'not_connected'},deployment_environment:'local'};
  else if(path==='/api/v1/tasks')body={tasks:[]};
  else if(path!=='/api/v1/heartbeat'){status=404;body={detail:'Unselected synthetic route'};}
  if(route.request().method()==='POST'&&!['/api/v1/auth/login','/api/v1/auth/logout','/api/v1/heartbeat'].includes(path))mutations.push(path);
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 });
 try{
  await page.goto('/console');await lookupStarted;
  await page.getByLabel('Operator name',{exact:true}).fill('synthetic-owner');
  await page.getByLabel('Password',{exact:true}).fill('synthetic-typed-before-lookup');
  const response=page.waitForResponse(value=>new URL(value.url()).pathname==='/api/v1/auth/me');release();await (await response).finished();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await expect(page.getByLabel('Password',{exact:true})).toHaveValue('synthetic-typed-before-lookup',{timeout:1500});
  await expect(page.getByLabel('Operator name',{exact:true})).toHaveValue('synthetic-owner');expect(logins).toBe(0);
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  if(accepted){
   await expect(page.locator('#operator')).toBeVisible();await expect(page.locator('#reset-form')).toBeVisible();
   await page.locator('#reset-password').fill('synthetic-unsubmitted-reset');await page.getByRole('button',{name:'Sign out',exact:true}).click();
   await expect(page.locator('#login-form')).toBeVisible();
  }else{await expect(page.locator('#notice')).toContainText('Synthetic credentials rejected');await expect(page.locator('#operator')).toBeHidden();}
  expect(logins).toBe(1);expect(session).toBe(false);expect(mutations).toEqual([]);
  expect(await page.locator('input[type="password"]').evaluateAll(fields=>fields.every(field=>field.value===''))).toBe(true);
 }finally{release();}
});
