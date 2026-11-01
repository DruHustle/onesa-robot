import {test,expect} from '@playwright/test';
import {waitForOperatorSignIn} from './sign-in-acceptance.mjs';

test('protected preview readiness retains its same-origin cookie before one deliberate sign-in',async({page})=>{
 let session=false,logins=0,authorizedReady=false;const mutations=[];
 await page.route('**/api/v1/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname;let status=200,body={};
  if(path.endsWith('/ready')){
   const headers=await request.allHeaders();
   authorizedReady=(headers.cookie??'').split(';').some(value=>value.trim()==='robot_preview_access=synthetic-only');
   status=authorizedReady?200:401;
   body=authorizedReady?{status:'ready',mode:'simulation',physical_actuation:false}:{detail:'Synthetic preview access required'};
  }else if(path.endsWith('/auth/me')){status=session?200:401;body=session?{csrf:'synthetic-preview-csrf'}:{detail:'Synthetic signed out'};}
  else if(path.endsWith('/auth/login')){
   expect(authorizedReady).toBe(true);expect(request.postDataJSON()).toEqual({name:'synthetic-preview-operator',password:'synthetic-preview-password'});
   session=true;logins++;body={csrf:'synthetic-preview-csrf'};
  }else if(path.endsWith('/status'))body={mode:'simulation',physical_actuation:false,stopped:true,position_radians:0,active_task:null,sampled_at:Date.now()/1000,capabilities:{edge_telemetry:'not_connected'},deployment_environment:'local'};
  else if(path.endsWith('/tasks'))body={tasks:[]};
  if(request.method()==='POST'&&!path.endsWith('/auth/login')&&!path.endsWith('/heartbeat'))mutations.push(path);
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto('/console');
 await page.context().addCookies([{name:'robot_preview_access',value:'synthetic-only',url:new URL(page.url()).origin,httpOnly:true,sameSite:'Lax'}]);
 await page.getByLabel('Operator name').fill('synthetic-preview-operator');
 await page.getByLabel('Password',{exact:true}).fill('synthetic-preview-password');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await expect(page.getByRole('button',{name:'Sign out',exact:true})).toBeVisible({timeout:5000});
 expect(authorizedReady).toBe(true);expect(logins).toBe(1);expect(mutations).toEqual([]);
 await expect(page.getByLabel('Password',{exact:true})).toHaveValue('');
});

test('sign-in acknowledgement waits for bounded readiness without replaying credentials',async({page})=>{
 let allowReady=false,session=false,logins=0;const mutations=[];
 // Every API request stays in this synthetic fixture, including initial session reads.
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  let status=200,body={};
  if(path.endsWith('/ready')){status=allowReady?200:503;body=allowReady?{status:'ready',mode:'simulation',physical_actuation:false}:{detail:'Synthetic readiness delay'};}
  else if(path.endsWith('/auth/me')){status=session?200:401;body=session?{csrf:'fixture-csrf'}:{detail:'Synthetic signed out'};}
  else if(path.endsWith('/auth/login')){expect(allowReady).toBe(true);session=true;logins++;body={csrf:'fixture-csrf'};}
  else if(path.endsWith('/auth/logout'))session=false;
  else if(path.endsWith('/status'))body={mode:'simulation',physical_actuation:false,stopped:true,position_radians:0,active_task:null,sampled_at:Date.now()/1000,capabilities:{edge_telemetry:'disconnected'},deployment_environment:'local'};
  else if(path.endsWith('/tasks'))body={tasks:[]};
  if(route.request().method()==='POST'&&!/\/auth\/(?:login|logout)$/.test(path)&&!path.endsWith('/heartbeat'))mutations.push(path);
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto('/console');
 await page.getByLabel('Operator name').fill('synthetic-operator');
 await page.getByLabel('Password',{exact:true}).fill('synthetic-password');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await expect(page.locator('#notice')).toContainText('Checking simulator readiness');
 // Deliberately exceed the old 5s assertion while staying inside the real 20s readiness bound.
 await page.waitForTimeout(5200);
 expect(logins).toBe(0);await expect(page.getByRole('button',{name:'Sign out'})).toBeHidden();
 allowReady=true;await waitForOperatorSignIn(page);
 expect(logins).toBe(1);expect(mutations).toEqual([]);
 await expect(page.getByLabel('Password',{exact:true})).toHaveValue('');
 await page.getByRole('button',{name:'Sign out'}).click();
 await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeVisible();
});

test('sign-in observer covers the existing readiness and credential request budgets',async({page})=>{
 test.setTimeout(40000);
 let readySince=0,loginAt=0,logins=0,session=false;const mutations=[];
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname;let status=200,body={};
  if(path.endsWith('/ready')){
   if(!readySince)readySince=performance.now();
   const ready=performance.now()-readySince>=19000;
   status=ready?200:503;body=ready?{status:'ready',mode:'simulation',physical_actuation:false}:{detail:'Synthetic bounded readiness delay'};
  }else if(path.endsWith('/auth/me')){status=session?200:401;body=session?{csrf:'synthetic-budget-session'}:{detail:'Synthetic signed out'};}
  else if(path.endsWith('/auth/login')){
   logins++;loginAt=performance.now();
   await new Promise(resolve=>setTimeout(resolve,6500));
   session=true;body={csrf:'synthetic-budget-session'};
  }else if(path.endsWith('/status'))body={mode:'simulation',physical_actuation:false,stopped:true,position_radians:0,active_task:null,sampled_at:Date.now()/1000,capabilities:{edge_telemetry:'not_connected'},deployment_environment:'local'};
  else if(path.endsWith('/tasks'))body={tasks:[]};
  if(route.request().method()==='POST'&&!path.endsWith('/auth/login')&&!path.endsWith('/heartbeat'))mutations.push(path);
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto('/console');
 await page.getByLabel('Operator name').fill('synthetic-budget-operator');
 await page.getByLabel('Password',{exact:true}).fill('synthetic-budget-password');
 const submitted=performance.now();
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await waitForOperatorSignIn(page);
 expect(loginAt-readySince).toBeGreaterThanOrEqual(19000);
 expect(performance.now()-submitted).toBeGreaterThan(25000);
 expect(logins).toBe(1);expect(mutations).toEqual([]);
 await expect(page.getByLabel('Password',{exact:true})).toHaveValue('');
});
