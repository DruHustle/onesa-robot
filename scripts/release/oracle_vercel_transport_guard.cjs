// Pinned owner preload. An uncertain create acknowledgement never permits retry.
// Only a complete HTTP400 missing_files response permits an identical continuation.
const fs=require('node:fs'),path=require('node:path'),dc=require('node:diagnostics_channel');
const crypto=require('node:crypto'),{AsyncLocalStorage}=require('node:async_hooks');
const {syncBuiltinESMExports}=require('node:module');
const endpoint=/^\/v[0-9]+\/(?:now\/)?deployments(?:\?|$)/;
function durable(directory,name,value){
 const descriptor=fs.openSync(path.join(directory,name),'wx',0o600);
 try{fs.writeFileSync(descriptor,JSON.stringify(value));fs.fsyncSync(descriptor);}finally{fs.closeSync(descriptor);}
 const parent=fs.openSync(directory,'r');try{fs.fsyncSync(parent);}finally{fs.closeSync(parent);}
}
function createGuard(directory,approvedOrigin='https://api.vercel.com'){
 const scope=JSON.parse(fs.readFileSync(path.join(directory,'scope.json')));
 const canonical=scope.repository==='DruHustle/onesa-ug'&&scope.schema_version===2&&scope.repository_id===1444746154&&scope.product==='robot'&&scope.source_path==='onesa-robot'&&scope.branch==='main'&&['candidate_commit','source_tree','product_source_tree'].every(key=>typeof scope[key]==='string'&&/^[a-f0-9]{40}$/.test(scope[key]));
 if(scope.request_budget!==1||!/^[a-f0-9-]{36}$/.test(scope.operation_id)||(!canonical&&scope.repository!=='DruHustle/onesa-robot')||!['frontend','identity-frontend'].includes(scope.component)||!['staging','production'].includes(scope.environment))throw Error('Invalid owner transport scope');
 return function check(origin,method,requestPath){
  if(String(method).toUpperCase()!=='POST'||!endpoint.test(String(requestPath)))return;
  if(new URL(String(origin)).origin!==approvedOrigin)throw Error('Deployment create target refused');
  try{durable(directory,'create-request.json',{...scope,method:'POST',origin:approvedOrigin,path:String(requestPath).split('?')[0],recorded_at:new Date().toISOString(),automatic_retry:false});}
  catch{throw Error('Deployment create already attempted; inspect only');}
 };
}
function install(directory,approvedOrigin='https://api.vercel.com'){
 const check=createGuard(directory,approvedOrigin),local=new AsyncLocalStorage(),scope=JSON.parse(fs.readFileSync(path.join(directory,'scope.json')));
 let first=null,nonCreating=false,continuation=false;
 const refused=()=>{process.stderr.write('Oracle deployment transport refused a duplicate, foreign or uncertain create; inspect retained operation.\n');process.exit(73);};
 const guarded=(origin,method,p)=>{
  if(String(method).toUpperCase()!=='POST'||!endpoint.test(String(p)))return;
  const owned=local.getStore();
  if(owned&&String(origin)===owned.origin&&String(p)===owned.path&&!owned.sent){owned.sent=true;return;}
  try{check(origin,method,p);}catch{refused();}
 };
 const originalFetch=globalThis.fetch;
 globalThis.fetch=async function(input,init){
  const url=new URL(typeof input==='string'||input instanceof URL?input:input.url);
  const method=String(init?.method||input?.method||'GET').toUpperCase(),p=url.pathname+url.search;
  if(method!=='POST'||!endpoint.test(p))return originalFetch.call(this,input,init);
  // The pinned SDK sends JSON strings. Unknown body/redirect transports fail closed.
  if(url.origin!==approvedOrigin||typeof init?.body!=='string'||Buffer.byteLength(init.body)>4*1024*1024)refused();
  if(scope.production_region){let body;try{body=JSON.parse(init.body);}catch{refused();}if(!Array.isArray(body.regions)||body.regions.length!==1||body.regions[0]!==scope.production_region)refused();}
  if(scope.repository==='DruHustle/onesa-ug'){
   let body;try{body=JSON.parse(init.body);}catch{refused();}
   const expected={githubCommitSha:scope.candidate_commit,githubCommitRef:scope.branch,onesaSourceSchema:String(scope.schema_version),onesaSourceRepository:scope.repository,onesaSourceRepositoryId:String(scope.repository_id),onesaSourceProduct:scope.product,onesaSourcePath:scope.source_path,onesaSourceRootTree:scope.source_tree,onesaSourceProductTree:scope.product_source_tree,onesaSourceBranch:scope.branch};
   if(!body.meta||Object.entries(expected).some(([key,value])=>body.meta[key]!==value))refused();
  }
  const bodyHash=crypto.createHash('sha256').update(init.body).digest('hex');
  if(!first){try{check(url.origin,method,p);}catch{refused();}first={path:p,body_sha256:bodyHash};}
  else{
   if(!nonCreating||continuation||first.path!==p||first.body_sha256!==bodyHash)refused();
   try{durable(directory,'create-continuation-request.json',{...first,recorded_at:new Date().toISOString(),definitive_noncreating_acknowledgement:'missing-files-response.json',automatic_retry:false});}catch{refused();}
   continuation=true;
  }
  const owned={origin:url.origin,path:p,sent:false};
  const response=await local.run(owned,()=>originalFetch.call(this,input,{...init,redirect:'error'}));
  if(!continuation&&response.status===400){
   const reader=response.clone().body?.getReader();let size=0;const chunks=[];
   if(reader){
    for(;;){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>256*1024){reader.cancel().catch(()=>{});throw Error('Provider non-creating response exceeds bound; inspect only');}chunks.push(Buffer.from(value));}
    const bytes=Buffer.concat(chunks);let value;try{value=JSON.parse(bytes.toString('utf8'));}catch{}
    const error=value?.error,missing=error?.missing;
    const ambiguous=[value,error].some(row=>row&&['id','deploymentId','deployment','url','readyState'].some(k=>Object.hasOwn(row,k)));
    if(!ambiguous&&error?.code==='missing_files'&&Array.isArray(missing)&&missing.length>0&&missing.length<=5000&&missing.every(x=>typeof x==='string'&&/^[a-f0-9]{40}$/.test(x))){
     durable(directory,'missing-files-response.json',{status:400,code:'missing_files',response_sha256:crypto.createHash('sha256').update(bytes).digest('hex'),missing_count:missing.length,request_body_sha256:bodyHash,observed_at:new Date().toISOString(),deployment_created:false});
     nonCreating=true;
    }
   }
  }
  return response;
 };
 dc.subscribe('undici:request:create',({request})=>guarded(request.origin,request.method,request.path));
 for(const [name,protocol] of [['node:http','http:'],['node:https','https:']]){
  const module=require(name),original=module.request;
  module.request=function(...args){
   let url,options;
   if(typeof args[0]==='string'||args[0] instanceof URL){url=new URL(args[0]);options=args[1]&&typeof args[1]==='object'?args[1]:{};}
   else{options=args[0]||{};const host=options.hostname||options.host||'localhost';url=new URL((options.protocol||protocol)+'//'+host+(options.port?':'+options.port:'')+(options.path||'/'));}
   guarded(url.origin,options.method||'GET',options.path||url.pathname+url.search);
   return original.apply(this,args);
  };
 }
 syncBuiltinESMExports();
}
module.exports={createGuard,install};
if(process.env.ONESA_ORACLE_VERIFIER_PRELOAD==='1'){
 try{install('/private/transport');}catch{process.stderr.write('Oracle deployment transport custody unavailable; no create allowed.\n');process.exit(73);}
}
