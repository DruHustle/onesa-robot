import {test, expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

async function fixture(page, authenticated = true) {
  const mutations = [], errors = [], models = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {if (request.url().endsWith('.glb')) models.push(request.url());});
  const catalog = {version:'1.0.0', mode:'simulation', physical_actuation:false, assets:[], robots:[{id:'sim-inspector',name:'Simulated inspection robot'}], skills:[], scenarios:[{id:'normal',label:'Normal readings',source:'synthetic fixture'}], asset_context_import:'disabled'};
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let status = 200, body = {};
    if (request.method() === 'POST' && !path.endsWith('/heartbeat')) mutations.push(path);
    if (!authenticated && !path.endsWith('/ready')) {status = 401; body = {detail:'Sign in required'};}
    else if (path.endsWith('/auth/me')) body = {csrf:'synthetic-reference-session', name:'synthetic-owner', role:'operator'};
    else if (path.endsWith('/auth/logout')) {authenticated = false; body = {status:'signed_out'};}
    else if (path.endsWith('/stop')) body = {status:'stopped'};
    else if (path.endsWith('/status')) body = {mode:'simulation', physical_actuation:false, stopped:true, position_radians:0, active_task:null, sampled_at:Date.now()/1000, capabilities:{edge_telemetry:'not_connected'}, deployment_environment:'local'};
    else if (path.endsWith('/missions/catalog')) body = catalog;
    else if (path.endsWith('/missions')) body = {missions:[]};
    else if (path.endsWith('/tasks')) body = {tasks:[]};
    else if (path.endsWith('/ready')) body = {status:'ready'};
    await route.fulfill({status, contentType:'application/json', body:JSON.stringify(body)});
  });
  await page.goto('/console');
  if (authenticated) {
    await expect(page.getByRole('button', {name:'Sign out', exact:true})).toBeVisible();
    await page.locator('#nvidia-library').scrollIntoViewIfNeeded();
  }
  return {mutations, errors, models};
}

async function settled(page) {
  await expect(page.locator('#nvidia-library')).toHaveAttribute('data-view-state', /^(ready|fallback)$/, {timeout:20_000});
  const state = await page.locator('#nvidia-library').getAttribute('data-view-state');
  if (state === 'ready') await expect(page.locator('#nvidia-stage canvas')).toBeVisible();
  else await expect(page.locator('#nvidia-preview')).toBeVisible();
  return state;
}

test('automatic local references preserve source, controls, keyboard and system appearance', async ({page}) => {
  const fixtureState = await fixture(page);
  for (const [id, name] of [['nvidia-transfer-conveyor','Transfer conveyor'], ['nvidia-mobile-cart','Mobile cart'], ['nvidia-storage-rack','Warehouse rack']]) {
    await page.locator('#nvidia-select').selectOption(id);
    const state = await settled(page);
    if (process.env.ROBOT_REQUIRE_WEBGL === 'true') expect(state, 'The supplemental rendering probe requires real WebGL').toBe('ready');
    await expect(page.locator('#nvidia-attribution')).toContainText(`${name} by NVIDIA`);
    await expect(page.locator('#nvidia-license')).toHaveAttribute('href', `/models/nvidia/${id}/LICENSE.txt`);
    if (state === 'ready') {
      await page.locator('#nvidia-stage canvas').focus();
      await page.keyboard.press('ArrowRight');
      await page.locator('#nvidia-fit').click();
      await expect(page.locator('#nvidia-dimensions')).toContainText('Reference size:');
    }
    await page.locator('#nvidia-select').focus();
    await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});
    await expect(page.locator('#nvidia-select')).toBeFocused();
    await expect(page.locator('#nvidia-select')).toHaveValue(id);
    await page.emulateMedia({colorScheme:'light'});
  }
  expect(fixtureState.models.length).toBeGreaterThanOrEqual(3);
  expect(fixtureState.mutations).toEqual([]); expect(fixtureState.errors).toEqual([]);
  await page.setViewportSize({width:320,height:860});
  await page.locator('#nvidia-library').scrollIntoViewIfNeeded();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect((await new AxeBuilder({page}).include('#nvidia-library').withTags(['wcag2a','wcag2aa','wcag21aa']).analyze()).violations).toEqual([]);
});

