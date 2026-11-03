import assert from 'node:assert/strict';
import {mkdtemp,copyFile,symlink,rm} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {dirname,join} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';
import {test} from 'node:test';

test('identity observer captures the start body before the UI click finishes navigation',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'onesa-identity-start-'));
 try{
  const require=createRequire(import.meta.url);
  await symlink(dirname(dirname(dirname(require.resolve('@playwright/test/package.json')))),join(directory,'node_modules'),'dir');
  await copyFile(new URL('../../scripts/release/oracle_robot_identity_browser.mjs',import.meta.url),join(directory,'identity.mjs'));
  const {signInIdentity}=await import(pathToFileURL(join(directory,'identity.mjs')).href);
  const identity={profile:'shared-identity-v1',environment:'staging',origin:'https://onesa-ai-robot-git-staging-fixture.vercel.app',issuer:'https://onesa-company-portal-staging-fixture.vercel.app/api/v1/identity',client_id:'robot-synthetic-staging',portal_bypass:null,
   accounts:{administrator:{subject:'00000000-0000-4000-8000-000000000001',email:'synthetic@example.test',password:'synthetic-private-password',totp_secret:'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'}}}; // gitleaks:allow -- public RFC 4226 test vector.
  const authorization=identity.issuer+'/auth?'+new URLSearchParams({client_id:identity.client_id,redirect_uri:identity.origin+'/api/v1/auth/identity/callback',response_type:'code',scope:'openid email profile onesa',state:'a'.repeat(43),nonce:'b'.repeat(43),code_challenge:'c'.repeat(43),code_challenge_method:'S256'});
  const events=[];let emit,navigated=false,bodyReads=0;
  const response={url:()=>identity.origin+'/api/v1/auth/identity/start',request:()=>({method:()=>'POST'}),status:()=>200,json:async()=>{
   bodyReads++;assert.equal(navigated,false,'The originating response body is no longer available after navigation');events.push('body');return {authorization_url:authorization};
  }};
  const page={url:()=>identity.origin+'/console',
   waitForResponse(predicate,options){assert.equal(options.timeout,15000);return new Promise(resolve=>{emit=()=>{assert(predicate(response));resolve(response);};});},
   locator(selector){assert.equal(selector,'#onesa-sign-in-button');return {async click(){
    assert(emit);events.push('click');emit();
    // The UI consumes its own JSON and completes navigation before click returns.
    await new Promise(resolve=>setImmediate(resolve));navigated=true;events.push('navigation');
   }};},
   waitForRequest(){return new Promise(()=>{});},
   async waitForURL(){events.push('portal-observation');throw new Error('Synthetic observer stops before Portal credentials');},
  };
  const context={async setExtraHTTPHeaders(value){assert.deepEqual(value,{});},async route(){}};
  await assert.rejects(signInIdentity(page,context,identity),{message:'ROBOT_IDENTITY_FLOW_FAILED'});
  assert.equal(bodyReads,1);assert.deepEqual(events,['click','body','navigation','portal-observation']);
 }finally{await rm(directory,{recursive:true,force:true});}
});
