// Source-pinned Robot qualification only. Credentials and the resulting cookie
// travel in private process pipes; no screenshots, traces or account changes.
import assert from 'node:assert/strict';
import {createHmac} from 'node:crypto';
import fs from 'node:fs';

const configuredContexts=new WeakSet();
const requireValue=(value)=>assert.ok(value,'ROBOT_IDENTITY_BOUNDARY_REFUSED');
export function validateIdentity(value,actor){
 requireValue(value?.profile==='shared-identity-v1'&&['staging','production'].includes(value.environment));
 requireValue(['administrator','operator'].includes(actor));
 const robot=new URL(value.origin),issuer=new URL(value.issuer),account=value.accounts?.[actor];
 requireValue(robot.origin===value.origin&&robot.protocol==='https:'&&!robot.port);
 requireValue(value.environment==='production'?value.origin==='https://robot.onesa.eu':/^onesa-ai-robot-git-staging-[a-z0-9-]+\.vercel\.app$/.test(robot.hostname));
 requireValue(issuer.protocol==='https:'&&!issuer.username&&!issuer.password&&!issuer.port&&!issuer.search&&!issuer.hash&&issuer.pathname==='/api/v1/identity');
 requireValue(value.environment==='production'?issuer.origin==='https://portal.onesa.eu':/^onesa-company-portal-[a-z0-9-]+\.vercel\.app$/.test(issuer.hostname));
 requireValue(/^[A-Za-z0-9_-]{16,128}$/.test(value.client_id));
 requireValue(account&&/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(account.subject));
 requireValue(typeof account.email==='string'&&/^[^\s@]{1,64}@[^\s@]{1,189}$/.test(account.email)&&account.email===account.email.toLowerCase());
 requireValue(typeof account.password==='string'&&account.password.length>=1&&account.password.length<=256&&/^[A-Z2-7]{16,128}$/.test(account.totp_secret));
 requireValue(value.portal_bypass===null||value.environment==='staging'&&typeof value.portal_bypass==='string'&&/^[!-~]{1,8192}$/.test(value.portal_bypass));
 return {robot:robot.origin,portal:issuer.origin,account};
}

export function validateAuthorization(raw,identity,stepUp=false){
 const target=new URL(raw),expected=new URL(identity.issuer);
 requireValue(target.origin===expected.origin&&target.pathname===expected.pathname+'/auth'&&!target.hash&&!target.username&&!target.password);
 const fields=['client_id','redirect_uri','response_type','scope','state','nonce','code_challenge','code_challenge_method'];
 if(stepUp)fields.push('prompt','max_age','acr_values');
 requireValue([...target.searchParams.keys()].length===fields.length&&fields.every(key=>target.searchParams.getAll(key).length===1));
 requireValue(target.searchParams.get('client_id')===identity.client_id&&target.searchParams.get('redirect_uri')===identity.origin+'/api/v1/auth/identity/callback');
 requireValue(target.searchParams.get('response_type')==='code'&&target.searchParams.get('scope')==='openid email profile onesa'&&target.searchParams.get('code_challenge_method')==='S256');
 requireValue(['state','nonce','code_challenge'].every(key=>/^[A-Za-z0-9_-]{40,128}$/.test(target.searchParams.get(key))));
 if(stepUp)requireValue(target.searchParams.get('prompt')==='login'&&target.searchParams.get('max_age')==='0'&&target.searchParams.get('acr_values')==='urn:onesa:mfa');
 return target;
}

export function validateCallback(raw,identity,authorization){
 const target=new URL(raw),keys=[...target.searchParams.keys()];
 requireValue(target.origin===identity.origin&&target.pathname==='/api/v1/auth/identity/callback'&&!target.hash&&!target.username&&!target.password);
 requireValue(keys.every(key=>['code','state','iss'].includes(key))&&new Set(keys).size===keys.length);
 requireValue(target.searchParams.get('state')===authorization.searchParams.get('state')&&target.searchParams.getAll('code').length===1&&target.searchParams.get('code').length>0&&target.searchParams.get('code').length<=4096);
 requireValue(!target.searchParams.has('iss')||target.searchParams.get('iss')===identity.issuer);
 return true;
}

