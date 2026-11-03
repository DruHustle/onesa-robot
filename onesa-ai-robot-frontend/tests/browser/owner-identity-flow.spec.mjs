import {test as base,expect} from '@playwright/test';
import {mkdtemp,copyFile,symlink,rm} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {dirname,join} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';
import {identityTransport,robotOrigin,portalOrigin} from './owner-identity-transport.mjs';
import {withIdentityDiagnostics} from './owner-identity-diagnostic.mjs';

const test=base.extend({
 identityTransport:async({},use)=>{const transport=await identityTransport();try{await use(transport);transport.assertHealthy();}finally{await transport.close();}},
 context:async({browser,contextOptions,identityTransport},use)=>{
  // Only this synthetic context trusts its ephemeral certificate. The bounded
  // proxy sends both production-shaped fixture origins exclusively to loopback.
  const context=await browser.newContext({...contextOptions,proxy:{server:identityTransport.proxy},ignoreHTTPSErrors:true});
  try{await use(context);}finally{await context.close();}
 },
});

let directory,signInIdentity;
test.beforeAll(async()=>{
 directory=await mkdtemp(join(tmpdir(),'onesa-owner-identity-'));
 const require=createRequire(import.meta.url);
 await symlink(dirname(dirname(dirname(require.resolve('@playwright/test/package.json')))),join(directory,'node_modules'),'dir');
 await copyFile(new URL('../../../scripts/release/oracle_robot_identity_browser.mjs',import.meta.url),join(directory,'identity.mjs'));
 ({signInIdentity}=await import(pathToFileURL(join(directory,'identity.mjs')).href));
});
test.afterAll(async()=>{if(directory)await rm(directory,{recursive:true});});
const observedSignIn=(page,...args)=>withIdentityDiagnostics(page,observed=>signInIdentity(observed,...args));

async function fixture(page,context,transport,{initialFactor=false,wrongSubject=false,wrongState=false,expired=false,apiOnly=false}={}){
 const origin=robotOrigin,portal=portalOrigin;
 const identity={profile:'shared-identity-v1',environment:'staging',origin,issuer:portal+'/api/v1/identity',client_id:'robot-synthetic-staging',portal_bypass:null,
  accounts:{administrator:{subject:'00000000-0000-4000-8000-000000000001',email:'synthetic@example.test',password:'synthetic-private-password',totp_secret:'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'}}}; // gitleaks:allow -- public RFC 4226 test vector.
 const state='a'.repeat(43),cookie='synthetic-session-'+ 'x'.repeat(32),csrf='synthetic-csrf-'+ 'x'.repeat(32);
 const authorization=identity.issuer+'/auth?'+new URLSearchParams({client_id:identity.client_id,redirect_uri:origin+'/api/v1/auth/identity/callback',response_type:'code',scope:'openid email profile onesa',state,nonce:'b'.repeat(43),code_challenge:'c'.repeat(43),code_challenge_method:'S256'});
 const paths=[],submissions=[];let signedIn=false;
 const html=body=>({headers:{'content-type':'text/html'},body});
 const json=(body,status=200,headers={})=>({status,headers:{'content-type':'application/json',...headers},body:JSON.stringify(body)});
 transport.setHandler(async request=>{
  const {url}=request;paths.push(url.origin+url.pathname);
  if(request.method==='GET'&&url.pathname==='/favicon.ico')return {status:204};
  if(url.origin===origin&&url.pathname==='/console')return html(signedIn?'<div id="operator">Synthetic authenticated simulator</div>':apiOnly?'<form id="login-form">Retained legacy sign-in UI</form>':
   '<button id="onesa-sign-in-button">Continue with Onesa</button><script>document.querySelector("button").onclick=async()=>{const r=await fetch("/api/v1/auth/identity/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({step_up:false})});location.assign((await r.json()).authorization_url);};</script>');
  if(url.origin===origin&&url.pathname==='/api/v1/auth/identity/start'){
   expect(request.method).toBe('POST');expect(request.headers.origin).toBe(origin);expect(JSON.parse(request.body)).toEqual({step_up:false});return json({authorization_url:authorization});
  }
  if(url.origin===portal&&url.pathname==='/api/v1/identity/auth')return {status:302,headers:{location:portal+'/account/sign-in/synthetic12345678'}};
  if(url.origin===portal&&url.pathname==='/account/sign-in/synthetic12345678')return html(
   '<form><input name="email"><input name="password" type="password">'+(initialFactor?'<input name="code">':'')+'<button>Continue</button></form><script>document.querySelector("form").onsubmit=async event=>{event.preventDefault();const body=Object.fromEntries(new FormData(event.currentTarget));const r=await fetch("/api/v1/account/interaction/synthetic12345678/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const result=await r.json();if(result.mfa){const field=document.createElement("input");field.name="code";document.querySelector("form").prepend(field);}else location.assign(result.continuePath);};</script>');
  if(url.origin===portal&&url.pathname==='/api/v1/account/interaction/synthetic12345678/login'){
   expect(request.method).toBe('POST');expect(request.headers.origin).toBe(portal);const body=JSON.parse(request.body);submissions.push(body);
   expect(body.email).toBe(identity.accounts.administrator.email);expect(body.password).toBe(identity.accounts.administrator.password);
   if(!body.code)return json({mfa:true},401);
   expect(body.code).toBe('123456');
   return json({continuePath:'/api/v1/identity/auth/synthetic12345678'},200,{'set-cookie':'portal_fixture=authenticated; Path=/api/v1; HttpOnly; Secure; SameSite=Lax'});
  }
  if(url.origin===portal&&url.pathname==='/api/v1/identity/auth/synthetic12345678')return {status:302,headers:{location:origin+'/api/v1/auth/identity/callback?'+new URLSearchParams({code:'synthetic-authorization-code',state:wrongState?'wrong':state,iss:identity.issuer})}};
  if(url.origin===origin&&url.pathname==='/api/v1/auth/identity/callback'){
   signedIn=true;
   return {status:302,headers:{location:origin+'/console','set-cookie':`robot_session=${cookie}; Max-Age=900; Path=/api/v1; HttpOnly; Secure; SameSite=Strict`}};
  }
  if(url.origin===portal&&url.pathname==='/api/v1/account/me'){
   expect(request.headers.cookie).toContain('portal_fixture=authenticated');return json({account:{id:wrongSubject?'00000000-0000-4000-8000-000000000099':identity.accounts.administrator.subject,email:identity.accounts.administrator.email,emailVerified:true,mfaEnrolled:true,globalAdmin:true}});
  }
  if(url.origin===origin&&url.pathname==='/api/v1/auth/me'){
   expect(request.headers.cookie).toContain('robot_session='+cookie);return json({authentication_method:'onesa',role:'administrator',csrf,confirmation_expires_at:expired?0:Date.now()/1000+240});
  }
  throw Error('Unexpected synthetic route');
 });
 await page.goto(origin+'/console');
 return {identity,wrapper:context,paths,submissions,cookie};
}

