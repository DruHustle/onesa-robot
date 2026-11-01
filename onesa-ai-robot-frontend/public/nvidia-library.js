import catalog from './nvidia-catalog.js';
import {fetchReference} from './nvidia-assets.js';

// Visual reference state is deliberately independent of simulator observation,
// approval, heartbeat and Stop. No Robot API or operator credential is imported.
const $ = id => document.getElementById(id);
const panel = $('nvidia-library');
const stage = $('nvidia-stage');
const image = $('nvidia-preview');
const status = $('nvidia-status');
const select = $('nvidia-select');
const retry = $('nvidia-retry');
const fit = $('nvidia-fit');
const dark = matchMedia('(prefers-color-scheme: dark)');
let selected = catalog.assets[0], visible = false, epoch = 0, request = null;
let graphics = null, state = 'idle', frame = 0, disposed = false;
const authenticated = () => document.body.dataset.session === 'authenticated';
const active = () => authenticated() && visible && !document.hidden && !disposed;

for (const asset of catalog.assets) {
  const option = document.createElement('option');
  option.value = asset.id; option.textContent = asset.name; select.append(option);
}

function metadata() {
  image.src = `/models/nvidia/${selected.thumbnail.path}`;
  image.alt = `${selected.name} — rendered preview of the NVIDIA reference model`;
  $('nvidia-attribution').textContent = selected.attribution;
  $('nvidia-source').href = selected.sourceUrl;
  $('nvidia-license').href = `/models/nvidia/${selected.licenseFile.path}`;
  $('nvidia-provenance').href = `/models/nvidia/${selected.sourceFile.path}`;
  $('nvidia-dimensions').textContent = 'Visual reference · dimensions in metres';
}

function disposeGraphics(current) {
  current.canvas.removeEventListener('webglcontextlost', lostContext);
  current.controls?.dispose();
  const geometries = new Set(), materials = new Set();
  current.scene?.traverse(object => {
    if (object.geometry) geometries.add(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) if (material) materials.add(material);
  });
  geometries.forEach(geometry => geometry.dispose());
  materials.forEach(material => material.dispose());
  current.renderer.dispose();
  current.renderer.forceContextLoss();
  current.canvas.remove();
}

function release() {
  cancelAnimationFrame(frame); frame = 0;
  if (!graphics) return;
  const current = graphics; graphics = null;
  disposeGraphics(current);
}

function cancel() {
  epoch++;
  request?.abort(); request = null;
}

function fallback(message) {
  release(); state = 'fallback';
  image.hidden = false; retry.hidden = false; fit.disabled = true;
  status.textContent = message;
  panel.dataset.viewState = state;
}

function lostContext(event) {
  event.preventDefault(); cancel();
  fallback('3D graphics were interrupted. The model preview remains available. Retry 3D when ready.');
}

function render() {
  if (!graphics || !active() || frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    if (!graphics || !active()) return;
    const {renderer, camera, scene} = graphics;
    const width = stage.clientWidth, height = stage.clientHeight;
    if (width < 1 || height < 1) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height; camera.updateProjectionMatrix();
    scene.background.set(dark.matches ? '#10232b' : '#edf4f5');
    try { renderer.render(scene, camera); }
    catch { fallback('3D graphics are unavailable. The rendered model preview remains available.'); }
  });
}

function fitView() {
  if (!graphics) return;
  const {camera, controls, span} = graphics;
  const aspect = Math.max(0.1, stage.clientWidth / Math.max(stage.clientHeight, 1));
  const distance = span * 1.55 * Math.max(1, 1 / aspect);
  camera.position.set(distance * 0.82, distance * 0.58, distance);
  controls.target.set(0, graphics.height / 2, 0);
  camera.near = Math.max(0.001, span / 1000); camera.far = span * 100;
  controls.minDistance = span * 0.3; controls.maxDistance = span * 10;
  controls.update(); render();
}

