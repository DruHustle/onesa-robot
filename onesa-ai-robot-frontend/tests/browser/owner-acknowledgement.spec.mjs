import {test,expect} from '@playwright/test';
import {mkdtemp,copyFile,symlink,rm} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {dirname,join} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';

let directory,observe,observeSignIn;
test.beforeAll(async()=>{
 directory=await mkdtemp(join(tmpdir(),'onesa-owner-acknowledgement-'));
 const require=createRequire(import.meta.url);
 const modules=dirname(dirname(dirname(require.resolve('@playwright/test/package.json'))));
 await symlink(modules,join(directory,'node_modules'),'dir');
 await copyFile(new URL('../../../scripts/release/oracle_robot_browser.mjs',import.meta.url),join(directory,'browser.mjs'));
 const module=await import(pathToFileURL(join(directory,'browser.mjs')).href);
 observe=module.observeSimulationAcknowledgement;observeSignIn=module.observeSignInTransport;
});
test.afterAll(async()=>{if(directory)await rm(directory,{recursive:true});});

async function fixture(page,path,{status=200,body={status:'stopped',physical_actuation:false},delay=0}={}){
 let requests=0;
 await page.route('http://acknowledgement.test/**',async route=>{
  if(new URL(route.request().url()).pathname==='/'){
   await route.fulfill({contentType:'text/html',body:`<!doctype html><button id="act">Submit</button><form id="dependent" hidden>Confirmed</form><script>document.querySelector('#act').onclick=async()=>{const response=await fetch(${JSON.stringify(path)},{method:'POST'});if(response.ok){await response.json();document.querySelector('#dependent').hidden=false;}};</script>`});return;
  }
  requests++;
  if(delay)await new Promise(resolve=>setTimeout(resolve,delay));
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto('http://acknowledgement.test/');
 return ()=>requests;
}

test('a six-second Stop acknowledgement precedes unchanged visible-state assertions',async({page})=>{
 const count=await fixture(page,'/api/v1/stop',{delay:6000});
 const result=await observe(page,'/api/v1/stop',()=>page.locator('#act').click());
 await expect(page.locator('#dependent')).toBeVisible();
 expect(result.status).toBe('stopped');expect(result.elapsed_ms).toBeGreaterThanOrEqual(6000);expect(count()).toBe(1);
});

for(const [path,result]of [['/api/v1/reset','reset'],['/api/v1/auth/logout','signed_out']]){
 test(`${path} observes one exact successful acknowledgement`,async({page})=>{
  const count=await fixture(page,path,{body:{status:result,physical_actuation:false}});
  expect((await observe(page,path,()=>page.locator('#act').click())).status).toBe(result);
  expect(count()).toBe(1);
 });
}

for(const revoked of [true,false,null])test(`shared logout requires confirmed central revocation (${revoked})`,async({page})=>{
 const path='/api/v1/auth/logout';
 const count=await fixture(page,path,{body:{status:'signed_out',physical_actuation:false,identity_signed_out:revoked}});
 const observed=observe(page,path,()=>page.locator('#act').click(),'onesa');
 if(revoked===true)expect((await observed).status).toBe('signed_out');
 else await expect(observed).rejects.toThrow('Shared sign-out must be confirmed');
 expect(count()).toBe(1);
});

for(const [label,options]of [['failed HTTP',{status:503}],['wrong state',{body:{status:'ready',physical_actuation:false}}],['physical activation',{body:{status:'stopped',physical_actuation:true}}],['incomplete body',{body:{status:'stopped'}}]]){
 test(`${label} cannot satisfy the acknowledgement`,async({page})=>{
  const count=await fixture(page,'/api/v1/stop',options);
  await expect(observe(page,'/api/v1/stop',()=>page.locator('#act').click())).rejects.toThrow();
  expect(count()).toBe(1);
 });
}

test('unknown operations are refused before a click',async({page})=>{
 let actions=0;
 await expect(observe(page,'/api/v1/missions',()=>{actions++;})).rejects.toThrow('Unsupported simulation acknowledgement');
 expect(actions).toBe(0);
});

test('sign-in diagnostics retain only fixed route, method and HTTP status',async({page})=>{
 const handlers=new Set(),fake={on(event,handler){expect(event).toBe('response');handlers.add(handler);},off(event,handler){expect(event).toBe('response');handlers.delete(handler);}};
 const stop=observeSignIn(fake,'https://synthetic.test');
 const response=(url,status=503)=>({url:()=>url,status:()=>status,request:()=>({method:()=> 'POST',postData(){throw Error('Credentials must not be read');}}),json(){throw Error('Private response must not be read');},headers(){throw Error('Cookies must not be read');}});
 for(const handler of handlers){
  handler(response('https://unrelated.test/api/v1/auth/login'));
  handler(response('https://synthetic.test/api/v1/missions'));
  handler(response('https://synthetic.test/api/v1/auth/login?private=synthetic-private-sentinel'));
 }
 const value=stop();expect(handlers.size).toBe(0);
 expect(value.responses).toEqual([{path:'/api/v1/auth/login',method:'POST',http_status:503}]);
 expect(JSON.stringify(value)).not.toContain('synthetic-private-sentinel');
 expect(value.state).toBe('SIGN_IN_HTTP_DIAGNOSTIC_NOT_QUALIFICATION');
});
