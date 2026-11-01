import {test} from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {setTimeout as sleep} from 'node:timers/promises';
import {readFile} from 'node:fs/promises';
test('proxy preserves auth boundaries, strips unrelated secrets and rejects open routes',async()=>{
 let received;
 const upstream=http.createServer((req,res)=>{received=req.headers;res.setHeader('Content-Type','application/json');res.setHeader('Set-Cookie',['robot_session=fixture; Path=/api/v1; HttpOnly','unrelated=secret']);res.end('{}');});
 await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
 const child=spawn(process.execPath,['--input-type=module','-e',"import http from 'node:http';import gateway from './api/gateway.mjs';const server=http.createServer(gateway);server.listen(3439,'127.0.0.1');process.on('SIGTERM',()=>server.close(()=>process.exit(0)));"],{env:{...process.env,PORT:'3439',ROBOT_API_URL:`http://127.0.0.1:${upstream.address().port}`},stdio:'ignore'});
 try {
  let ready=false;for(let i=0;i<40;i++){try{ready=(await fetch('http://127.0.0.1:3439/')).ok;}catch{}if(ready)break;await sleep(50);}assert.ok(ready);
  const response=await fetch('http://127.0.0.1:3439/api/v1/status',{headers:{cookie:'robot_session=fixture; vercel-bypass=private','x-vercel-protection-bypass':'private','x-forwarded-for':'spoofed'}});
  assert.equal(response.status,200);assert.equal(received.cookie,'robot_session=fixture');assert.equal(received['x-vercel-protection-bypass'],undefined);assert.equal(received['x-forwarded-for'],undefined);assert.equal(response.headers.getSetCookie().length,1);
  assert.equal((await fetch('http://127.0.0.1:3439/api/v1/status?url=https://attacker.invalid')).status,404);
  assert.equal((await fetch('http://127.0.0.1:3439/api/v1/private')).status,404);
  // Actual Vercel route metadata must support nested auth/task paths.
  assert.equal((await fetch('http://127.0.0.1:3439/api/v1/ready?__onesa_route=ready')).status,200);
  assert.equal((await fetch('http://127.0.0.1:3439/api/v1/auth/me?__onesa_route=auth%2Fme')).status,200);
  for(const suffix of ['', '/allow', '/block']){
    const path='/api/v1/admin/physical-operation'+suffix;
    const response=await fetch('http://127.0.0.1:3439'+path+'?__onesa_route='+encodeURIComponent(path.slice('/api/v1/'.length)),suffix?{method:'POST',headers:{Origin:'https://synthetic-owner.invalid','X-CSRF-Token':'synthetic-csrf',Cookie:'robot_session=fixture'},body:'{}'}:{});
    assert.equal(response.status,200);
    if(suffix){assert.equal(received.origin,'https://synthetic-owner.invalid');assert.equal(received['x-csrf-token'],'synthetic-csrf');assert.equal(received.cookie,'robot_session=fixture');}
  }
  for(const path of ['/api/v1/admin','/api/v1/admin/physical-operation/enable','/api/v1/admin/physical-operation/allow?role=administrator'])assert.equal((await fetch('http://127.0.0.1:3439'+path)).status,404);
  for(const suffix of ['model','model.urdf','state','heartbeat','proposals','proposals/00000000-0000-0000-0000-000000000000/approve']){
    const path='/api/v1/humanoid/'+suffix;
    const mutation=suffix==='heartbeat'||suffix.startsWith('proposals');
    const response=await fetch('http://127.0.0.1:3439'+path+'?__onesa_route='+encodeURIComponent(path.slice('/api/v1/'.length)),{headers:{Origin:'https://synthetic-owner.invalid','X-CSRF-Token':'synthetic-csrf',Cookie:'robot_session=fixture'},...(mutation?{method:'POST',body:'{}'}:{})});
    assert.equal(response.status,200,path);
    assert.equal(received.cookie,'robot_session=fixture');
    assert.equal(received['x-csrf-token'],'synthetic-csrf');
    if(suffix==='model.urdf')assert.equal(response.headers.get('content-disposition'),'attachment; filename="onesa-humanoid-reference.urdf"');
  }
  for(const path of ['/api/v1/humanoid','/api/v1/humanoid/actuate','/api/v1/humanoid/model.xml','/api/v1/humanoid/proposals/not-a-uuid/approve','/api/v1/humanoid/model?download=private'])assert.equal((await fetch('http://127.0.0.1:3439'+path)).status,404);
  assert.equal((await fetch('http://127.0.0.1:3439/humanoid.js')).status,200);
  const approval='/api/v1/tasks/00000000-0000-0000-0000-000000000000/approve';
  assert.equal((await fetch('http://127.0.0.1:3439'+approval+'?__onesa_route='+encodeURIComponent(approval.slice('/api/v1/'.length)),{method:'POST',body:'{}'})).status,200);
  for (const route of [
    '/api/v1/status?__onesa_route=tasks',
    '/api/v1/status?__onesa_route=status&__onesa_route=status',
    '/api/v1/status?__onesa_route=status&url=https://attacker.invalid',
    '/api/gateway?__onesa_route=status',
    '/api/v1/private?__onesa_route=private'
  ]) assert.equal((await fetch('http://127.0.0.1:3439'+route)).status,404);

  assert.equal((await fetch('http://127.0.0.1:3439/api/v1/stop',{method:'POST',body:'x'.repeat(16385)})).status,413);
  assert.equal((await fetch('http://127.0.0.1:3439/.env')).status,404);
  const privacy=await fetch('http://127.0.0.1:3439/privacy');
  assert.equal(privacy.status,200);
  const privacyHTML=await privacy.text();
  assert.match(privacyHTML,/<h1>Development privacy<\/h1>/);
  assert.match(privacyHTML,/protected software development and testing by authorized operators/);
  assert.match(privacyHTML,/Oracle Cloud Infrastructure hosts the protected API/);
  assert.match(privacyHTML,/Cloudflare provides HTTPS ingress/);
  assert.match(privacyHTML,/Encrypted recovery copies are kept in Onesa's existing private Neon object storage/);
  assert.doesNotMatch(privacyHTML,/\.neon\.tech|S3_BUCKET|S3_ENDPOINT/);
  assert.match(privacyHTML,/Render is a former hosting provider/);
  assert.match(privacyHTML,/remain awaiting the owner's recorded decisions/);
  assert.doesNotMatch(privacyHTML,/<script\b/i);
  assert.match(await (await fetch('http://127.0.0.1:3439/')).text(),/href="\/privacy">Development privacy/);
  for(const width of [640,1280]){
    const image=await fetch('http://127.0.0.1:3439/images/robot-concept-'+width+'.webp');
    assert.equal(image.status,200);assert.equal(image.headers.get('content-type'),'image/webp');
    assert.deepEqual(Buffer.from(await image.arrayBuffer()),await readFile('dist/images/robot-concept-'+width+'.webp'));
  }
  for(const mode of ['light','dark']){
    const image=await fetch('http://127.0.0.1:3439/images/robot-workspace-example-'+mode+'.png');
    assert.equal(image.status,200);assert.equal(image.headers.get('content-type'),'image/png');
    assert.deepEqual(Buffer.from(await image.arrayBuffer()),await readFile('dist/images/robot-workspace-example-'+mode+'.png'));
  }
  assert.equal((await fetch('http://127.0.0.1:3439/images/robot-workspace-example-private.png')).status,404);
  const catalog=JSON.parse(await readFile('dist/models/nvidia/manifest.json','utf8'));
  for(const asset of catalog.assets){
    for(const [file,type] of [[asset.glb,'model/gltf-binary'],[asset.thumbnail,'image/png'],[asset.licenseFile,'text/plain; charset=utf-8'],[asset.sourceFile,'application/json']]){
      const model=await fetch('http://127.0.0.1:3439/models/nvidia/'+file.path);
      assert.equal(model.status,200);assert.equal(model.headers.get('content-type'),type);
      if(file.path.endsWith('.glb'))assert.equal(model.headers.get('cache-control'),'public, max-age=31536000, immutable');
      assert.deepEqual(Buffer.from(await model.arrayBuffer()),await readFile('dist/models/nvidia/'+file.path));
    }
  }
  for(const path of ['/vendor/three/GLTFLoader.js','/vendor/three/SkeletonUtils.js','/nvidia-library.js','/nvidia-catalog.js'])assert.equal((await fetch('http://127.0.0.1:3439'+path)).status,200);
  for(const path of ['/models/nvidia/private.glb','/models/nvidia/../../.env','/vendor/three/../../.env','/models%2fnvidia/private.glb'])assert.equal((await fetch('http://127.0.0.1:3439'+path)).status,404);
  for(const path of ['/images/robot-concept-999.webp','/images/../.env','/images%2frobot-concept-640.webp','/images/robot-concept-640.webp/..%2f..%2f.env'])assert.equal((await fetch('http://127.0.0.1:3439'+path)).status,404);
  assert.equal((await fetch('http://127.0.0.1:3439/')).headers.get('content-security-policy').includes("frame-ancestors 'none'"),true);
 } finally {child.kill('SIGTERM');await new Promise(resolve=>child.once('exit',resolve));await new Promise(resolve=>upstream.close(resolve));}
});