test('failed and corrupted downloads retain a visible preview and explicit retry', async ({page}) => {
  let failure = 'unavailable';
  await page.route('**/*.glb', async route => {
    if (failure === 'unavailable') await route.fulfill({status:503, body:''});
    else if (failure === 'corrupt') await route.fulfill({status:200, contentType:'model/gltf-binary', body:Buffer.from('corrupt reference')});
    else await route.continue();
  });
  const state = await fixture(page);
  await expect(page.locator('#nvidia-library')).toHaveAttribute('data-view-state','fallback');
  await expect(page.locator('#nvidia-preview')).toBeVisible();
  await expect(page.locator('#nvidia-retry')).toBeVisible();
  failure = 'corrupt'; await page.locator('#nvidia-retry').click();
  await expect(page.locator('#nvidia-library')).toHaveAttribute('data-view-state','fallback');
  failure = ''; await page.locator('#nvidia-retry').click();
  await settled(page);
  expect(state.mutations).toEqual([]); expect(state.errors).toEqual([]);
});

test('missing WebGL shows the model preview without interfering with Stop or sign-out', async ({page}) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, ...args) {return type.startsWith('webgl') ? null : original.call(this, type, ...args);};
  });
  const state = await fixture(page);
  await expect(page.locator('#nvidia-library')).toHaveAttribute('data-view-state','fallback', {timeout:20_000});
  await expect(page.locator('#nvidia-preview')).toBeVisible();
  await page.getByRole('button',{name:'Stop simulation',exact:true}).click();
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  await expect(page.locator('#nvidia-library')).toBeHidden();
  await expect(page.locator('#nvidia-stage canvas')).toHaveCount(0);
  expect(state.mutations).toEqual(['/api/v1/stop','/api/v1/auth/logout']); expect(state.errors).toEqual([]);
});

test('anonymous console and script-free product do not request the 3D engine or models', async ({page}) => {
  const state = await fixture(page, false), engines = [];
  page.on('request', request => {if (request.url().includes('/vendor/three/')) engines.push(request.url());});
  await expect(page.locator('#operator')).toBeHidden();
  await page.goto('/');
  await expect(page.locator('script')).toHaveCount(0);
  expect(state.models).toEqual([]); expect(engines).toEqual([]); expect(state.mutations).toEqual([]);
});

test('sign-out cancels a pending reference load and discards its late completion', async ({page}) => {
  let release, entered;
  const held = new Promise(resolve => {release = resolve;}), requested = new Promise(resolve => {entered = resolve;});
  await page.route('**/*.glb', async route => {entered(); await held; await route.continue().catch(() => {});});
  const state = await fixture(page);
  await requested;
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  release();
  await expect(page.locator('#nvidia-library')).toBeHidden();
  await expect(page.locator('#nvidia-stage canvas')).toHaveCount(0);
  expect(state.mutations).toEqual(['/api/v1/auth/logout']); expect(state.errors).toEqual([]);
});

test('changing selection cancels stale model downloads without replacing the newer view', async ({page}) => {
  let release, entered;
  const held = new Promise(resolve => {release = resolve;}), requested = new Promise(resolve => {entered = resolve;});
  await page.route('**/nvidia-transfer-conveyor/*.glb', async route => {entered(); await held; await route.continue().catch(() => {});});
  const state = await fixture(page);
  await requested;
  await page.locator('#nvidia-select').selectOption('nvidia-mobile-cart');
  await settled(page); release();
  await expect(page.locator('#nvidia-select')).toHaveValue('nvidia-mobile-cart');
  await expect(page.locator('#nvidia-attribution')).toContainText('Mobile cart by NVIDIA');
  await expect(page.locator('#nvidia-stage canvas[aria-label^="Transfer conveyor"]')).toHaveCount(0);
  expect(state.mutations).toEqual([]); expect(state.errors).toEqual([]);
});

test('graphics context loss retains preview, independent Stop and a recoverable view', async ({page}) => {
  const state = await fixture(page);
  const result = await settled(page);
  if (result === 'ready') {
    await page.locator('#nvidia-stage canvas').evaluate(canvas => canvas.dispatchEvent(new Event('webglcontextlost', {cancelable:true})));
    await expect(page.locator('#nvidia-status')).toContainText('graphics were interrupted');
  }
  await expect(page.locator('#nvidia-preview')).toBeVisible();
  await expect(page.locator('#nvidia-retry')).toBeVisible();
  await page.getByRole('button',{name:'Stop simulation',exact:true}).click();
  await page.locator('#nvidia-library').scrollIntoViewIfNeeded();
  await page.locator('#nvidia-retry').click();
  const recovered = await settled(page);
  if (process.env.ROBOT_REQUIRE_WEBGL === 'true') expect(recovered).toBe('ready');
  expect(state.mutations).toEqual(['/api/v1/stop']); expect(state.errors).toEqual([]);
});