export function totp(secret,step){
 requireValue(/^[A-Z2-7]{16,128}$/.test(secret)&&Number.isSafeInteger(step)&&step>=0);
 const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';let bits='';
 for(const char of secret)bits+=alphabet.indexOf(char).toString(2).padStart(5,'0');
 const bytes=[];for(let at=0;at+8<=bits.length;at+=8)bytes.push(parseInt(bits.slice(at,at+8),2));
 const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(step));
 const digest=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=digest[digest.length-1]&15;
 return String((digest.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}

export async function freshCode(secret,{now=Date.now,wait=milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds))}={}){
 // A previous browser/role may have consumed this account's current counter.
 // Wait once for a new counter; never retry an invalid factor or use recovery codes.
 const previous=Math.floor(now()/30000),delay=(previous+1)*30000-now()+1000;
 requireValue(delay>0&&delay<=31000);await wait(delay);
 const current=Math.floor(now()/30000);requireValue(current>previous);
 return totp(secret,current);
}

export async function scopeIdentityRequests(context,identity,robotBypass){
 const {robot,portal}=validateIdentity(identity,'administrator');
 requireValue(robotBypass===null||robotBypass===undefined||identity.environment==='staging'&&typeof robotBypass==='string'&&/^[!-~]{1,8192}$/.test(robotBypass));
 if(configuredContexts.has(context))return;
 await context.setExtraHTTPHeaders({});
 for(const [origin,secret] of [[robot,robotBypass],[portal,identity.portal_bypass]]){
  if(!secret)continue;
  const isolated=await context.browser().newContext();
  try{
   const response=await isolated.request.get(origin,{maxRedirects:0,headers:{'x-vercel-protection-bypass':secret,'x-vercel-set-bypass-cookie':'true'}});
   requireValue([200,302,303,307,308].includes(response.status()));
   const location=response.headers().location;
   if(location)requireValue(new URL(location,origin).origin===origin);
   const cookies=(await isolated.cookies(origin)).filter(cookie=>cookie.name==='_vercel_jwt');
   requireValue(cookies.length===1&&cookies[0].secure&&cookies[0].domain.replace(/^\./,'')===new URL(origin).hostname);
   await context.addCookies([{name:'_vercel_jwt',value:cookies[0].value,url:origin,secure:true,httpOnly:true,sameSite:'Lax'}]);
  }finally{await isolated.close();}
 }
 await context.route('**/*',async route=>{
  const request=route.request(),target=new URL(request.url());
  // Do not override Cookie or credential headers: overrides may survive a
  // redirect hop that Playwright does not route again. Host-only cookies scope access.
  if(request.headers()['x-vercel-protection-bypass'])return route.abort();
  if(![robot,portal].includes(target.origin)&&request.isNavigationRequest?.())return route.abort();
  if(!['GET','HEAD'].includes(request.method())){
   if(![robot,portal].includes(target.origin))return route.abort();
   if(target.origin===portal&&!/^\/api\/v1\/account\/(?:login|interaction\/[A-Za-z0-9_-]{8,128}\/(?:login|continue))$/.test(target.pathname))return route.abort();
  }
  return route.continue();
 });
 configuredContexts.add(context);
}

async function jsonGet(context,origin,path){
 const response=await context.request.get(origin+path,{maxRedirects:0});
 requireValue(response.status()===200);const raw=await response.body();requireValue(raw.length<=65536);
 return JSON.parse(raw.toString('utf8'));
}

function observeIdentityStart(page,identity,stepUp){
 let rejectBudget,timer;
 const captured=page.waitForResponse(value=>value.request().method()==='POST'&&value.url()===identity.origin+'/api/v1/auth/identity/start',{timeout:15000}).then(async response=>{
  requireValue(response.status()===200);
  return validateAuthorization((await response.json()).authorization_url,identity,stepUp);
 });
 const budget=new Promise((_resolve,reject)=>{rejectBudget=reject;timer=setTimeout(()=>reject(new Error('ROBOT_IDENTITY_START_TIMEOUT')),15000);});
 const promise=Promise.race([captured,budget]).finally(()=>clearTimeout(timer));
 promise.catch(()=>{});
 return {promise,close(){clearTimeout(timer);rejectBudget(new Error('ROBOT_IDENTITY_START_CLOSED'));}};
}

