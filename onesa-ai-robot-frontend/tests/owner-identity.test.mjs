import test from 'node:test';
import assert from 'node:assert/strict';
import {validateIdentity,validateAuthorization,validateCallback,totp,freshCode,scopeIdentityRequests} from '../../scripts/release/oracle_robot_identity_browser.mjs';

function fixture(){
 const account={subject:'00000000-0000-4000-8000-000000000001',email:'synthetic@example.test',password:'synthetic-private-password',totp_secret:'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'}; // gitleaks:allow -- public RFC 4226 test vector.
 return {profile:'shared-identity-v1',environment:'staging',origin:'https://onesa-ai-robot-git-staging-fixture.vercel.app',
  issuer:'https://onesa-company-portal-staging-fixture.vercel.app/api/v1/identity',client_id:'robot-synthetic-staging',
  accounts:{administrator:account,operator:{...account,subject:'00000000-0000-4000-8000-000000000002',email:'synthetic-operator@example.test'}},portal_bypass:'synthetic-portal-bypass'};
}
function authorization(identity,stepUp=false){
 return new URL(identity.issuer+'/auth?'+new URLSearchParams({client_id:identity.client_id,
  redirect_uri:identity.origin+'/api/v1/auth/identity/callback',response_type:'code',scope:'openid email profile onesa',
  state:'a'.repeat(43),nonce:'b'.repeat(43),code_challenge:'c'.repeat(43),code_challenge_method:'S256',
  ...(stepUp?{prompt:'login',max_age:'0',acr_values:'urn:onesa:mfa'}:{})}));
}

test('identity origins, known product, exact role and existing private credential shape are mandatory',()=>{
 const identity=fixture();assert.equal(validateIdentity(identity,'administrator').portal,new URL(identity.issuer).origin);
 assert.equal(validateIdentity(identity,'operator').account.email,'synthetic-operator@example.test');
 for(const mutation of [
  {origin:'https://foreign.example.test'}, {issuer:'https://foreign.example.test/api/v1/identity'},
  {issuer:identity.issuer+'?private=true'}, {client_id:'short'}, {profile:'legacy'},
  {environment:'production'}, {portal_bypass:'secret\nheader'},
 ])assert.throws(()=>validateIdentity({...identity,...mutation},'administrator'));
 for(const actor of ['viewer','owner','foreign'])assert.throws(()=>validateIdentity(identity,actor));
 const production={...identity,environment:'production',origin:'https://robot.onesa.eu',issuer:'https://portal.onesa.eu/api/v1/identity',portal_bypass:null};
 validateIdentity(production,'administrator');
 assert.throws(()=>validateIdentity({...production,portal_bypass:'synthetic-production-secret'},'administrator'));
});

test('authorization observes exact PKCE code flow and explicit MFA step-up',()=>{
 const identity=fixture();validateAuthorization(authorization(identity),identity);
 validateAuthorization(authorization(identity,true),identity,true);
 for(const [key,value] of [['client_id','foreign'],['redirect_uri','https://foreign.example.test/callback'],
  ['response_type','token'],['code_challenge_method','plain'],['state','short'],['nonce','short'],['code_challenge','short'],['scope','openid']]){
  const wrong=authorization(identity);wrong.searchParams.set(key,value);
  assert.throws(()=>validateAuthorization(wrong,identity));
 }
 const duplicate=authorization(identity);duplicate.searchParams.append('state','a'.repeat(43));
 assert.throws(()=>validateAuthorization(duplicate,identity));
 const unknown=authorization(identity);unknown.searchParams.set('unreviewed','true');
 assert.throws(()=>validateAuthorization(unknown,identity));
 assert.throws(()=>validateAuthorization(authorization(identity),identity,true));
 const silent=authorization(identity,true);silent.searchParams.set('prompt','none');
 assert.throws(()=>validateAuthorization(silent,identity,true));
});

test('TOTP uses the standard counter and waits once for an unused next period',async()=>{
 const secret=fixture().accounts.administrator.totp_secret;
 assert.equal(totp(secret,1),'287082');
 let instant=59000;const waits=[];
 const value=await freshCode(secret,{now:()=>instant,wait:async amount=>{waits.push(amount);instant+=amount;}});
 assert.deepEqual(waits,[2000]);assert.equal(value,totp(secret,2));
 await assert.rejects(freshCode(secret,{now:()=>59000,wait:async()=>{}}));
 assert.throws(()=>totp(secret,-1));assert.throws(()=>totp(secret.toLowerCase(),1));
});

