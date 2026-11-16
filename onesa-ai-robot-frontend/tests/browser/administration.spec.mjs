import {test,expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

async function fixture(page,role='administrator'){
 let signedIn=false,revision=0,allowed=false,failRead=false;const mutations=[],reads=[];
 const state=()=>({permission:allowed?'allowed':'blocked',revision,expires_at:allowed?Date.now()/1000+600:null,controller_connection:'not_connected',controller_readiness:'not_ready',physical_operation:'inactive',physical_actuation:false,sampled_at:Date.now()/1000});
 await page.route('**/api/v1/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname;let status=200,body={};
  if(request.method()==='POST')mutations.push(path);else reads.push(path);
  if(path.endsWith('/ready'))body={status:'ready',mode:'simulation',physical_actuation:false};
  else if(path.endsWith('/auth/login')){signedIn=true;body={csrf:'synthetic-csrf',name:'synthetic-owner',role};}
  else if(path.endsWith('/auth/me')){status=signedIn?200:401;body=signedIn?{csrf:'synthetic-csrf',name:'synthetic-owner',role}:{detail:'Sign in required'};}
  else if(path.endsWith('/auth/logout')){signedIn=false;allowed=false;revision++;}
  else if(path.startsWith('/api/v1/admin/')){
   if(!signedIn||role!=='administrator'){status=signedIn?403:401;body={detail:'Administrator authorization required'};}
   else if(path.endsWith('/allow')){const data=request.postDataJSON();expect(data).toEqual({password:'synthetic-confirmation',expected_revision:revision});allowed=true;revision++;body=state();}
   else if(path.endsWith('/block')){allowed=false;revision++;body=state();}
   else if(failRead){status=503;body={detail:'Permission observation unavailable'};}
   else body=state();
  }else if(path.endsWith('/status'))body={mode:'simulation',physical_actuation:false,stopped:true,position_radians:0,active_task:null,sampled_at:Date.now()/1000,capabilities:{edge_telemetry:'not_connected'},deployment_environment:'local'};
  else if(path.endsWith('/tasks'))body={tasks:[]};
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto('/console');await page.getByLabel('Operator name').fill('synthetic-owner');await page.getByLabel('Password',{exact:true}).fill('synthetic-login');await page.getByRole('button',{name:'Sign in',exact:true}).click();await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible();
 return {mutations,reads,failReads(value){failRead=value;},externalBlock(){allowed=false;revision++;},restart(){allowed=false;revision=0;}};
}

test('administrator can allow and block permission without claiming physical activation',async({page})=>{
 const errors=[];page.on('pageerror',error=>errors.push(error.name));await page.setViewportSize({width:390,height:1000});
 const {mutations}=await fixture(page);
 await expect(page.getByRole('heading',{name:'Robot administration'})).toBeVisible();
 await expect(page.locator('#physical-permission-state')).toHaveText('Blocked');
 const password=page.getByLabel('Confirm administrator password to allow physical operation');await password.fill('synthetic-confirmation');await password.focus();
 const actions=()=>mutations.filter(path=>path!=='/api/v1/heartbeat');const before=actions();await page.emulateMedia({colorScheme:'dark'});await expect(password).toBeFocused();await expect(password).toHaveValue('synthetic-confirmation');expect(actions()).toEqual(before);
 await page.getByRole('button',{name:'Allow physical operation',exact:true}).click();await expect(page.locator('#physical-permission-state')).toHaveText('Allowed for the issuing administrator session');await expect(password).toHaveValue('');
 await expect(page.locator('#physical-operation-state')).toHaveText('Inactive · controller not connected');
 await page.getByRole('button',{name:'Block physical operation',exact:true}).click();await expect(page.locator('#physical-permission-state')).toHaveText('Blocked');
 expect((await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze()).violations).toEqual([]);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.getByRole('button',{name:'Sign out'}).click();await expect(page.locator('#administrator')).toBeHidden();expect(errors).toEqual([]);
 expect(mutations.filter(path=>path.includes('/tasks'))).toEqual([]);
});

test('ordinary operator sees no administrator controls or permission reads',async({page})=>{
 const {reads}=await fixture(page,'operator');await expect(page.locator('#administrator')).toBeHidden();await expect(page.getByRole('button',{name:'Allow physical operation',exact:true})).toBeHidden();expect(reads.filter(path=>path.includes('/admin/'))).toEqual([]);
});

test('idle administrator statuses follow repeated system appearance changes without operator actions',async({page})=>{
 await page.setViewportSize({width:1440,height:900});
 const {mutations}=await fixture(page);
 await expect(page.locator('#physical-permission-state')).toHaveText('Blocked');
 const password=page.getByLabel('Confirm administrator password to allow physical operation');
 await password.fill('synthetic-confirmation');await password.focus();
 // Ordinary supervision continues during accessibility scans; compare every
 // operator action, without mistaking the scheduled heartbeat for a theme write.
 const actions=()=>mutations.filter(path=>path!=='/api/v1/heartbeat');const before=actions();
 for(const [colorScheme,foreground] of [['dark','rgb(229, 237, 246)'],['light','rgb(16, 38, 62)'],['dark','rgb(229, 237, 246)']]){
  await page.emulateMedia({colorScheme});
  await expect.poll(()=>page.evaluate(()=>getComputedStyle(document.body).color)).toBe(foreground);
  // A stale inherited leaf can remain unreadable after the canvas has changed.
  for(const id of ['physical-permission-state','physical-controller-state','physical-operation-state','physical-permission-expiry'])
   expect(await page.locator('#'+id).evaluate(element=>getComputedStyle(element).color)).toBe(foreground);
  await expect(password).toBeFocused();await expect(password).toHaveValue('synthetic-confirmation');
  await expect(page.locator('#physical-permission-state')).toHaveText('Blocked');
  expect(actions()).toEqual(before);
  expect((await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze()).violations).toEqual([]);
 }
});

test('permission reviews expire on revision changes and failed reads clear the whole panel',async({page})=>{
 const state=await fixture(page);
 const allow=page.getByRole('button',{name:'Allow physical operation',exact:true});
 const block=page.getByRole('button',{name:'Block physical operation',exact:true});
 const password=page.getByLabel('Confirm administrator password to allow physical operation');
 await expect(allow).toBeEnabled();
 await password.fill('synthetic-confirmation');
 state.externalBlock();
 await expect(password).toHaveValue('',{timeout:10000});
 await password.fill('synthetic-confirmation');
 await allow.click();
 await expect(page.locator('#physical-permission-state')).toHaveText('Allowed for the issuing administrator session');
 state.failReads(true);
 for(const id of ['physical-permission-state','physical-controller-state','physical-operation-state','physical-permission-expiry']){
  await expect(page.locator('#'+id)).toHaveText('Unavailable',{timeout:10000});
 }
 await expect(allow).toBeDisabled();
 await expect(block).toBeEnabled();
 state.failReads(false);
 await block.click();
 await expect(page.locator('#physical-permission-state')).toHaveText('Blocked');
 await expect(allow).toBeEnabled();
 expect(state.mutations.filter(path=>path.endsWith('/allow'))).toHaveLength(1);
});

test('a lower permission revision clears the old grant until a new read establishes the baseline',async({page})=>{
 const state=await fixture(page);
 const allow=page.getByRole('button',{name:'Allow physical operation',exact:true});
 const password=page.getByLabel('Confirm administrator password to allow physical operation');
 const permission=page.locator('#physical-permission-state');
 await expect(allow).toBeEnabled();
 await password.fill('synthetic-confirmation');
 await allow.click();
 await expect(permission).toHaveText('Allowed for the issuing administrator session');
 await password.fill('synthetic-confirmation');
 state.restart();
 await expect(permission).toHaveText('Unavailable',{timeout:10000});
 await expect(password).toHaveValue('');
 await expect(allow).toBeDisabled();
 await expect(page.getByRole('button',{name:'Block physical operation',exact:true})).toBeEnabled();
 await expect(permission).toHaveText('Blocked',{timeout:10000});
 await expect(allow).toBeEnabled();
 expect(state.mutations.filter(path=>path.endsWith('/allow'))).toHaveLength(1);
});
