import {test,expect} from '@playwright/test';

test('simulation controls wait for a verified snapshot and stop remains available',async({page})=>{
 let releaseInitial;const initial=new Promise(resolve=>releaseInitial=resolve);let released=false,unavailable=false,stopped=true,resets=0;
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname;let status=200,body={};
  if(path.endsWith('/auth/me'))body={csrf:'synthetic-observation-session',name:'synthetic-owner',role:'operator'};
  else if(path.endsWith('/status')||path.endsWith('/tasks')){
   if(!released)await initial;
   if(unavailable){status=503;body={detail:'Synthetic observation unavailable'};}
   else body=path.endsWith('/tasks')?{tasks:[]}:{mode:'simulation',physical_actuation:false,stopped,position_radians:0,active_task:null,sampled_at:Date.now()/1000,capabilities:{edge_telemetry:'not_connected'},deployment_environment:'local'};
  }else if(path.endsWith('/reset')){resets++;stopped=false;}
  else if(!path.endsWith('/heartbeat')){status=404;body={detail:'Unexpected fixture route'};}
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 });
 try{
  await page.goto('/console');await expect(page.getByRole('button',{name:'Sign out',exact:true})).toBeVisible();
  await page.locator('#joint-tools > summary').click();
  const reset=page.getByRole('button',{name:'Reset simulation stop',exact:true});const propose=page.getByRole('button',{name:'Create proposal',exact:true});
  await expect(reset).toBeDisabled({timeout:1000});await expect(propose).toBeDisabled();
  await expect(page.getByRole('button',{name:'Stop simulation',exact:true})).toBeEnabled();await expect(page.getByRole('button',{name:'Sign out',exact:true})).toBeEnabled();
  await page.locator('#reset-form').dispatchEvent('submit');expect(resets).toBe(0);
  released=true;releaseInitial();await expect(reset).toBeEnabled();await expect(page.locator('#stop-state')).toContainText('Stopped');
  await page.getByLabel('Confirm password to reset').fill('synthetic-reset-only');await reset.click();await expect(page.locator('#notice')).toContainText('Simulation stop reset.');await expect(page.locator('#stop-state')).toContainText('Ready');expect(resets).toBe(1);
  unavailable=true;await expect(page.locator('#stop-state')).toContainText('Unavailable');await expect(reset).toBeDisabled();await expect(propose).toBeDisabled();await expect(page.getByRole('button',{name:'Stop simulation',exact:true})).toBeEnabled();await expect(page.getByRole('button',{name:'Sign out',exact:true})).toBeEnabled();
 }finally{released=true;releaseInitial();}
});