test('callback state and issuer are bound to the observed code transaction',()=>{
 const identity=fixture(),start=authorization(identity);
 const callback=new URL(identity.origin+'/api/v1/auth/identity/callback?'+new URLSearchParams({code:'synthetic-authorization-code',state:start.searchParams.get('state'),iss:identity.issuer}));
 assert.equal(validateCallback(callback,identity,start),true);
 for(const [key,value] of [['state','wrong'],['iss','https://foreign.example.test/api/v1/identity'],['code','']]){
  const wrong=new URL(callback);wrong.searchParams.set(key,value);assert.throws(()=>validateCallback(wrong,identity,start));
 }
 const duplicate=new URL(callback);duplicate.searchParams.append('state','a'.repeat(43));assert.throws(()=>validateCallback(duplicate,identity,start));
 const failed=new URL(callback);failed.searchParams.set('error','access_denied');assert.throws(()=>validateCallback(failed,identity,start));
});

function browserFixture({foreignRedirect=false,wrongCookie=false}={}){
 const calls=[],cookies=[],closed=[];let route;
 const browser={async newContext(){
  let origin;
  return {request:{async get(selected,options){origin=selected;calls.push({origin,options});return {status:()=>302,headers:()=>({location:foreignRedirect?'https://foreign.example.test/':origin+'/console'})};}},
   async cookies(){return [{name:'_vercel_jwt',value:'private-cookie-for-'+origin,secure:true,domain:wrongCookie?'foreign.example.test':new URL(origin).hostname}];},
   async close(){closed.push(origin);}};
 }};
 const context={browser:()=>browser,async setExtraHTTPHeaders(headers){assert.deepEqual(headers,{});},
  async addCookies(values){cookies.push(...values);},async route(_pattern,callback){route=callback;}};
 return {context,calls,cookies,closed,async request(url,{method='GET',headers={}}={}){
  const outcomes=[];await route({request:()=>({url:()=>url,method:()=>method,headers:()=>headers}),
   async continue(overrides){outcomes.push({continue:true,overrides});},async abort(){outcomes.push({abort:true});}});return outcomes;
 }};
}

test('preview credentials are used only in isolated no-redirect handshakes and rebound host-only cookies',async()=>{
 const identity=fixture(),browser=browserFixture();
 await scopeIdentityRequests(browser.context,identity,'synthetic-robot-bypass');
 assert.equal(browser.calls.length,2);assert.equal(browser.closed.length,2);
 assert.deepEqual(browser.calls.map(call=>call.origin),[identity.origin,new URL(identity.issuer).origin]);
 for(const call of browser.calls){assert.equal(call.options.maxRedirects,0);assert.equal(call.options.headers['x-vercel-set-bypass-cookie'],'true');}
 assert.equal(browser.calls[0].options.headers['x-vercel-protection-bypass'],'synthetic-robot-bypass');
 assert.equal(browser.calls[1].options.headers['x-vercel-protection-bypass'],'synthetic-portal-bypass');
 for(const cookie of browser.cookies){assert.equal(Object.hasOwn(cookie,'domain'),false);assert.equal(cookie.secure,true);assert.equal(cookie.httpOnly,true);}
 assert.deepEqual(await browser.request('https://third-party.example.test/asset'),[{continue:true,overrides:undefined}]);
 assert.deepEqual(await browser.request(identity.origin+'/api/v1/auth/identity/start',{method:'POST'}),[{continue:true,overrides:undefined}]);
 assert.deepEqual(await browser.request(new URL(identity.issuer).origin+'/api/v1/account/interaction/abcdefgh/login',{method:'POST'}),[{continue:true,overrides:undefined}]);
 assert.deepEqual(await browser.request('https://third-party.example.test/submit',{method:'POST'}),[{abort:true}]);
 assert.deepEqual(await browser.request(new URL(identity.issuer).origin+'/api/v1/account/password/request',{method:'POST'}),[{abort:true}]);
 assert.deepEqual(await browser.request(identity.origin+'/console',{headers:{'x-vercel-protection-bypass':'unexpected'}}),[{abort:true}]);
 await scopeIdentityRequests(browser.context,identity,'synthetic-robot-bypass');assert.equal(browser.calls.length,2);
});

test('foreign redirect or cookie scope never imports preview credentials into a browser context',async()=>{
 for(const options of [{foreignRedirect:true},{wrongCookie:true}]){
  const browser=browserFixture(options);
  await assert.rejects(scopeIdentityRequests(browser.context,fixture(),'synthetic-robot-bypass'));
  assert.deepEqual(browser.cookies,[]);assert.equal(browser.closed.length,1);
 }
});
