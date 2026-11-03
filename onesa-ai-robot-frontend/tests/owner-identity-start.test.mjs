import assert from 'node:assert/strict';
import {mkdtemp,copyFile,symlink,rm} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {dirname,join} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';
import {test} from 'node:test';

async function withHelper(action){
 const directory=await mkdtemp(join(tmpdir(),'onesa-identity-start-'));
 try{
  const require=createRequire(import.meta.url);
  await symlink(dirname(dirname(dirname(require.resolve('@playwright/test/package.json')))),join(directory,'node_modules'),'dir');
  await copyFile(new URL('../../scripts/release/oracle_robot_identity_browser.mjs',import.meta.url),join(directory,'identity.mjs'));
  const {signInIdentity}=await import(pathToFileURL(join(directory,'identity.mjs')).href);
  const identity={profile:'shared-identity-v1',environment:'staging',origin:'https://onesa-ai-robot-git-staging-fixture.vercel.app',issuer:'https://onesa-company-portal-staging-fixture.vercel.app/api/v1/identity',client_id:'robot-synthetic-staging',portal_bypass:null,
   accounts:{administrator:{subject:'00000000-0000-4000-8000-000000000001',email:'synthetic@example.test',password:'synthetic-private-password',totp_secret:'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'}}}; // gitleaks:allow -- public RFC 4226 test vector.
  const authorization=identity.issuer+'/auth?'+new URLSearchParams({client_id:identity.client_id,redirect_uri:identity.origin+'/api/v1/auth/identity/callback',response_type:'code',scope:'openid email profile onesa',state:'a'.repeat(43),nonce:'b'.repeat(43),code_challenge:'c'.repeat(43),code_challenge_method:'S256'});
  await action({signInIdentity,identity,authorization});
 }finally{await rm(directory,{recursive:true,force:true});}
}

const context=()=>({async setExtraHTTPHeaders(value){assert.deepEqual(value,{});},async route(){}});

test('UI identity validates the actual browser transaction when its response body is unavailable',async t=>withHelper(async({signInIdentity,identity,authorization})=>{
 for(const mutation of ['none','start_status','start_frame','client','method','frame','non_navigation','foreign_origin'])await t.test(mutation,async()=>{
  const frame={},observers={};let bodyReads=0,portalObservations=0,beforeClick;
  const response={url:()=>identity.origin+'/api/v1/auth/identity/start',request:()=>({method:()=>'POST',frame:()=>mutation==='start_frame'?{}:frame}),status:()=>mutation==='start_status'?503:200,
   async json(){bodyReads++;throw new Error('Response body was discarded during document replacement');}};
  const page={url:()=>identity.origin+'/console',mainFrame:()=>frame,
   async route(){assert.fail('The protocol observer must not install another request route');},
   waitForResponse(predicate,options){assert.equal(options.timeout,15000);return new Promise((resolve,reject)=>{observers.start={predicate,resolve,reject};});},
   waitForRequest(predicate,options){const key=options.timeout===90000?'callback':'authorization';assert.equal(options.timeout,key==='callback'?90000:20000);return new Promise((resolve,reject)=>{observers[key]={predicate,resolve,reject};});},
   locator(selector){assert.equal(selector,'#onesa-sign-in-button');return {async click(){
    beforeClick={start:!!observers.start,authorization:!!observers.authorization,callback:!!observers.callback};assert(observers.start);
    if(observers.start.predicate(response))observers.start.resolve(response);else observers.start.reject(new Error('Synthetic start observer timeout'));
    const destination=new URL(authorization);
    if(mutation==='client')destination.searchParams.set('client_id','foreign-client');
    if(mutation==='foreign_origin')destination.hostname='foreign.example.test';
    const request={url:()=>destination.href,method:()=>mutation==='method'?'POST':'GET',frame:()=>mutation==='frame'?{}:frame,isNavigationRequest:()=>mutation!=='non_navigation'};
    if(observers.authorization){if(observers.authorization.predicate(request))observers.authorization.resolve(request);else observers.authorization.reject(new Error('Synthetic authorization observer timeout'));}
   }};},
   async waitForURL(){portalObservations++;throw new Error('Synthetic observer stops before Portal credentials');},
  };
  await assert.rejects(signInIdentity(page,context(),identity),{message:'ROBOT_IDENTITY_FLOW_FAILED'});
  assert.equal(bodyReads,0);assert.deepEqual(beforeClick,{start:true,authorization:true,callback:true});assert.equal(portalObservations,mutation==='none'?1:0);
 });
}));

test('API-only retains original JSON before navigation and bounds delayed body completion',async t=>withHelper(async({signInIdentity,identity,authorization})=>{
 for(const timeout of [false,true])await t.test('body timeout '+timeout,async scenario=>{
  if(timeout)scenario.mock.timers.enable({apis:['setTimeout']});
  const frame={},events=[];let emit,finishBody,bodyReads=0;
  const response={url:()=>identity.origin+'/api/v1/auth/identity/start',request:()=>({method:()=>'POST',frame:()=>frame}),status:()=>200,async json(){
   bodyReads++;events.push('body');if(timeout)await new Promise(resolve=>{finishBody=resolve;});return {authorization_url:authorization};
  }};
  const page={url:()=>identity.origin+'/console',mainFrame:()=>frame,
   async route(){assert.fail('API-only must not install another request route');},
   waitForResponse(predicate,options){assert.equal(options.timeout,15000);return new Promise(resolve=>{emit=()=>{assert(predicate(response));resolve(response);};});},
   waitForRequest(_predicate,options){assert.equal(options.timeout,90000);return new Promise(()=>{});},
   async evaluate(){events.push('start');emit();if(timeout){await new Promise(resolve=>setImmediate(resolve));scenario.mock.timers.tick(15001);}},
   async goto(url){assert.equal(url,authorization);events.push('navigation');},
   async waitForURL(){events.push('portal-observation');throw new Error('Synthetic observer stops before Portal credentials');},
  };
  await assert.rejects(signInIdentity(page,context(),identity,{apiOnly:true}),{message:'ROBOT_IDENTITY_FLOW_FAILED'});
  assert.equal(bodyReads,1);
  assert.deepEqual(events,timeout?['start','body']:['start','body','navigation','portal-observation']);
  if(timeout){finishBody();await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(events,['start','body']);}
 });
}));
