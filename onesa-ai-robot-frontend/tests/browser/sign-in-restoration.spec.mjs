import {test,expect} from '@playwright/test';

function deferred(){let resolve;const promise=new Promise(yes=>resolve=yes);return {promise,resolve};}

test('late session restoration cannot erase a submitted sign-in during readiness',async({page})=>{
 const restore=deferred(),ready=deferred(),readyStarted=deferred();let loginRequests=0,submittedIntact=false;
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname;let status=200,body={};
  if(path==='/api/v1/auth/me'){await restore.promise;status=401;body={detail:'Synthetic anonymous session'};}
  else if(path==='/api/v1/ready'){readyStarted.resolve();await ready.promise;body={status:'ready',mode:'simulation',physical_actuation:false};}
  else if(path==='/api/v1/auth/login'){
   loginRequests++;const input=route.request().postDataJSON();
   submittedIntact=input.name==='synthetic-owner'&&input.password==='synthetic-submission-only';
   status=submittedIntact?200:401;body=submittedIntact?{csrf:'synthetic-csrf',name:'synthetic-owner',role:'operator'}:{detail:'Synthetic credentials rejected'};
  }else if(path==='/api/v1/status')body={mode:'simulation',physical_actuation:false,stopped:true,position_radians:0,active_task:null,sampled_at:Date.now()/1000,capabilities:{edge_telemetry:'not_connected'},deployment_environment:'local'};
  else if(path==='/api/v1/tasks')body={tasks:[]};
  else if(path!=='/api/v1/heartbeat'){status=404;body={detail:'Unselected synthetic route'};}
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 });
 try{
  await page.goto('/console');
  await page.getByLabel('Operator name',{exact:true}).fill('synthetic-owner');
  await page.getByLabel('Password',{exact:true}).fill('synthetic-submission-only');
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await readyStarted.promise;
  const restored=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/auth/me');
  restore.resolve();await (await restored).finished();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  ready.resolve();
  await expect(page.locator('#operator')).toBeVisible({timeout:3000});
  expect(loginRequests).toBe(1);expect(submittedIntact).toBe(true);
  await expect(page.getByLabel('Password',{exact:true})).toHaveValue('');
 }finally{restore.resolve();ready.resolve();}
});

test('a missing application module cannot submit a password in a navigation URL',async({page})=>{
 await page.route('**/app.js',route=>route.abort());
 const urls=[];page.on('request',request=>urls.push(request.url()));
 await page.goto('/console');
 const form=page.locator('#login-form');
 expect(await form.evaluate(element=>element.method)).toBe('post');
 await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeDisabled();
 await page.getByLabel('Operator name',{exact:true}).fill('synthetic-owner');
 await page.getByLabel('Password',{exact:true}).fill('synthetic-no-navigation');
 await page.getByLabel('Password',{exact:true}).press('Enter');
 expect(urls.some(url=>url.includes('synthetic-no-navigation')||url.includes('password='))).toBe(false);
 expect(new URL(page.url()).search).toBe('');
});
