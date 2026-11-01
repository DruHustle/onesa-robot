import {test,expect} from '@playwright/test';

// Intercepted timing fixture only; no hosted acceptance, operator account or command.
const catalog={version:'1.0.0',mode:'simulation',physical_actuation:false,assets:[],robots:[{id:'synthetic-profile',name:'Synthetic test profile'}],skills:[],scenarios:[{id:'normal',label:'Synthetic normal scenario',source:'local timing fixture'}],asset_context_import:'disabled'};
const snapshot={mode:'simulation',physical_actuation:false,stopped:false,active_task:null,position_radians:0,sampled_at:1,capabilities:{edge_telemetry:'not_connected'},deployment_environment:'local'};

async function controlCatalog(page,newer='held'){
 let releaseCatalog,releaseFuture,requested;
 const pendingCatalog=new Promise(resolve=>releaseCatalog=resolve),future=new Promise(resolve=>releaseFuture=resolve),catalogRequested=new Promise(resolve=>requested=resolve);
 const reads={status:0,tasks:0};
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname;let status=200,body={};
  if(path.endsWith('/auth/me'))body={csrf:'synthetic-catalog-session',name:'synthetic-owner',role:'operator'};
  else if(path.endsWith('/heartbeat'))body={status:'received',physical_actuation:false};
  else if(path.endsWith('/status')||path.endsWith('/tasks')){
   // Control observation generations directly. Heartbeat follows a verified
   // observation and must never be needed to advance this read-only fixture.
   const operation=path.endsWith('/tasks')?'tasks':'status',generation=++reads[operation];
   if(generation>(newer==='held'?1:2))await future;
   if(generation>=2&&newer==='unavailable'){status=503;body={detail:'Synthetic observation unavailable'};}
   else body=operation==='tasks'?{tasks:[]}:{...snapshot,stopped:generation>=2&&newer==='stopped'};
  }else if(path.endsWith('/missions/catalog')){requested();await pendingCatalog;body=catalog;}
  else if(path.endsWith('/missions'))body={missions:[]};
  else{status=404;body={detail:'Unexpected synthetic timing route'};}
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 });
 return {catalogRequested,releaseCatalog,async close(){releaseCatalog();releaseFuture();await page.unrouteAll({behavior:'ignoreErrors'});}};
}

test('completed catalog enables a ready plan without waiting for another poll',async({page})=>{
 const fixture=await controlCatalog(page);
 try{
  await page.goto('/console');await fixture.catalogRequested;
  await expect(page.locator('#mission-submit')).toBeDisabled();
  fixture.releaseCatalog();
  await expect(page.locator('#mission-scenario option')).toHaveCount(1);
  await expect(page.locator('#mission-submit')).toBeEnabled({timeout:1500});
 }finally{await fixture.close();}
});

for(const newer of ['stopped','unavailable'])test(`late catalog cannot enable controls after a newer ${newer} observation`,async({page})=>{
 const fixture=await controlCatalog(page,newer);
 try{
  await page.goto('/console');await fixture.catalogRequested;
  await expect(page.locator('#mission-readiness')).toContainText(newer==='stopped'?'Stopped':'Disconnected',{timeout:8000});
  fixture.releaseCatalog();
  await expect(page.locator('#mission-scenario option')).toHaveCount(1);
  await expect(page.locator('#mission-submit')).toBeDisabled();
  await expect(page.locator('#mission-readiness')).toContainText(newer==='stopped'?'Stopped':'Disconnected');
 }finally{await fixture.close();}
});

test('catalog completion preserves an in-flight form guard',async({page})=>{
 const fixture=await controlCatalog(page);
 try{
  await page.goto('/console');await fixture.catalogRequested;
  await page.locator('#mission-form').evaluate(form=>form.setAttribute('aria-busy','true'));
  fixture.releaseCatalog();
  await expect(page.locator('#mission-scenario option')).toHaveCount(1);
  await expect(page.locator('#mission-submit')).toBeDisabled();
 }finally{await fixture.close();}
});