async function load() {
  if (!active() || state === 'loading' || state === 'ready' || state === 'fallback') return;
  cancel(); release();
  const current = epoch, asset = selected;
  const controller = new AbortController(); request = controller;
  const timeout = setTimeout(() => controller.abort(), 15_000);
  state = 'loading'; panel.dataset.viewState = state;
  status.textContent = 'Loading the local NVIDIA reference model…';
  image.hidden = false; retry.hidden = true; fit.disabled = true;
  let detached = null, provisional = null;
  try {
    const [THREE, {GLTFLoader}, {OrbitControls}, buffer] = await Promise.all([
      import('./vendor/three/three.module.js'),
      import('./vendor/three/GLTFLoader.js'),
      import('./vendor/three/OrbitControls.js'),
      fetchReference(asset, controller.signal),
    ]);
    if (current !== epoch || !active()) return;
    const model = await new GLTFLoader().parseAsync(buffer, '');
    detached = model.scene;
    if (current !== epoch || !active()) return;
    const bounds = new THREE.Box3().setFromObject(model.scene);
    const dimensions = bounds.getSize(new THREE.Vector3());
    const span = Math.max(dimensions.x, dimensions.y, dimensions.z);
    if (!Number.isFinite(span) || span <= 0 || span > 1000) throw new Error('Invalid reference bounds');
    const center = bounds.getCenter(new THREE.Vector3());
    model.scene.position.set(-center.x, -bounds.min.y, -center.z);
    const canvas = document.createElement('canvas');
    canvas.tabIndex = 0; canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', `${asset.name} interactive 3D reference. Drag to orbit, shift-drag to pan, scroll to zoom. Arrow keys pan while the view is focused.`);
    canvas.addEventListener('webglcontextlost', lostContext);
    const renderer = new THREE.WebGLRenderer({canvas, antialias:true, powerPreference:'low-power'});
    provisional = {renderer, canvas};
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.5));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    const scene = new THREE.Scene(); provisional.scene = scene; scene.background = new THREE.Color(); scene.add(model.scene);
    detached = null;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 3));
    const light = new THREE.DirectionalLight(0xffffff, 4); light.position.set(span, span * 2, span); scene.add(light);
    const fill = new THREE.DirectionalLight(0xc5e7ed, 2); fill.position.set(-span, span, -span); scene.add(fill);
    const grid = new THREE.GridHelper(span * 2.4, 12, 0x607d86, 0x607d86);
    grid.material.transparent = true; grid.material.opacity = 0.22; scene.add(grid);
    const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 1000);
    const controls = new OrbitControls(camera, canvas);
    provisional.controls = controls;
    controls.enableDamping = false; controls.autoRotate = false;
    controls.maxPolarAngle = Math.PI * 0.49;
    controls.listenToKeyEvents(canvas); controls.addEventListener('change', render);
    graphics = {renderer, scene, camera, canvas, controls, span, height:dimensions.y};
    provisional = null;
    stage.append(canvas); image.hidden = true;
    state = 'ready'; panel.dataset.viewState = state;
    status.textContent = '3D reference ready. Drag to orbit, shift-drag to pan; scroll to zoom. Arrow keys pan the focused view.';
    $('nvidia-dimensions').textContent = `Reference size: ${dimensions.x.toFixed(2)} × ${dimensions.y.toFixed(2)} × ${dimensions.z.toFixed(2)} m · width × height × depth`;
    fit.disabled = false; fitView();
  } catch {
    if (current === epoch && active()) fallback('The interactive model could not load. Its rendered preview and source details remain available.');
  } finally {
    clearTimeout(timeout);
    controller.abort();
    if (provisional) disposeGraphics(provisional);
    if (detached) detached.traverse(object => {
      object.geometry?.dispose();
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) material?.dispose();
    });
    if (current === epoch) request = null;
  }
}

function reconcile() {
  if (!authenticated() || disposed) {
    cancel(); release(); state = 'idle'; panel.dataset.viewState = state;
    image.hidden = false; retry.hidden = true; fit.disabled = true;
    status.textContent = 'Reference models load automatically in the operator workspace.';
    return;
  }
  if (!active()) {
    cancelAnimationFrame(frame); frame = 0;
    if (state === 'loading') {cancel(); state = 'idle'; panel.dataset.viewState = state;}
  } else {void load(); render();}
}

select.addEventListener('change', () => {
  selected = catalog.assets.find(asset => asset.id === select.value) || catalog.assets[0];
  cancel(); release(); state = 'idle'; metadata(); void load();
});
retry.addEventListener('click', () => {state = 'idle'; void load();});
fit.addEventListener('click', fitView);
dark.addEventListener('change', render);
document.addEventListener('visibilitychange', reconcile);
window.addEventListener('pagehide', () => {disposed = true; reconcile();});
window.addEventListener('pageshow', () => {disposed = false; reconcile();});
new MutationObserver(reconcile).observe(document.body, {attributes:true, attributeFilter:['data-session']});
new IntersectionObserver(entries => {visible = entries[0].isIntersecting; reconcile();}, {rootMargin:'160px'}).observe(panel);
new ResizeObserver(render).observe(stage);
metadata();
