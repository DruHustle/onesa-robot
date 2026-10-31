/** Local visual-only assets. This module has no operator, task or device API. */
export function validateStaticGlb(buffer) {
  const view = new DataView(buffer);
  if (buffer.byteLength < 28 || view.getUint32(0, true) !== 0x46546c67 ||
      view.getUint32(4, true) !== 2 || view.getUint32(8, true) !== buffer.byteLength ||
      view.getUint32(16, true) !== 0x4e4f534a) throw new Error('Invalid reference model');
  const length = view.getUint32(12, true);
  if (length > buffer.byteLength - 20 || length % 4) throw new Error('Invalid reference model');
  const model = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 20, length)));
  if (model.asset?.version !== '2.0' || model.extensionsRequired?.length ||
      model.extensionsUsed?.length || model.animations?.length || model.skins?.length ||
      model.images?.length || model.buffers?.length !== 1 || model.buffers[0].uri ||
      !model.meshes?.length) throw new Error('Reference model must be self-contained and static');
  // Fixed reviewed derivatives never need a URI, plugin or active resource.
  function inspect(value) {
    if (!value || typeof value !== 'object') return;
    for (const [key, child] of Object.entries(value)) {
      if (key === 'uri' || key === 'extensions') throw new Error('External reference resources are disabled');
      inspect(child);
    }
  }
  inspect(model);
  return model;
}

export async function fetchReference(asset, signal) {
  const path = `/models/nvidia/${asset.glb.path}`;
  const response = await fetch(path, {signal, credentials:'omit', redirect:'error'});
  if (!response.ok) throw new Error('Reference download failed');
  const declared = response.headers.get('content-length');
  if (declared !== null && Number(declared) > asset.glb.byteLength) throw new Error('Reference exceeds its reviewed size');
  const reader = response.body?.getReader();
  if (!reader) throw new Error('Reference download unavailable');
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > asset.glb.byteLength) throw new Error('Reference exceeds its reviewed size');
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally { reader.releaseLock(); }
  if (size !== asset.glb.byteLength) throw new Error('Reference download is incomplete');
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.byteLength;}
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  const hash = Array.from(digest, value => value.toString(16).padStart(2, '0')).join('');
  if (hash !== asset.glb.sha256) throw new Error('Reference integrity check failed');
  validateStaticGlb(bytes.buffer);
  signal.throwIfAborted();
  return bytes.buffer;
}