async function holdIdentityNavigation(page,identity,start){
 const issuer=new URL(identity.issuer),frame=page.mainFrame();
 const matches=url=>url.origin===issuer.origin&&url.pathname===issuer.pathname+'/auth';
 let active=true,resolveWait,rejectWait;
 const wait=new Promise((resolve,reject)=>{resolveWait=resolve;rejectWait=reject;});
 wait.catch(()=>{});
 const timer=setTimeout(()=>{active=false;rejectWait(new Error('ROBOT_IDENTITY_NAVIGATION_TIMEOUT'));},20000);
 const handler=async route=>{
  try{
   const authorization=await start,request=route.request();
   requireValue(active&&request.isNavigationRequest()&&request.frame()===frame&&request.method()==='GET'&&request.url()===authorization.href);
   // Keep the existing context route guard and native cookie/redirect handling.
   await route.fallback();clearTimeout(timer);resolveWait();
  }catch(error){active=false;clearTimeout(timer);rejectWait(error);await route.abort().catch(()=>{});}
 };
 const close=async()=>{active=false;clearTimeout(timer);rejectWait(new Error('ROBOT_IDENTITY_NAVIGATION_CLOSED'));await page.unroute(matches,handler);};
 try{await page.route(matches,handler,{times:1});return {wait,close};}
 catch(error){await close().catch(()=>{});throw error;}
}

