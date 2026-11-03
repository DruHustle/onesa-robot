import {test,expect} from '@playwright/test';
import {mkdtemp,copyFile,symlink,rm} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {dirname,join} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';

let directory,signInIdentity;
test.beforeAll(async()=>{
 directory=await mkdtemp(join(tmpdir(),'onesa-owner-identity-'));
 const require=createRequire(import.meta.url);
 await symlink(dirname(dirname(dirname(require.resolve('@playwright/test/package.json')))),join(directory,'node_modules'),'dir');
 await copyFile(new URL('../../../scripts/release/oracle_robot_identity_browser.mjs',import.meta.url),join(directory,'identity.mjs'));
 ({signInIdentity}=await import(pathToFileURL(join(directory,'identity.mjs')).href));
});
test.afterAll(async()=>{if(directory)await rm(directory,{recursive:true});});

async function fixture(page,context,{initialFactor=false,wrongSubject=false,wrongState=false,expired=false,apiOnly=false}={}){
 const origin='https://onesa-ai-robot-git-staging-fixture.vercel.app',portal='https://onesa-company-portal-staging-fixture.vercel.app';
 const identity={profile:'shared-identity-v1',environment:'staging',origin,issuer:portal+'/api/v1/identity',client_id:'robot-synthetic-staging',portal_bypass:null,
  accounts:{administrator:{subject:'00000000-0000-4000-8000-000000000001',email:'synthetic@example.test',password:'synthetic-private-password',totp_secret:'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'}}}; // gitleaks:allow -- public RFC 4226 test vector.
 const state='a'.repeat(43),cookie='synthetic-session-'+ 'x'.repeat(32),csrf='synthetic-csrf-'+ 'x'.repeat(32);
 const authorization=identity.issuer+'/auth?'+new URLSearchParams({client_id:identity.client_id,redirect_uri:origin+'/api/v1/auth/identity/callback',response_type:'code',scope:'openid email profile onesa',state,nonce:'b'.repeat(43),code_challenge:'c'.repeat(43),code_challenge_method:'S256'});
 const paths=[],submissions=[];let signedIn=false;
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());paths.push(url.origin+url.pathname);
  if(url.origin===origin&&url.pathname==='/console')return route.fulfill({contentType:'text/html',body:signedIn?'<div id="operator">Synthetic authenticated simulator</div>':apiOnly?'<form id="login-form">Retained legacy sign-in UI</form>':
   '<button id="onesa-sign-in-button">Continue with Onesa</button><script>document.querySelector("button").onclick=async()=>{const r=await fetch("/api/v1/auth/identity/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({step_up:false})});location.assign((await r.json()).authorization_url);};</script>'});
  if(url.origin===origin&&url.pathname==='/api/v1/auth/identity/start')return route.fulfill({contentType:'application/json',body:JSON.stringify({authorization_url:authorization})});
  if(url.origin===portal&&url.pathname==='/api/v1/identity/auth')return route.fulfill({status:302,headers:{location:portal+'/account/sign-in/synthetic12345678'}});
  if(url.origin===portal&&url.pathname==='/account/sign-in/synthetic12345678')return route.fulfill({contentType:'text/html',body:
   '<form><input name="email"><input name="password" type="password">'+(initialFactor?'<input name="code">':'')+'<button>Continue</button></form><script>document.querySelector("form").onsubmit=async event=>{event.preventDefault();const body=Object.fromEntries(new FormData(event.currentTarget));const r=await fetch("/api/v1/account/interaction/synthetic12345678/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const result=await r.json();if(result.mfa){const field=document.createElement("input");field.name="code";document.querySelector("form").prepend(field);}else location.assign(result.continuePath);};</script>'});
  if(url.origin===portal&&url.pathname==='/api/v1/account/interaction/synthetic12345678/login'){
   const body=request.postDataJSON();submissions.push(body);
   expect(body.email).toBe(identity.accounts.administrator.email);expect(body.password).toBe(identity.accounts.administrator.password);
   if(!body.code)return route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({mfa:true})});
   expect(body.code).toBe('123456');
   return route.fulfill({contentType:'application/json',body:JSON.stringify({continuePath:'/api/v1/identity/auth/synthetic12345678'})});
  }
  if(url.origin===portal&&url.pathname==='/api/v1/identity/auth/synthetic12345678')return route.fulfill({status:302,headers:{location:origin+'/api/v1/auth/identity/callback?'+new URLSearchParams({code:'synthetic-authorization-code',state:wrongState?'wrong':state,iss:identity.issuer})}});
  if(url.origin===origin&&url.pathname==='/api/v1/auth/identity/callback'){
   signedIn=true;
   return route.fulfill({status:302,headers:{location:origin+'/console','set-cookie':`robot_session=${cookie}; Max-Age=900; Path=/api/v1; HttpOnly; Secure; SameSite=Strict`}});
  }
  throw Error('Unexpected synthetic route');
 });
 const wrapper={browser:()=>context.browser(),setExtraHTTPHeaders:headers=>context.setExtraHTTPHeaders(headers),
  route:(pattern,callback)=>context.route(pattern,callback),cookies:url=>context.cookies(url),
  request:{async post(url,options){
   expect(apiOnly).toBe(true);expect(url).toBe(origin+'/api/v1/auth/identity/start');expect(options.maxRedirects).toBe(0);
   expect(options.headers).toEqual({Origin:origin});expect(options.data).toEqual({step_up:false});paths.push(url);
   return {status:()=>200,json:async()=>({authorization_url:authorization})};
  },async get(url,options){expect(options.maxRedirects).toBe(0);let body;
   if(url===portal+'/api/v1/account/me')body={account:{id:wrongSubject?'00000000-0000-4000-8000-000000000099':identity.accounts.administrator.subject,email:identity.accounts.administrator.email,emailVerified:true,mfaEnrolled:true,globalAdmin:true}};
   else if(url===origin+'/api/v1/auth/me')body={authentication_method:'onesa',role:'administrator',csrf,confirmation_expires_at:expired?0:Date.now()/1000+240};
   else throw Error('Unexpected synthetic observation');
   return {status:()=>200,body:async()=>Buffer.from(JSON.stringify(body))};}}};
 await page.goto(origin+'/console');
 return {identity,wrapper,paths,submissions,cookie};
}

