import {test,expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {waitForApprovedSimulationCompletion,waitForSimulationReset} from './simulation-acceptance.mjs';
import {cleanupOperatorSession} from './session-cleanup.mjs';
import {waitForOperatorSignIn} from './sign-in-acceptance.mjs';
import {observeRequestTimings} from './request-timings.mjs';
import {reloadWithSessionAcknowledgement,signOutWithAcknowledgement} from './session-acceptance.mjs';
const requestTimings=new WeakMap();
test.beforeEach(async({page,baseURL})=>{requestTimings.set(page,observeRequestTimings(page,baseURL));});
test.afterEach(async({page,baseURL},testInfo)=>{if(testInfo.status!==testInfo.expectedStatus)console.log('Bounded Robot request timings: '+JSON.stringify(requestTimings.get(page)?.snapshot()||{}));await page.unrouteAll({behavior:'ignoreErrors'});await cleanupOperatorSession(page,baseURL);});
for(const [hour,mode] of [[20,'light'],[12,'dark']]) for(const width of [320,390,768,1024,1440]) test(`simulation ${mode} flows and accessibility at ${width}`,async({page})=>{
 await page.addInitScript(hour=>{Date.prototype.getHours=()=>hour;},hour);
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.emulateMedia({colorScheme:mode});await page.setViewportSize({width,height:1000});await page.goto('/console');
 await expect(page).toHaveTitle('Operator Console | Onesa AI Robot');
 await expect(page.locator('.appearance-control')).toHaveCount(0);
 expect(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme)).toBe(mode);
 expect(await page.evaluate(()=>getComputedStyle(document.body).backgroundColor)).toBe(mode==='dark'?'rgb(11, 21, 35)':'rgb(255, 255, 255)');
 await page.keyboard.press('Tab');
 await expect(page.getByRole('link',{name:'Skip to content'})).toBeFocused();
 await page.getByLabel('Operator name').fill(process.env.ROBOT_TEST_OPERATOR || 'operator');await page.getByLabel('Password',{exact:true}).fill((process.env.ROBOT_TEST_PASSWORD || 'browser-test-operator-only'));await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible({timeout:30000});for(let navigation=0;navigation<3;navigation++){await reloadWithSessionAcknowledgement(page);await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible();}
 await page.getByLabel('Confirm password to reset').fill((process.env.ROBOT_TEST_PASSWORD || 'browser-test-operator-only'));await page.getByRole('button',{name:'Reset simulation stop'}).click();
 await waitForSimulationReset(page);
 await page.locator('#joint-tools > summary').click();await page.getByRole('button',{name:'Create proposal'}).click();await expect(page.locator('#approval-form')).toBeVisible();await expect(page.locator('#proposed-id')).toContainText(/[a-f0-9-]{36}/);const taskId=await page.locator('#proposed-id').textContent();await page.getByLabel('Confirm operator password',{exact:true}).fill((process.env.ROBOT_TEST_PASSWORD || 'browser-test-operator-only'));await page.getByRole('button',{name:'Approve simulation task'}).click();
 await waitForApprovedSimulationCompletion(page,taskId);
 await page.getByRole('button',{name:'Stop simulation',exact:true}).click();await expect(page.locator('#stop-state')).toContainText('Stopped');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
 if(process.env.ONESA_VISUAL_EVIDENCE_DIR&&[390,1440].includes(width))await page.screenshot({path:`${process.env.ONESA_VISUAL_EVIDENCE_DIR}/${test.info().project.name}/robot-console-${mode}-${width}.png`,fullPage:true});
 const axe=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']).analyze();expect(axe.violations).toEqual([]);
 await page.getByRole('button',{name:'Sign out'}).click();await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeVisible();
 expect(errors).toEqual([]);
});
test('late unauthorized telemetry cannot sign out a newer operator session',async({page})=>{
 let held, release;
 const received=new Promise(resolve=>held=resolve);
 const wait=new Promise(resolve=>release=resolve);
 await page.route('**/api/v1/status',async route=>{
  if(!held){await route.continue();return;}
  const signal=held;held=null;signal();await wait;
  await route.fulfill({status:401,contentType:'application/json',body:'{"detail":"Old session expired"}'});
 });
 await page.goto('/console');
 async function signIn(){await page.getByLabel('Operator name').fill(process.env.ROBOT_TEST_OPERATOR || 'operator');await page.getByLabel('Password',{exact:true}).fill((process.env.ROBOT_TEST_PASSWORD || 'browser-test-operator-only'));await page.getByRole('button',{name:'Sign in',exact:true}).click();await waitForOperatorSignIn(page);}
 await signIn();await received;
 await signOutWithAcknowledgement(page);
 await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeVisible();
 await signIn();release();
 await expect(page.locator('#connection')).toHaveText('Connected to software simulator');
 await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible();
 await signOutWithAcknowledgement(page);
 await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeVisible();
});
test('independent Stop and Sign out work while another form response is pending',async({page})=>{
 // Test-owned HTTP responses isolate the interaction race: no server session,
 // reset, stop, task or credential mutation occurs in this regression fixture.
 let session=false,resets=0,stops=0,logouts=0,held,release;
 const received=new Promise(resolve=>held=resolve),pending=new Promise(resolve=>release=resolve);
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  let status=200,body={};
  if(path.endsWith('/ready'))body={status:'ready',mode:'simulation',physical_actuation:false};
  else if(path.endsWith('/auth/me')){status=session?200:401;body=session?{csrf:'fixture-csrf'}:{detail:'Fixture signed out'};}
  else if(path.endsWith('/auth/login')){session=true;body={csrf:'fixture-csrf'};}
  else if(path.endsWith('/auth/logout')){session=false;logouts++;}
  else if(path.endsWith('/reset')){resets++;held();await pending;}
  else if(path.endsWith('/stop'))stops++;
  else if(path.endsWith('/status'))body={mode:'simulation',physical_actuation:false,stopped:true,position_radians:0,active_task:null,sampled_at:Date.now()/1000,capabilities:{edge_telemetry:'disconnected'},deployment_environment:'local'};
  else if(path.endsWith('/tasks'))body={tasks:[]};
  await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto('/console');await page.getByLabel('Operator name').fill('fixture-operator');await page.getByLabel('Password',{exact:true}).fill('fixture-password');await page.getByRole('button',{name:'Sign in',exact:true}).click();await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible();
 await page.getByLabel('Confirm password to reset').fill('fixture-password');await page.getByRole('button',{name:'Reset simulation stop'}).click();await received;await expect(page.locator('#reset-form')).toHaveAttribute('aria-busy','true');
 await page.getByLabel('Confirm password to reset').focus();await page.getByRole('button',{name:'Stop simulation',exact:true}).click();await expect.poll(()=>stops).toBe(1);
 await page.evaluate(()=>document.querySelector('#reset-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));expect(resets).toBe(1);
 await page.getByLabel('Confirm password to reset').focus();await page.getByRole('button',{name:'Sign out'}).click();await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeVisible();expect(logouts).toBe(1);
 release();await expect(page.locator('#reset-form')).not.toHaveAttribute('aria-busy','true');await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeVisible();expect(resets).toBe(1);
});
for(const mode of ['light','dark'])for(const width of [320,390,768,1024,1440])test(`public Robot identity and telemetry isolation ${mode} at ${width}`,async({page})=>{
 await page.emulateMedia({colorScheme:mode});await page.setViewportSize({width,height:1000});const privateRequests=[];page.on('request',request=>{if(request.url().includes('/api/v1/'))privateRequests.push(request.url());});
 await page.goto('/');await expect(page).toHaveTitle('Product | Onesa AI Robot');await expect(page.getByRole('heading',{name:/Inspect with purpose/})).toBeVisible();const hero=page.getByRole('img',{name:'Original illustration of the synthetic Pump P-101 inspection scene with a software robot at the inspection position.'});await expect(hero).toBeVisible();await expect.poll(()=>hero.evaluate(image=>image.complete&&image.naturalWidth>0)).toBe(true);await expect(page.locator('#operator')).toHaveCount(0);expect(privateRequests).toEqual([]);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();expect((await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']).analyze()).violations).toEqual([]);
 if(process.env.ONESA_VISUAL_EVIDENCE_DIR&&[390,1440].includes(width))await page.screenshot({path:`${process.env.ONESA_VISUAL_EVIDENCE_DIR}/${test.info().project.name}/robot-product-${mode}-${width}.png`,fullPage:true});
});
test('system preference change preserves a proposal draft, focus and simulation authority',async({page})=>{
 await page.emulateMedia({colorScheme:'light'});await page.goto('/console');
 await page.getByLabel('Operator name').fill(process.env.ROBOT_TEST_OPERATOR || 'operator');await page.getByLabel('Password',{exact:true}).fill(process.env.ROBOT_TEST_PASSWORD || 'browser-test-operator-only');await page.getByRole('button',{name:'Sign in',exact:true}).click();await waitForOperatorSignIn(page);
 await page.getByLabel('Confirm password to reset').fill(process.env.ROBOT_TEST_PASSWORD || 'browser-test-operator-only');await page.getByRole('button',{name:'Reset simulation stop'}).click();await waitForSimulationReset(page);
 await page.locator('#joint-tools > summary').click();
 const target=page.getByLabel('Target position');await target.fill('0.08');await target.focus();await page.emulateMedia({colorScheme:'dark'});await expect(target).toHaveValue('0.08');await expect(target).toBeFocused();await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible();
 await page.getByRole('button',{name:'Create proposal'}).click();await expect(page.locator('#proposed-id')).toContainText(/[a-f0-9-]{36}/);const taskId=await page.locator('#proposed-id').textContent();await page.getByLabel('Confirm operator password',{exact:true}).fill(process.env.ROBOT_TEST_PASSWORD || 'browser-test-operator-only');await page.getByRole('button',{name:'Approve simulation task'}).click();await expect(page.locator('#notice')).toContainText('Simulation running');
 const mutations=[];page.on('request',request=>{if(request.method()==='POST'&&!request.url().endsWith('/heartbeat'))mutations.push(new URL(request.url()).pathname);});await page.emulateMedia({colorScheme:'light'});await expect(page.locator('#history li').filter({hasText:taskId})).toContainText('completed',{timeout:6000});expect(mutations).toEqual([]);await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible();
 // This owner-coordinated synthetic fixture restores only its own completed scenario.
 // Recent-history snapshots do not certify arbitrary concurrent operator activity.
 const state=await(await page.request.get('/api/v1/status')).json();const history=(await(await page.request.get('/api/v1/tasks')).json()).tasks;
 expect(state.mode).toBe('simulation');expect(state.physical_actuation).toBe(false);expect(state.active_task).toBe(null);expect(history.find(task=>task.id===taskId)?.state).toBe('completed');expect(history.filter(task=>['running','proposed'].includes(task.state))).toEqual([]);
 await page.getByRole('button',{name:'Stop simulation',exact:true}).click();await expect(page.locator('#stop-state')).toContainText('Stopped');expect((await(await page.request.get('/api/v1/tasks')).json()).tasks).toEqual(history);
});
test('forced colors and reduced motion keep operator navigation and focus accessible',async({page})=>{
 await page.emulateMedia({forcedColors:'active',reducedMotion:'reduce',colorScheme:'dark'});await page.goto('/console');await page.keyboard.press('Tab');await expect(page.getByRole('link',{name:'Skip to content'})).toBeFocused();await expect(page.getByLabel('Operator name')).toBeVisible();expect(await page.evaluate(()=>getComputedStyle(document.querySelector('.skip')).outlineStyle)).not.toBe('none');
});
test('readiness recovery sends credentials once and never starts a task',async({page})=>{
 let readiness=0,logins=0;const mutations=[];
 await page.route('**/api/v1/ready',async route=>{readiness++;if(readiness<=2)await route.fulfill({status:503,contentType:'application/json',body:'{"detail":"Gateway unavailable"}'});else await route.continue();});
 page.on('request',request=>{if(request.method()==='POST'){const path=new URL(request.url()).pathname;if(path.endsWith('/auth/login'))logins++;else if(!path.endsWith('/heartbeat'))mutations.push(path);}});
 await page.goto('/console');await page.getByLabel('Operator name').fill(process.env.ROBOT_TEST_OPERATOR||'operator');await page.getByLabel('Password',{exact:true}).fill(process.env.ROBOT_TEST_PASSWORD||'browser-test-operator-only');await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await expect(page.locator('#notice')).toContainText('Checking simulator readiness');await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible({timeout:30000});expect(readiness).toBeGreaterThanOrEqual(3);expect(logins).toBe(1);expect(mutations).toEqual([]);await expect(page.getByLabel('Password',{exact:true})).toHaveValue('');
});
test('unavailable sign in retains masked input for an explicit retry without replay',async({page})=>{
 test.setTimeout(45000);let logins=0;
 const password=process.env.ROBOT_TEST_PASSWORD||'browser-test-operator-only';
 await page.route('**/api/v1/ready',route=>route.fulfill({status:503,contentType:'application/json',body:'{"detail":"Gateway unavailable"}'}));
 page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/auth/login'))logins++;});
 await page.goto('/console');await page.getByLabel('Operator name').fill(process.env.ROBOT_TEST_OPERATOR||'operator');await page.getByLabel('Password',{exact:true}).fill(password);await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await expect(page.locator('#notice')).toContainText('Your sign-in entries are retained',{timeout:25000});expect(logins).toBe(0);expect(await page.getByLabel('Password',{exact:true}).inputValue()===password).toBe(true);await expect(page.getByLabel('Password',{exact:true})).toHaveAttribute('type','password');await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeEnabled();
 await page.unroute('**/api/v1/ready');await page.getByRole('button',{name:'Sign in',exact:true}).click();await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible({timeout:30000});expect(logins).toBe(1);
});
test('a failed credential submission needs a manual retry and preserves masked input',async({page})=>{
 let logins=0;const password=process.env.ROBOT_TEST_PASSWORD||'browser-test-operator-only';
 await page.route('**/api/v1/auth/login',async route=>{logins++;if(logins===1)await route.fulfill({status:503,contentType:'application/json',body:'{"detail":"Gateway unavailable"}'});else await route.continue();});
 await page.goto('/console');await page.getByLabel('Operator name').fill(process.env.ROBOT_TEST_OPERATOR||'operator');await page.getByLabel('Password',{exact:true}).fill(password);await page.getByRole('button',{name:'Sign in',exact:true}).click();await expect(page.locator('#notice')).toContainText('select Sign in again');expect(logins).toBe(1);expect(await page.getByLabel('Password',{exact:true}).inputValue()===password).toBe(true);
 await page.getByRole('button',{name:'Sign in',exact:true}).click();await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible({timeout:30000});expect(logins).toBe(2);
});
test('actual public and console icon requests match their declared MIME and approved bytes',async({page})=>{
 const provenance=JSON.parse(await readFile(new URL('../../public/brand/icons/v1/sources.json',import.meta.url),'utf8'));
 for(const route of ['/','/console']){await page.goto(route);const icons=await page.locator('link[rel="icon"],link[rel="apple-touch-icon"]').evaluateAll(nodes=>nodes.map(node=>({href:node.getAttribute('href'),type:node.getAttribute('type')})));expect(icons).toHaveLength(3);
  for(const icon of icons){const response=await page.request.get(icon.href);expect(response.status()).toBe(200);const type=response.headers()['content-type'];const bytes=await response.body();const name=icon.href==='/favicon.ico'?'onesa-robot.ico':icon.href.split('/').pop();const expected=provenance.products[0].files.find(file=>file.name===name);expect(createHash('sha256').update(bytes).digest('hex')).toBe(expected.sha256);expect(type.split(';')[0]).toMatch(name.endsWith('.svg')?/^image\/svg\+xml$/:name.endsWith('.ico')?/^image\/(?:vnd.microsoft.icon|x-icon)$/:/^image\/png$/);}
 }
});
