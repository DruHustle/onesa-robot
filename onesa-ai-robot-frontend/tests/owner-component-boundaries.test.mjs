import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp,copyFile,symlink,rm} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {dirname,join} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';
import {request as apiRequest} from '@playwright/test';

test('component-specific anonymous boundaries use actual HTTP and never fall back',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'onesa-owner-component-'));
 const require=createRequire(import.meta.url);
 await symlink(dirname(dirname(dirname(require.resolve('@playwright/test/package.json')))),join(directory,'node_modules'),'dir');
 await copyFile(new URL('../../scripts/release/oracle_robot_browser.mjs',import.meta.url),join(directory,'browser.mjs'));
 const {verifyAnonymousBoundary}=await import(pathToFileURL(join(directory,'browser.mjs')).href);
 const calls=[];let taskStatus=401,missionStatus=404;
 const server=http.createServer((q,s)=>{calls.push(q.url);s.writeHead(q.url==='/api/v1/tasks'?taskStatus:missionStatus);s.end();});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const api=await apiRequest.newContext();
 const base={binding:{repository:'DruHustle/onesa-robot',component:'api'},origin:`http://127.0.0.1:${server.address().port}`,environment:'production',read_only:true};
 try{
  assert.deepEqual(await verifyAnonymousBoundary({request:api},base),{path:'/api/v1/tasks',http_status:401});
  assert.deepEqual(calls,['/api/v1/tasks']);
  const identityApi={...base,binding:{...base.binding,component:'identity-api'}};
  assert.deepEqual(await verifyAnonymousBoundary({request:api},identityApi),{path:'/api/v1/tasks',http_status:401});
  assert.equal(identityApi.binding.component,'identity-api');
  for(const value of [{...base,binding:{...base.binding,component:'frontend'}},{...base,binding:{...base.binding,component:'identity-frontend'}},{...base,environment:'staging',read_only:false},{...identityApi,environment:'staging',read_only:false}]){
   const before=calls.length;await assert.rejects(verifyAnonymousBoundary({request:api},value));assert.deepEqual(calls.slice(before),['/api/v1/missions']);
  }
  missionStatus=401;
  assert.deepEqual(await verifyAnonymousBoundary({request:api},{...base,environment:'staging',read_only:false}),{path:'/api/v1/missions',http_status:401});
  for(const status of [200,302,403,404,503]){
   taskStatus=status;const before=calls.length;await assert.rejects(verifyAnonymousBoundary({request:api},base));assert.deepEqual(calls.slice(before),['/api/v1/tasks']);
  }
  for(const value of [{...base,read_only:false},{...base,binding:{...base.binding,component:'worker'}},{...base,binding:{...base.binding,repository:'foreign/repo'}}]){
   const before=calls.length;await assert.rejects(verifyAnonymousBoundary({request:api},value));assert.equal(calls.length,before);
  }
 }finally{await api.dispose();await new Promise(resolve=>server.close(resolve));await rm(directory,{recursive:true});}
});
