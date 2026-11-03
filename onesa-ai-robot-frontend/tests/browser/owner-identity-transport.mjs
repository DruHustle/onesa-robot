// Synthetic browser transport only. No request can leave the loopback fixture.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

export const robotOrigin='https://onesa-ai-robot-git-staging-fixture.vercel.app';
export const portalOrigin='https://onesa-company-portal-staging-fixture.vercel.app';
const hosts=new Set([robotOrigin,portalOrigin].map(value=>new URL(value).hostname));

export async function identityTransport(){
 const directory=await mkdtemp(join(tmpdir(),'onesa-identity-transport-'));
 const sockets=new Set(),errors=[];let handler;
 const track=socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));};
 const listen=server=>new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',()=>{server.off('error',reject);resolve();});});
 let application,proxy;
 const close=async()=>{
  const servers=[application,proxy].filter(Boolean);
  const closed=servers.map(server=>new Promise(resolve=>server.close(()=>resolve())));
  for(const socket of sockets)socket.destroy();
  await Promise.all(closed);await rm(directory,{recursive:true,force:true});
 };
 try{
  const key=join(directory,'key.pem'),cert=join(directory,'cert.pem');
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=Onesa synthetic identity fixture','-addext','subjectAltName='+[...hosts].map(host=>'DNS:'+host).join(','),'-keyout',key,'-out',cert],{stdio:'ignore'});
  application=https.createServer({key:await readFile(key),cert:await readFile(cert)},async(request,response)=>{
   try{
    const url=new URL(request.url,'https://'+request.headers.host);
    assert(hosts.has(url.hostname)&&[robotOrigin,portalOrigin].includes(url.origin)&&handler);
    let length=0;const chunks=[];
    for await(const chunk of request){length+=chunk.length;assert(length<=8192);chunks.push(chunk);}
    const result=await handler({url,method:request.method,headers:request.headers,body:Buffer.concat(chunks).toString('utf8')});
    const body=Buffer.from(result.body||'');
    response.writeHead(result.status||200,{'cache-control':'no-store','content-length':body.length,...result.headers});response.end(body);
   }catch(error){errors.push(error);response.writeHead(500,{'content-type':'text/plain'});response.end('Synthetic fixture refused request');}
  });
  application.requestTimeout=5000;application.headersTimeout=5000;
  application.on('connection',track);application.on('tlsClientError',()=>{});
  await listen(application);
  proxy=http.createServer((_request,response)=>{response.writeHead(403);response.end();});
  proxy.on('connection',track);
  proxy.on('connect',(request,client,head)=>{
   if(![...hosts].some(host=>request.url===host+':443')){client.end('HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');return;}
   // This fixed loopback destination is the only connection the proxy can open.
   const upstream=net.connect(application.address().port,'127.0.0.1');track(upstream);
   upstream.once('connect',()=>{client.write('HTTP/1.1 200 Connection Established\r\n\r\n');if(head.length)upstream.write(head);upstream.pipe(client);client.pipe(upstream);});
   upstream.on('error',()=>client.destroy());client.on('error',()=>upstream.destroy());client.on('close',()=>upstream.destroy());
  });
  await listen(proxy);
  return {proxy:'http://127.0.0.1:'+proxy.address().port,setHandler(value){assert(typeof value==='function');handler=value;},assertHealthy(){assert.deepEqual(errors,[]);},close};
 }catch(error){await close();throw error;}
}
