import {readFile, mkdir, writeFile, cp} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {join} from 'node:path';
import {validateStaticGlb} from '../public/nvidia-assets.js';

export async function verifyAssets(root = 'public/models/nvidia') {
  const manifest = JSON.parse(await readFile(join(root, 'manifest.json'), 'utf8'));
  if (manifest.schemaVersion !== 1 || manifest.catalogId !== 'onesa-nvidia-simready-reference-v1' ||
      manifest.assets.length !== 3) throw new Error('Unexpected NVIDIA reference catalog');
  const seen = new Set();
  for (const asset of manifest.assets) {
    if (seen.has(asset.id) || !/^nvidia-[a-z-]+$/.test(asset.id) ||
        asset.author !== 'NVIDIA' || asset.license !== 'CC-BY-4.0' || asset.visualOnly !== true ||
        asset.sourceRevision !== '4763fbfaa4f8e97658e68b57d01b4c86659e08b5' ||
        asset.unit !== 'm' || asset.upAxis !== 'Y' || asset.glb.byteLength > 2_000_000 ||
        !asset.attribution.includes('Converted and optimized') ||
        asset.sourceUrl !== `https://huggingface.co/datasets/nvidia/simready-assets/tree/${asset.sourceRevision}/${asset.sourceAssetId}`)
      throw new Error('Unreviewed reference metadata');
    seen.add(asset.id);
    for (const [key, file] of Object.entries({glb:asset.glb, thumbnail:asset.thumbnail, licenseFile:asset.licenseFile, sourceFile:asset.sourceFile})) {
      if (!file.path.startsWith(asset.id + '/') || file.path.includes('..') || !/^[a-zA-Z0-9_./-]+$/.test(file.path)) throw new Error('Invalid reference path');
      const bytes = await readFile(join(root, file.path));
      if (bytes.length !== file.byteLength || createHash('sha256').update(bytes).digest('hex') !== file.sha256) throw new Error(`Reference integrity failed: ${file.path}`);
      if (key === 'glb') validateStaticGlb(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
      if (key === 'sourceFile') {
        const provenance = JSON.parse(bytes);
        if (provenance.revision !== asset.sourceRevision || provenance.author !== 'NVIDIA' ||
            provenance.license !== asset.license || provenance.dataset !== 'nvidia/simready-assets') throw new Error('Reference source mismatch');
      }
    }
  }
  return manifest;
}

export async function packageAssets() {
  const manifest = await verifyAssets();
  await writeFile('dist/nvidia-catalog.js', `// Generated from the verified local NVIDIA catalog.\nexport default ${JSON.stringify(manifest)};\n`);
  const packageInfo = JSON.parse(await readFile('node_modules/three/package.json', 'utf8'));
  if (packageInfo.version !== '0.186.1' || packageInfo.license !== 'MIT') throw new Error('Unreviewed Three.js engine');
  await mkdir('dist/vendor/three', {recursive:true});
  const modules = [
    ['build/three.core.js', 'three.core.js'],
    ['build/three.module.js', 'three.module.js'],
    ['examples/jsm/loaders/GLTFLoader.js', 'GLTFLoader.js'],
    ['examples/jsm/utils/BufferGeometryUtils.js', 'BufferGeometryUtils.js'],
    ['examples/jsm/utils/SkeletonUtils.js', 'SkeletonUtils.js'],
    ['examples/jsm/controls/OrbitControls.js', 'OrbitControls.js'],
  ];
  for (const [source, target] of modules) {
    const text = (await readFile(`node_modules/three/${source}`, 'utf8'))
      .replaceAll("from 'three'", "from './three.module.js'")
      .replaceAll("from '../utils/BufferGeometryUtils.js'", "from './BufferGeometryUtils.js'")
      .replaceAll("from '../utils/SkeletonUtils.js'", "from './SkeletonUtils.js'");
    await writeFile(`dist/vendor/three/${target}`, text);
  }
  await cp('node_modules/three/LICENSE', 'dist/vendor/three/LICENSE.txt');
}
