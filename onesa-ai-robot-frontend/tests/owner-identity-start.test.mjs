import assert from 'node:assert/strict';
import {mkdtemp,copyFile,symlink,rm} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {dirname,join} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';
import {test} from 'node:test';

test('identity observer retains the original body until the exact authorization navigation is validated',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'onesa-identity-start-'));
 try{
  const require=createRequire(import.meta.url);
  await symlink(dirname(dirname(dirname(require.resolve('@playwright/test/package.json')))),join(directory,'node_modules'),'dir');
  await copyFile(new URL('../../scripts/release/oracle_robot_identity_browser.mjs',import.meta.url),join(directory,'identity.mjs'));
  const {signInIdentity}=await import(pathToFileURL(join(directory,'identity.mjs')).href);
  const identity={profile:'shared-identity-v1',environment:'staging',origin:'https://onesa-ai-robot-git-staging-fixture.vercel.app',issuer:'https://onesa-company-portal-staging-fixture.vercel.app/api/v1/identity',client_id:'robot-synthetic-staging',portal_bypass:null,
   accounts:{administrator:{subject:'00000000-0000-4000-8000-000000000001',email:'synthetic@example.test',password:'synthetic-private-password',totp_secret:'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'}}}; // gitleaks:allow -- public RFC 4226 test vector.
  const authorization=identity.issuer+'/auth?'+new URLSearchParams({client_id:identity.client_id,redirect_uri:identity.origin+'/api/v1/auth/identity/callback',response_type:'code',scope:'openid email profile onesa',state:'a'.repeat(43),nonce:'b'.repeat(43),code_challenge:'c'.repeat(43),code_challenge_method:'S256'});
  for(const mutation of ['none','url','method','frame','body_timeout'])await t.test('navigation '+mutation,async scenario=>{
   if(mutation==='body_timeout')scenario.mock.timers.enable({apis:['setTimeout']});
   const events=[],frame={};let emit,navigated=false,bodyReads=0,matches,handler,removed=0,fallbacks=0,aborts=0,finishBody;
   const response={url:()=>identity.origin+'/api/v1/auth/identity/start',request:()=>({method:()=>'POST'}),status:()=>200,json:async()=>{
    bodyReads++;events.push('body-start');
    if(mutation==='body_timeout')await new Promise(resolve=>{finishBody=resolve;});
    else await new Promise(resolve=>setImmediate(resolve));
    assert.equal(navigated,false,'The originating response body was discarded during navigation');events.push('body-captured');return {authorization_url:authorization};
   }};
   const page={url:()=>identity.origin+'/console',mainFrame:()=>frame,
    waitForResponse(predicate,options){assert.equal(options.timeout,15000);return new Promise(resolve=>{emit=()=>{assert(predicate(response));resolve(response);};});},
    async route(pattern,callback,options){assert.deepEqual(options,{times:1});matches=pattern;handler=callback;events.push('gate-installed');},
    async unroute(pattern,callback){assert.equal(pattern,matches);assert.equal(callback,handler);removed++;},
    locator(selector){assert.equal(selector,'#onesa-sign-in-button');return {async click(){
     assert(emit);events.push('click');emit();await Promise.resolve();events.push('navigation-request');
     const destination=mutation==='url'?authorization.replace('state='+'a'.repeat(43),'state='+'d'.repeat(43)):authorization;
     assert(matches(new URL(destination)));assert(!matches(new URL(identity.origin+'/console')));
     const held=handler({request:()=>({url:()=>destination,method:()=>mutation==='method'?'POST':'GET',isNavigationRequest:()=>true,frame:()=>mutation==='frame'?{}:frame}),
      async fallback(){fallbacks++;navigated=true;events.push('navigation');},async abort(){aborts++;events.push('abort');}});
     if(mutation==='body_timeout'){await new Promise(resolve=>setImmediate(resolve));scenario.mock.timers.tick(15001);}
     await held;
    }};},
    waitForRequest(){return new Promise(()=>{});},
    async waitForURL(){events.push('portal-observation');throw new Error('Synthetic observer stops before Portal credentials');},
   };
   const context={async setExtraHTTPHeaders(value){assert.deepEqual(value,{});},async route(){}};
   await assert.rejects(signInIdentity(page,context,identity),{message:'ROBOT_IDENTITY_FLOW_FAILED'});
   assert.equal(bodyReads,1);assert.equal(removed,1);
   if(mutation==='body_timeout'){
    assert.equal(fallbacks,0);assert.equal(aborts,1);assert.deepEqual(events,['gate-installed','click','body-start','navigation-request','abort']);
    finishBody();await new Promise(resolve=>setImmediate(resolve));assert.equal(fallbacks,0);assert.equal(aborts,1);return;
   }
   assert.deepEqual(events.slice(0,5),['gate-installed','click','body-start','navigation-request','body-captured']);
   if(mutation==='none'){assert.equal(fallbacks,1);assert.equal(aborts,0);assert.deepEqual(events.slice(5),['navigation','portal-observation']);}
   else{assert.equal(fallbacks,0);assert.equal(aborts,1);assert.deepEqual(events.slice(5),['abort']);}
  });
 }finally{await rm(directory,{recursive:true,force:true});}
});
