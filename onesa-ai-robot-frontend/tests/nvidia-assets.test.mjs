import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile, mkdtemp, cp, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {verifyAssets} from '../scripts/nvidia-assets.mjs';
import {fetchReference, validateStaticGlb} from '../public/nvidia-assets.js';

test('all three NVIDIA references retain exact source, model, preview and license bytes', async () => {
  const catalog = await verifyAssets();
  assert.deepEqual(catalog.assets.map(asset => asset.id), ['nvidia-transfer-conveyor', 'nvidia-mobile-cart', 'nvidia-storage-rack']);
  for (const asset of catalog.assets) {
    assert.equal(asset.visualOnly, true);
    const bytes = await readFile('dist/models/nvidia/' + asset.glb.path);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), asset.glb.sha256);
  }
  const lock = JSON.parse(await readFile('package-lock.json', 'utf8'));
  assert.equal(lock.packages['node_modules/three'].version, '0.186.1');
  assert.match(lock.packages['node_modules/three'].resolved, /^https:\/\/registry\.npmjs\.org\/three\//);
  assert.match(lock.packages['node_modules/three'].integrity, /^sha512-/);
  assert.deepEqual(await readFile('dist/vendor/three/LICENSE.txt'), await readFile('node_modules/three/LICENSE'));
  // Every packaged import resolves within the same reviewed engine directory.
  for (const module of ['three.core.js', 'three.module.js', 'GLTFLoader.js', 'BufferGeometryUtils.js', 'SkeletonUtils.js', 'OrbitControls.js']) {
    const text = await readFile('dist/vendor/three/' + module, 'utf8');
    for (const match of text.matchAll(/^(?:import|export)[\s\S]*?\bfrom\s*['"]([^'"]+)['"]/gm)) {
      assert.match(match[1], /^\.\/[A-Za-z.]+\.js$/);
      await readFile('dist/vendor/three/' + match[1]);
    }
  }
});

test('packaging rejects altered local model bytes before producing a release', async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'robot-nvidia-'));
  try {
    await cp('public/models/nvidia', temporary, {recursive:true});
    const catalog = JSON.parse(await readFile(join(temporary, 'manifest.json'), 'utf8'));
    const target = join(temporary, catalog.assets[0].glb.path);
    const bytes = await readFile(target); bytes[bytes.length - 1] ^= 1; await writeFile(target, bytes);
    await assert.rejects(verifyAssets(temporary), /integrity/);
  } finally {await rm(temporary, {recursive:true, force:true});}
});

test('verified browser downloads omit credentials and enforce byte count, hash and cancellation', async () => {
  const original = globalThis.fetch;
  const catalog = await verifyAssets(), asset = catalog.assets[1];
  const bytes = await readFile('public/models/nvidia/' + asset.glb.path);
  let request;
  try {
    globalThis.fetch = async (path, options) => {request = {path, options}; return new Response(bytes);};
    const controller = new AbortController();
    assert.equal((await fetchReference(asset, controller.signal)).byteLength, bytes.length);
    assert.equal(request.path, '/models/nvidia/' + asset.glb.path);
    assert.equal(request.options.credentials, 'omit'); assert.equal(request.options.redirect, 'error');
    globalThis.fetch = async () => new Response(bytes.subarray(0, bytes.length - 1));
    await assert.rejects(fetchReference(asset, controller.signal), /incomplete/);
    globalThis.fetch = async () => new Response(Buffer.concat([bytes, Buffer.of(0)]));
    await assert.rejects(fetchReference(asset, controller.signal), /reviewed size/);
    const corrupt = Buffer.from(bytes); corrupt[corrupt.length - 1] ^= 1;
    globalThis.fetch = async () => new Response(corrupt);
    await assert.rejects(fetchReference(asset, controller.signal), /integrity/);
    globalThis.fetch = async () => new Response(bytes);
    controller.abort(); await assert.rejects(fetchReference(asset, controller.signal), {name:'AbortError'});
  } finally {globalThis.fetch = original;}
});

function glb(model) {
  const json = JSON.stringify(model), padding = (4 - new TextEncoder().encode(json).byteLength % 4) % 4;
  const text = new TextEncoder().encode(json + ' '.repeat(padding));
  const buffer = new ArrayBuffer(20 + text.byteLength), view = new DataView(buffer);
  [0x46546c67, 2, buffer.byteLength, text.byteLength, 0x4e4f534a].forEach((value, index) => view.setUint32(index * 4, value, true));
  new Uint8Array(buffer, 20).set(text); return buffer;
}

test('active, external, skinned and compressed model features are rejected before the renderer parses them', () => {
  const base = {asset:{version:'2.0'}, buffers:[{byteLength:4}], meshes:[{}]};
  for (const model of [
    {...base, buffers:[{uri:'https://example.invalid/model.bin'}]},
    {...base, images:[{uri:'data:image/png;base64,AA=='}]},
    {...base, animations:[{}]}, {...base, skins:[{}]},
    {...base, extensionsUsed:['KHR_draco_mesh_compression']},
    {...base, nodes:[{extensions:{unreviewed:{}}}]},
  ]) assert.throws(() => validateStaticGlb(glb(model)));
  assert.throws(() => validateStaticGlb(new ArrayBuffer(0)));
});