for(const initialFactor of [false,true])test(`owner sign-in uses real browser PKCE navigation and host-only API cookie (initial factor ${initialFactor})`,async({page,context,identityTransport})=>{
 const value=await fixture(page,context,identityTransport,{initialFactor});
 const session=await observedSignIn(page,value.wrapper,value.identity,{code:async()=>'123456'});
 expect(session.subject).toBe(value.identity.accounts.administrator.subject);expect(session.cookie.value).toBe(value.cookie);
 expect(session.cookie.path).toBe('/api/v1');expect(session.cookie.secure).toBe(true);expect(session.cookie.httpOnly).toBe(true);
 expect(value.submissions).toHaveLength(initialFactor?1:2);
 expect(value.paths.filter(path=>path.endsWith('/api/v1/auth/identity/start'))).toHaveLength(1);
 expect(value.paths.every(path=>path.startsWith(value.identity.origin)||path.startsWith(new URL(value.identity.issuer).origin))).toBe(true);
});

test('API qualification uses real PKCE and Portal forms before the product sign-in UI is deployed',async({page,context,identityTransport})=>{
 const value=await fixture(page,context,identityTransport,{initialFactor:true,apiOnly:true});
 await expect(page.locator('#login-form')).toBeVisible();await expect(page.locator('#onesa-sign-in-button')).toHaveCount(0);
 const session=await observedSignIn(page,value.wrapper,value.identity,{code:async()=>'123456',apiOnly:true});
 expect(session.subject).toBe(value.identity.accounts.administrator.subject);expect(value.submissions).toHaveLength(1);
 expect(value.paths.filter(path=>path.endsWith('/api/v1/auth/identity/start'))).toHaveLength(1);
});

for(const options of [{wrongSubject:true},{wrongState:true},{expired:true}])test(`owner sign-in rejects mismatched live evidence ${Object.keys(options)[0]}`,async({page,context,identityTransport})=>{
 const value=await fixture(page,context,identityTransport,{initialFactor:true,...options});
 await expect(observedSignIn(page,value.wrapper,value.identity,{code:async()=>'123456'})).rejects.toThrow('ROBOT_IDENTITY_FLOW_FAILED');
 expect(value.submissions).toHaveLength(1);
});