export async function signInIdentity(page,context,identity,{actor='administrator',robotBypass=null,stepUp=false,apiOnly=false,code=freshCode}={}){
 let startObserver,navigation;
 try{
  const {robot,portal,account}=validateIdentity(identity,actor);
  const {expect}=await import('@playwright/test');
  await scopeIdentityRequests(context,identity,robotBypass);
  requireValue(new URL(page.url()).origin===robot);
  requireValue(typeof apiOnly==='boolean'&&(!apiOnly||!stepUp));
  startObserver=observeIdentityStart(page,identity,stepUp);
  const start=startObserver.promise;
  if(apiOnly){
   // API acceptance can precede the independently deployed product UI. This
   // still creates a real browser-bound PKCE transaction on the product origin.
   await page.evaluate(async()=>{
    const result=await fetch('/api/v1/auth/identity/start',{method:'POST',mode:'same-origin',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify({step_up:false}),signal:AbortSignal.timeout(15000)});
    if(!result.ok)throw new Error('IDENTITY_START_REFUSED');
    await result.arrayBuffer();
   });
  }else{
   // Chromium can discard the response body during a cross-origin document swap.
   // Hold only the exact authorization navigation until its original JSON is valid.
   navigation=await holdIdentityNavigation(page,identity,start);
   await page.locator(stepUp?'#onesa-confirm-identity':'#onesa-sign-in-button').click();
   await navigation.wait;
  }
  const authorization=await start;
  const callback=page.waitForRequest(request=>{
   const target=new URL(request.url());return target.origin===robot&&target.pathname==='/api/v1/auth/identity/callback';
  },{timeout:90000});
  callback.catch(()=>{}); // Awaited below; early form failure remains a fixed error.
  if(apiOnly)await page.goto(authorization.href);
  await page.waitForURL(url=>url.origin===portal&&/^\/account\/sign-in\/[A-Za-z0-9_-]{8,128}$/.test(url.pathname),{timeout:20000});
  await expect(page.locator('input[name="email"]')).toBeEditable();
  requireValue(new URL(page.url()).origin===portal);
  await page.locator('input[name="email"]').fill(account.email);
  requireValue(new URL(page.url()).origin===portal);
  await page.locator('input[name="password"]').fill(account.password);
  const factor=page.locator('input[name="code"]');
  const supplied=await factor.isVisible();
  if(supplied){
   const factorValue=await code(account.totp_secret);requireValue(new URL(page.url()).origin===portal);
   await factor.fill(factorValue);
  }
  await page.locator('form button').filter({hasText:/^(Continue|Weiter)$/}).click();
  if(!supplied){
   await expect(factor).toBeEditable({timeout:20000});
   requireValue(new URL(page.url()).origin===portal);
   const factorValue=await code(account.totp_secret);requireValue(new URL(page.url()).origin===portal);
   await factor.fill(factorValue);
   await page.locator('form button').filter({hasText:/^(Continue|Weiter)$/}).click();
  }
  validateCallback((await callback).url(),identity,authorization);
  await page.waitForURL(url=>url.origin===robot&&url.pathname==='/console'&&!url.search,{timeout:30000});
  await expect(page.locator('#operator')).toBeVisible({timeout:30000});
  const portalSession=await jsonGet(context,portal,'/api/v1/account/me');
  requireValue(portalSession.account?.id===account.subject&&portalSession.account.email===account.email&&portalSession.account.emailVerified===true&&portalSession.account.mfaEnrolled===true);
  const session=await jsonGet(context,robot,'/api/v1/auth/me'),instant=Date.now()/1000;
  requireValue(session.authentication_method==='onesa'&&session.role===actor&&/^[A-Za-z0-9_-]{20,128}$/.test(session.csrf));
  requireValue(Number.isFinite(session.confirmation_expires_at)&&session.confirmation_expires_at>instant&&session.confirmation_expires_at<=instant+330);
  if(actor==='operator')requireValue(session.global_administrator===false&&portalSession.account.globalAdmin===false);
  const cookies=(await context.cookies(robot+'/api/v1/auth/me')).filter(cookie=>cookie.name==='robot_session');
  requireValue(cookies.length===1);const cookie=cookies[0];
  requireValue(cookie.domain===new URL(robot).hostname&&cookie.path==='/api/v1'&&cookie.secure&&cookie.httpOnly&&cookie.sameSite==='Strict');
  requireValue(/^[A-Za-z0-9_-]{20,128}$/.test(cookie.value)&&cookie.expires>instant&&cookie.expires<=instant+86400);
  return {schema_version:1,origin:robot,issuer:identity.issuer,subject:account.subject,role:actor,csrf:session.csrf,confirmation_expires_at:session.confirmation_expires_at,
   cookie:Object.fromEntries(['name','value','domain','path','expires','httpOnly','secure','sameSite'].map(key=>[key,cookie[key]]))};
 }catch{throw new Error('ROBOT_IDENTITY_FLOW_FAILED');}
 finally{
  startObserver?.close();
  if(navigation)try{await navigation.close();}catch{throw new Error('ROBOT_IDENTITY_FLOW_FAILED');}
 }
}

export async function acquireIdentity(request){
 validateIdentity(request.identity,request.actor);
 requireValue(typeof request.api_only==='boolean');
 const {chromium}=await import('@playwright/test');
 const browser=await chromium.launch({headless:true});
 try{
  const context=await browser.newContext({locale:'en-US'});
  await scopeIdentityRequests(context,request.identity,request.robot_bypass);
  const page=await context.newPage();await page.goto(request.identity.origin+'/console');
  const result=await signInIdentity(page,context,request.identity,{actor:request.actor,robotBypass:request.robot_bypass,apiOnly:request.api_only});
  await context.close();return result;
 }finally{await browser.close();}
}

if(process.argv[1]&&import.meta.url===new URL('file:'+process.argv[1]).href){
 try{
  const raw=fs.readFileSync(0);requireValue(raw.length>0&&raw.length<=65536);
  const request=JSON.parse(raw.toString('utf8'));requireValue(Object.keys(request).sort().join(',')==='actor,api_only,identity,robot_bypass');
  process.stdout.write(JSON.stringify(await acquireIdentity(request)));
 }catch{process.stderr.write('ROBOT_IDENTITY_SESSION_REFUSED\n');process.exitCode=1;}
}
