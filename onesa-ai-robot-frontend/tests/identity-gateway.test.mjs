import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';

test('identity callbacks retain only bound state cookies, query keys and local redirects',async()=>{
 let received,redirect='/console';
 const upstream=http.createServer((req,res)=>{
  received={url:req.url,headers:req.headers};
  res.setHeader('Set-Cookie',['robot_session=synthetic; HttpOnly; Path=/api/v1','robot_identity=synthetic; HttpOnly; Path=/api/v1/auth/identity','unrelated=private']);
  if(req.url.startsWith('/api/v1/auth/identity/callback'))res.writeHead(303,{Location:redirect}).end();
  else res.writeHead(200,{'Content-Type':'application/json'}).end('{}');
 });
 await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
 const child=spawn(process.execPath,['--input-type=module','-e',"import http from 'node:http';import gateway from './api/gateway.mjs';const server=http.createServer(gateway);server.listen(0,'127.0.0.1',()=>console.log(server.address().port));process.on('SIGTERM',()=>server.close(()=>process.exit(0)));"],{env:{...process.env,ROBOT_API_URL:`http://127.0.0.1:${upstream.address().port}`},stdio:['ignore','pipe','pipe']});
 try{
  const [chunk]=await once(child.stdout,'data');
  const port=Number(chunk.toString().trim());assert.ok(port>0);
  const origin=`http://127.0.0.1:${port}`;
  const headers={Cookie:'robot_session=synthetic; robot_identity=browser-proof; unrelated=private','X-Forwarded-For':'untrusted','X-Vercel-Protection-Bypass':'private'};
  const callback='/api/v1/auth/identity/callback?code=synthetic-code&state=synthetic-state&iss=https%3A%2F%2Faccount.example.test%2Fapi%2Fv1%2Fidentity';
  const response=await fetch(origin+callback,{headers,redirect:'manual'});
  assert.equal(response.status,303);assert.equal(response.headers.get('location'),'/console');
  assert.equal(response.headers.getSetCookie().length,2);
  assert.equal(received.url,callback);
  assert.equal(received.headers.cookie,'robot_session=synthetic; robot_identity=browser-proof');
  assert.equal(received.headers['x-forwarded-for'],undefined);
  assert.equal(received.headers['x-vercel-protection-bypass'],undefined);
  for(const extra of ['&state=duplicate','&return_to=https://attacker.example','&url=https://attacker.example']){
   assert.equal((await fetch(origin+callback+extra,{headers,redirect:'manual'})).status,404);
  }
  redirect='https://attacker.example/steal';
  assert.equal((await fetch(origin+callback,{headers,redirect:'manual'})).status,502);
  redirect='//attacker.example/steal';
  assert.equal((await fetch(origin+callback,{headers,redirect:'manual'})).status,502);
  redirect='/console?identity=access';
  assert.equal((await fetch(origin+callback,{headers,redirect:'manual'})).headers.get('location'),redirect);
  const ordinary=await fetch(origin+'/api/v1/status',{headers});
  assert.equal(ordinary.status,200);assert.equal(ordinary.headers.getSetCookie().length,1);
  assert.equal(received.headers.cookie,'robot_session=synthetic');
  assert.equal((await fetch(origin+'/api/v1/status?code=secret',{headers})).status,404);
  assert.equal((await fetch(origin+'/api/v1/auth/identity/start?return_to=evil',{method:'POST',headers,body:'{}'})).status,404);
 }finally{
  child.kill('SIGTERM');await once(child,'exit');
  await new Promise(resolve=>upstream.close(resolve));
 }
});
