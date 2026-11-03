import assert from 'node:assert/strict';
import http from 'node:http';
import tls from 'node:tls';
import {once} from 'node:events';
import {test} from 'node:test';
import {identityTransport,robotOrigin,portalOrigin} from './browser/owner-identity-transport.mjs';

async function tunnel(proxy,authority){
 const endpoint=new URL(proxy);
 return new Promise((resolve,reject)=>{
  const request=http.request({hostname:endpoint.hostname,port:endpoint.port,method:'CONNECT',path:authority});
  request.setTimeout(5000,()=>request.destroy(new Error('Synthetic CONNECT timeout')));
  request.on('error',reject);
  request.on('connect',(response,socket,head)=>{if(head.length)socket.unshift(head);resolve({status:response.statusCode,socket});});
  request.end();
 });
}

async function request(proxy,origin,path,headers={}){
 const hostname=new URL(origin).hostname;
 const connected=await tunnel(proxy,hostname+':443');assert.equal(connected.status,200);
 // Only the disposable loopback fixture certificate is accepted here.
 const socket=tls.connect({socket:connected.socket,servername:hostname,rejectUnauthorized:false});
 socket.setTimeout(5000,()=>socket.destroy(new Error('Synthetic HTTPS timeout')));
 try{
  await once(socket,'secureConnect');
  socket.write(`GET ${path} HTTP/1.1\r\nHost: ${hostname}\r\nConnection: close\r\n`+Object.entries(headers).map(([key,value])=>`${key}: ${value}\r\n`).join('')+'\r\n');
  const chunks=[];for await(const chunk of socket)chunks.push(chunk);
  const [head,...body]=Buffer.concat(chunks).toString('utf8').split('\r\n\r\n');
  return {head,body:body.join('\r\n\r\n')};
 }finally{socket.destroy();}
}

test('identity fixture serves real HTTPS redirects and cookie responses at both allowed origins',async()=>{
 const transport=await identityTransport();const observed=[];
 try{
  transport.setHandler(({url,headers})=>{
   observed.push(url.href);
   if(url.origin===robotOrigin)return {status:302,headers:{location:portalOrigin+'/account/sign-in/synthetic','set-cookie':'robot_session=synthetic; Path=/api/v1; Secure; HttpOnly; SameSite=Strict'}};
   assert.equal(headers.cookie,'portal_fixture=synthetic');
   return {headers:{'content-type':'application/json'},body:JSON.stringify({synthetic:true})};
  });
  const redirect=await request(transport.proxy,robotOrigin,'/api/v1/auth/identity/callback');
  assert.match(redirect.head,/^HTTP\/1\.1 302 /);
  assert.match(redirect.head,new RegExp('location: '+portalOrigin.replaceAll('.','\\.')+'/account/sign-in/synthetic','i'));
  assert.match(redirect.head,/set-cookie: robot_session=synthetic; Path=\/api\/v1; Secure; HttpOnly; SameSite=Strict/i);
  const response=await request(transport.proxy,portalOrigin,'/api/v1/account/me',{Cookie:'portal_fixture=synthetic'});
  assert.match(response.head,/^HTTP\/1\.1 200 /);assert.deepEqual(JSON.parse(response.body),{synthetic:true});
  assert.deepEqual(observed,[robotOrigin+'/api/v1/auth/identity/callback',portalOrigin+'/api/v1/account/me']);transport.assertHealthy();
 }finally{await transport.close();}
});

test('identity fixture rejects foreign hosts, suffix lookalikes and non-HTTPS ports before tunnelling',async()=>{
 const transport=await identityTransport();
 try{
  transport.setHandler(()=>{throw new Error('Rejected authority reached application');});
  for(const authority of ['example.test:443',new URL(robotOrigin).hostname+'.example.test:443',new URL(robotOrigin).hostname+':80','127.0.0.1:443']){
   const result=await tunnel(transport.proxy,authority);assert.equal(result.status,403);result.socket.destroy();
  }
  transport.assertHealthy();
 }finally{await transport.close();}
 await assert.rejects(tunnel(transport.proxy,new URL(robotOrigin).hostname+':443'),{code:'ECONNREFUSED'});
});