for(const initialFactor of [false,true])test(`owner sign-in uses real browser PKCE navigation and host-only API cookie (initial factor ${initialFactor})`,async({page,context})=>{
 const value=await fixture(page,context,{initialFactor});
 const session=await signInIdentity(page,value.wrapper,value.identity,{code:async()=>'123456'});
 expect(session.subject).toBe(value.identity.accounts.administrator.subject);expect(session.cookie.value).toBe(value.cookie);
 expect(session.cookie.path).toBe('/api/v1');expect(session.cookie.secure).toBe(true);expect(session.cookie.httpOnly).toBe(true);
 expect(value.submissions).toHaveLength(initialFactor?1:2);
 expect(value.paths.filter(path=>path.endsWith('/api/v1/auth/identity/start'))).toHaveLength(1);
 expect(value.paths.every(path=>path.startsWith(value.identity.origin)||path.startsWith(new URL(value.identity.issuer).origin))).toBe(true);
});

test('API qualification uses real PKCE and Portal forms before the product sign-in UI is deployed',async({page,context})=>{
 const value=await fixture(page,context,{initialFactor:true,apiOnly:true});
 await expect(page.locator('#login-form')).toBeVisible();await expect(page.locator('#onesa-sign-in-button')).toHaveCount(0);
 const session=await signInIdentity(page,value.wrapper,value.identity,{code:async()=>'123456',apiOnly:true});
 expect(session.subject).toBe(value.identity.accounts.administrator.subject);expect(value.submissions).toHaveLength(1);
 expect(value.paths.filter(path=>path.endsWith('/api/v1/auth/identity/start'))).toHaveLength(1);
});

for(const options of [{wrongSubject:true},{wrongState:true},{expired:true}])test(`owner sign-in rejects mismatched live evidence ${Object.keys(options)[0]}`,async({page,context})=>{
 const value=await fixture(page,context,{initialFactor:true,...options});
 await expect(signInIdentity(page,value.wrapper,value.identity,{code:async()=>'123456'})).rejects.toThrow('ROBOT_IDENTITY_FLOW_FAILED');
 expect(value.submissions).toHaveLength(1);
});
