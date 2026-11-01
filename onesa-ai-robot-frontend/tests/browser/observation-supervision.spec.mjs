import {test,expect} from '@playwright/test';
import {cleanupOperatorSession} from './session-cleanup.mjs';
import {waitForOperatorSignIn} from './sign-in-acceptance.mjs';
import {waitForSimulationReset} from './simulation-acceptance.mjs';

test.afterEach(async({page,baseURL})=>cleanupOperatorSession(page,baseURL));

for(const failedRead of ['status','tasks'])test(`loss of ${failedRead} observation stops the actual simulator despite a reachable heartbeat`,async({page})=>{
 const password=process.env.ROBOT_TEST_PASSWORD||'browser-test-operator-only';
 await page.goto('/console');
 await page.getByLabel('Operator name').fill(process.env.ROBOT_TEST_OPERATOR||'operator');
 await page.getByLabel('Password',{exact:true}).fill(password);
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await waitForOperatorSignIn(page);
 const stopped=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/stop'&&response.request().method()==='POST');
 await page.getByRole('button',{name:'Stop simulation',exact:true}).click();
 expect((await stopped).status()).toBe(200);
 await page.getByLabel('Confirm password to reset').fill(password);
 await page.getByRole('button',{name:'Reset simulation stop'}).click();
 await waitForSimulationReset(page);
 await page.locator('#joint-tools > summary').click();
 await page.locator('#target').fill('0.2');
 await page.locator('#duration').fill('10');
 await page.getByRole('button',{name:'Create proposal',exact:true}).click();
 await expect(page.locator('#proposed-id')).toContainText(/[a-f0-9-]{36}/);
 const taskId=await page.locator('#proposed-id').textContent();
 await page.getByLabel('Confirm operator password',{exact:true}).fill(password);
 await page.getByRole('button',{name:'Approve simulation task',exact:true}).click();
 await expect(page.locator('#notice')).toContainText('Simulation running');
 const active=await (await page.request.get('/api/v1/status')).json();
 expect(active.active_task).toBe(taskId);
 let failedReads=0;
 await page.route(`**/api/v1/${failedRead}`,async route=>{
  failedReads++;
  await route.fulfill({status:503,contentType:'application/json',body:'{"detail":"Synthetic observation outage"}'});
 });
 await expect(page.locator('#connection')).toContainText('Disconnected');
 await expect(page.getByRole('button',{name:'Stop simulation',exact:true})).toBeEnabled();
 await expect(page.getByRole('button',{name:'Sign out',exact:true})).toBeEnabled();
 // APIRequestContext reads the real API outside the intercepted page route.
 // No test-owned heartbeat or Stop helps the watchdog satisfy this assertion.
 await expect.poll(async()=>{
  const response=await page.request.get('/api/v1/status');
  expect(response.status()).toBe(200);
  const observed=await response.json();
  expect(observed.physical_actuation).toBe(false);
  return observed.stopped&&observed.active_task===null;
 },{timeout:6000}).toBe(true);
 expect(failedReads).toBeGreaterThan(0);
 const history=await (await page.request.get('/api/v1/tasks')).json();
 expect(history.tasks.find(task=>task.id===taskId)).toMatchObject({state:'cancelled',reason:'operator_connection_lost'});
});
