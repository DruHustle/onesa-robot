import {test,expect} from '@playwright/test';
import {cleanupOperatorSession} from './session-cleanup.mjs';
import {waitForSimulationReset} from './simulation-acceptance.mjs';

// This case deliberately uses the actual frontend proxy and simulator. Intercepted
// UI fixtures cannot detect a missing gateway route or a drifted API contract.
test.afterEach(async({page,baseURL})=>cleanupOperatorSession(page,baseURL));
test('authenticated whole-body workflow crosses the real gateway and exports reference URDF',async({page})=>{
 await page.goto('/console');
 await page.getByLabel('Operator name').fill(process.env.ROBOT_TEST_OPERATOR||'operator');
 await page.getByLabel('Password',{exact:true}).fill(process.env.ROBOT_TEST_PASSWORD||'browser-test-operator-only');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await expect(page.getByRole('button',{name:'Sign out',exact:true})).toBeVisible({timeout:30000});
 // Establish this case's own stopped baseline even after a completed earlier run.
 const stopAcknowledgement=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/stop'&&response.request().method()==='POST');
 await page.getByRole('button',{name:'Stop simulation',exact:true}).click();
 const stopResponse=await stopAcknowledgement;
 expect(stopResponse.status()).toBe(200);
 expect(await stopResponse.json()).toMatchObject({status:'stopped',physical_actuation:false});
 await expect(page.locator('#reset-form')).toBeVisible();
 await page.getByLabel('Confirm password to reset').fill(process.env.ROBOT_TEST_PASSWORD||'browser-test-operator-only');
 await page.getByRole('button',{name:'Reset simulation stop'}).click();
 await waitForSimulationReset(page);
 await page.locator('#humanoid > summary').click();
 await expect(page.locator('#humanoid-joints input')).toHaveCount(33);
 const urdf=await page.request.get('/api/v1/humanoid/model.urdf');
 expect(urdf.status()).toBe(200);
 expect(urdf.headers()['content-type']).toContain('application/xml');
 expect(urdf.headers()['content-disposition']).toContain('onesa-humanoid-reference.urdf');
 expect((await urdf.text()).match(/<joint /g)).toHaveLength(33);
 await page.getByLabel('left arm elbow',{exact:true}).fill('0.2');
 await page.locator('#humanoid-propose').click();
 await expect(page.locator('#humanoid-plan-values tr')).toHaveCount(33);
 await page.getByLabel('Confirm password for humanoid motion').fill(process.env.ROBOT_TEST_PASSWORD||'browser-test-operator-only');
 await page.locator('#humanoid-approve').click();
 await expect(page.locator('#humanoid-state')).toContainText('Coordinated motion running');
 await expect.poll(async()=>{
  const response=await page.request.get('/api/v1/humanoid/state');
  expect(response.status()).toBe(200);
  const observed=await response.json();
  expect(observed.physical_actuation).toBe(false);
  return observed.positions.left_arm_elbow;
 }).toBeGreaterThan(0);
 await page.locator('#humanoid-stop').click();
 await expect(page.locator('#humanoid-state')).toContainText('Stopped');
 const stopped=await (await page.request.get('/api/v1/humanoid/state')).json();
 expect(stopped.active_command).toBeNull();
 expect(stopped.pending).toBeNull();
 expect(stopped.history.at(-1).state).toBe('cancelled');
 expect(Object.values(stopped.velocities).every(value=>value===0)).toBe(true);
 await expect(page.locator('#humanoid-password')).toHaveValue('');
});
