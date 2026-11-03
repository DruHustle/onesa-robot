const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),http=require('node:http'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const {createGuard}=require('./oracle_vercel_transport_guard.cjs');
const modulePath=path.join(__dirname,'oracle_vercel_transport_guard.cjs');
function fixture(){const p=fs.mkdtempSync(path.join(os.tmpdir(),'onesa-transport-test-'));fs.chmodSync(p,0o700);fs.writeFileSync(path.join(p,'scope.json'),JSON.stringify({request_budget:1,operation_id:crypto.randomUUID(),repository:'DruHustle/onesa-robot',component:'frontend',environment:'staging'}),{mode:0o600});return p;}
function child(code){return new Promise((resolve,reject)=>{const p=spawn(process.execPath,['-e',code],{stdio:'ignore'});p.on('error',reject);p.on('exit',code=>resolve(code));});}
test('identity frontend keeps its distinct durable scope and the single create budget',()=>{
 const p=fixture();try{
  const scope=JSON.parse(fs.readFileSync(path.join(p,'scope.json')));scope.component='identity-frontend';fs.writeFileSync(path.join(p,'scope.json'),JSON.stringify(scope));
  const check=createGuard(p);check('https://api.vercel.com','POST','/v13/deployments');
  assert.equal(JSON.parse(fs.readFileSync(path.join(p,'create-request.json'))).component,'identity-frontend');
  assert.throws(()=>check('https://api.vercel.com','POST','/v13/deployments'));
  for(const component of ['api','identity-api','identity-foreign']){scope.component=component;fs.writeFileSync(path.join(p,'scope.json'),JSON.stringify(scope));assert.throws(()=>createGuard(p));}
 }finally{fs.rmSync(p,{recursive:true});}
});
test('the create budget survives process interruption and rejects a foreign target',()=>{
 const p=fixture();try{let check=createGuard(p);assert.throws(()=>check('https://foreign.invalid','POST','/v13/deployments'));check('https://api.vercel.com','POST','/v13/deployments?teamId=fixture');check('https://api.vercel.com','POST','/v2/files');check('https://api.vercel.com','GET','/v13/deployments');check=createGuard(p);assert.throws(()=>check('https://api.vercel.com','POST','/v13/deployments'));assert.equal(JSON.parse(fs.readFileSync(path.join(p,'create-request.json'))).automatic_retry,false);}finally{fs.rmSync(p,{recursive:true});}
});
test('simultaneous processes reserve one create atomically',async()=>{
 const p=fixture();try{const code=`try{require(${JSON.stringify(modulePath)}).createGuard(${JSON.stringify(p)})('https://api.vercel.com','POST','/v13/deployments');process.exit(0)}catch{process.exit(73)}`;const codes=await Promise.all([child(code),child(code)]);assert.deepEqual(codes.sort(),[0,73]);}finally{fs.rmSync(p,{recursive:true});}
});
for(const kind of ['fetch','http'])test(`${kind} transport cannot send a second deployment create`,async()=>{
 const p=fixture();let count=0;const server=http.createServer((req,res)=>{count++;req.resume();res.writeHead(201,{'Content-Type':'application/json'});res.end('{}');});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
 try{const request=kind==='fetch'?`await fetch(${JSON.stringify(origin+'/v13/deployments')},{method:'POST',body:'{}'})`:`await new Promise((resolve,reject)=>{const r=require('node:http').request(${JSON.stringify(origin+'/v13/deployments')},{method:'POST'},res=>{res.resume();res.on('end',resolve)});r.on('error',reject);r.end('{}')})`;
 const code=`require(${JSON.stringify(modulePath)}).install(${JSON.stringify(p)},${JSON.stringify(origin)});(async()=>{${request};${request};process.exit(0)})().catch(()=>process.exit(2));`;
 assert.equal(await child(code),73);assert.equal(count,1);
 }finally{await new Promise(resolve=>server.close(resolve));fs.rmSync(p,{recursive:true});}
});

async function serverFixture(first,action,{interrupt=false,repeatMissing=false}={}){
 const p=fixture();let count=0;
 const server=http.createServer((req,res)=>{req.resume();if(req.url.startsWith('/v2/files')){res.writeHead(200);res.end('{}');return;}count++;
  if(interrupt){res.writeHead(400,{'Content-Type':'application/json','Content-Length':'500'});res.write('{"error":');setTimeout(()=>res.destroy(),20);return;}
  res.writeHead(count===1||repeatMissing?first.status:201,{'Content-Type':'application/json'});res.end(count===1||repeatMissing?first.body:'{"id":"dpl_fixture"}');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
 const setup=`require(${JSON.stringify(modulePath)}).install(${JSON.stringify(p)},${JSON.stringify(origin)});`;
 const request=(body='{}',query='')=>`await fetch(${JSON.stringify(origin+'/v13/deployments'+query)},{method:'POST',body:${JSON.stringify(body)}})`;
 try{return await action({p,origin,setup,request,count:()=>count});}
 finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));fs.rmSync(p,{recursive:true});}
}
const missing={status:400,body:JSON.stringify({error:{code:'missing_files',missing:['a'.repeat(40)]}})};
test('a complete non-creating missing-file response permits one identical continuation, never a third create',async()=>{
 await serverFixture(missing,async({p,origin,setup,request,count})=>{
  const code=setup+`(async()=>{${request()};await fetch(${JSON.stringify(origin+'/v2/files')},{method:'POST',body:'synthetic'});${request()};${request()}})().catch(()=>process.exit(2));`;
  assert.equal(await child(code),73);assert.equal(count(),2);
  const ack=JSON.parse(fs.readFileSync(path.join(p,'missing-files-response.json')));assert.equal(ack.deployment_created,false);assert.equal(ack.status,400);assert.equal(ack.missing_count,1);
  assert.ok(fs.existsSync(path.join(p,'create-continuation-request.json')));
 });
});
for(const [name,first] of [
 ['server failure',{status:500,body:missing.body}],
 ['malformed JSON',{status:400,body:'{"error":'}],
 ['different error',{status:400,body:'{"error":{"code":"not_allowed","missing":["'+ 'a'.repeat(40)+'"]}}'}],
 ['malformed file identity',{status:400,body:'{"error":{"code":"missing_files","missing":["unbound"]}}'}],
 ['empty missing list',{status:400,body:'{"error":{"code":"missing_files","missing":[]}}'}],
 ['ambiguous deployment identity',{status:400,body:JSON.stringify({id:'dpl_possible',error:{code:'missing_files',missing:['a'.repeat(40)]}})}],
])test(`${name} cannot authorize a continuation`,async()=>{
 await serverFixture(first,async({setup,request,count})=>{assert.equal(await child(setup+`(async()=>{${request()};${request()}})().catch(()=>process.exit(2));`),73);assert.equal(count(),1);});
});
for(const [name,body,query] of [['body','{"changed":true}',''],['team','{}','?teamId=changed']])test(`a changed ${name} cannot continue the file probe`,async()=>{
 await serverFixture(missing,async({setup,request,count})=>{assert.equal(await child(setup+`(async()=>{${request()};${request(body,query)}})().catch(()=>process.exit(2));`),73);assert.equal(count(),1);});
});
test('an acknowledged file probe cannot be resumed by a new process',async()=>{
 await serverFixture(missing,async({setup,request,count})=>{assert.equal(await child(setup+`(async()=>{${request()};process.exit(0)})().catch(()=>process.exit(2));`),0);assert.equal(await child(setup+`(async()=>{${request()}})().catch(()=>process.exit(2));`),73);assert.equal(count(),1);});
});
test('an interrupted response never resets the durable create budget',async()=>{
 await serverFixture(missing,async({p,setup,request,count})=>{assert.equal(await child(setup+`(async()=>{${request()}})().catch(()=>process.exit(2));`),2);assert.equal(fs.existsSync(path.join(p,'missing-files-response.json')),false);assert.equal(await child(setup+`(async()=>{${request()}})().catch(()=>process.exit(2));`),73);assert.equal(count(),1);},{interrupt:true});
});
test('repeated missing-file responses never expand the one-continuation allowance',async()=>{
 await serverFixture(missing,async({setup,request,count})=>{assert.equal(await child(setup+`(async()=>{${request()};${request()};${request()}})().catch(()=>process.exit(2));`),73);assert.equal(count(),2);},{repeatMissing:true});
});

test('a production create cannot move the pinned function region',async()=>{
 const p=fixture();let count=0;const server=http.createServer((req,res)=>{count++;req.resume();res.writeHead(201,{'Content-Type':'application/json'});res.end('{}');});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin='http://127.0.0.1:'+server.address().port;
 try{
  const scope=JSON.parse(fs.readFileSync(path.join(p,'scope.json')));scope.environment='production';scope.production_region='iad1';fs.writeFileSync(path.join(p,'scope.json'),JSON.stringify(scope));
  for(const regions of [undefined,['sin1'],['iad1','sin1']]){
   const body=JSON.stringify({target:'production',...(regions?{regions}:{})});
   const code=`require(${JSON.stringify(modulePath)}).install(${JSON.stringify(p)},${JSON.stringify(origin)});fetch(${JSON.stringify(origin+'/v13/deployments')},{method:'POST',body:${JSON.stringify(body)}}).then(()=>process.exit(0)).catch(()=>process.exit(2));`;
   assert.equal(await child(code),73);
  }
  assert.equal(count,0);assert.equal(fs.existsSync(path.join(p,'create-request.json')),false);
 }finally{await new Promise(resolve=>server.close(resolve));fs.rmSync(p,{recursive:true});}
});
