import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {access,copyFile,mkdir,mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,join} from 'node:path';

test('API function package serves readiness and anonymous auth without static output', {timeout:20000}, async()=>{
 const directory=await mkdtemp(join(tmpdir(),'onesa-robot-gateway-package-'));
 const observed=[];
 const responses=new Map([
  ['/api/v1/health',[200,{status:'live',mode:'simulation'}]],
  ['/api/v1/ready',[200,{status:'ready',mode:'simulation',physical_actuation:false,deployment_environment:'staging'}]],
  ['/api/v1/auth/me',[401,{detail:'Sign in required'}]],
 ]);
 const upstream=http.createServer((request,response)=>{
  observed.push({method:request.method,path:request.url});
  const [status,body]=responses.get(request.url)||[404,{detail:'Unknown synthetic route'}];
  response.writeHead(status,{'Content-Type':'application/json'}).end(JSON.stringify(body));
 });
 let child,startupDeadline;
 try {
  // Match the serverless failure boundary: the API code exists but the CDN's
  // generated dist/ tree, including its NVIDIA catalogue, is not in this package.
  for(const file of ['api/gateway.mjs','server.mjs','lib/tls-trust.mjs']){
   const target=join(directory,file);
   await mkdir(dirname(target),{recursive:true});
   await copyFile(new URL('../'+file,import.meta.url),target);
  }
  await assert.rejects(access(join(directory,'dist')),{code:'ENOENT'});
  upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
  child=spawn(process.execPath,['--input-type=module','-e',`
   import http from 'node:http';
   import gateway from './api/gateway.mjs';
   const server=http.createServer(gateway);
   server.listen(0,'127.0.0.1',()=>process.send({port:server.address().port}));
   process.on('SIGTERM',()=>{server.closeAllConnections();server.close(()=>process.exit(0));});
  `],{cwd:directory,env:{...process.env,ROBOT_API_URL:'http://127.0.0.1:'+upstream.address().port},stdio:['ignore','ignore','pipe','ipc']});
  let diagnostic='';
  child.stderr.on('data',chunk=>{diagnostic=(diagnostic+chunk.toString()).slice(-8192);});
  const port=await new Promise((resolve,reject)=>{
   startupDeadline=setTimeout(()=>reject(new Error('Packaged gateway did not start')),5000);
   child.once('error',reject);
   child.once('exit',(code,signal)=>reject(new Error(`Packaged gateway exited before listening (${code??signal}): ${diagnostic}`)));
   child.once('message',message=>resolve(message.port));
  }).finally(()=>clearTimeout(startupDeadline));
  const paths=[...responses.keys(),'/api/v1/health'];
  for(const path of paths){
   const response=await fetch('http://127.0.0.1:'+port+path,{signal:AbortSignal.timeout(3000)});
   const [expectedStatus,expectedBody]=responses.get(path);
   assert.equal(response.status,expectedStatus,path);
   assert.match(response.headers.get('content-type'),/^application\/json/);
   assert.deepEqual(await response.json(),expectedBody,path);
  }
  assert.deepEqual(observed,paths.map(path=>({method:'GET',path})));
  assert.equal(child.exitCode,null,'Gateway remains alive after anonymous rejection');
  await assert.rejects(access(join(directory,'dist')),{code:'ENOENT'});
 } finally {
  clearTimeout(startupDeadline);
  if(child&&child.exitCode===null&&child.signalCode===null){
   const exited=once(child,'exit');child.kill('SIGTERM');await exited;
  }
  if(upstream.listening){upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));}
  await rm(directory,{recursive:true,force:true});
 }
});
