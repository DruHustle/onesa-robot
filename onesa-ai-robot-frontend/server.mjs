import './lib/tls-trust.mjs';
/** Independent static operator frontend with a fixed, server-only API proxy. */
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import {fileURLToPath} from 'node:url';
const root = resolve(import.meta.dirname, 'dist');
let referenceFiles;
async function staticReferenceFiles() {
  if(referenceFiles)return referenceFiles;
  // The API function package does not contain the separately served static tree.
  // Only local static reference requests need its generated NVIDIA catalogue.
  const catalog=JSON.parse(await readFile(resolve(root,'models/nvidia/manifest.json'),'utf8'));
  const files=new Map();
  for(const asset of catalog.assets)for(const file of [asset.glb,asset.thumbnail,asset.licenseFile,asset.sourceFile])files.set('/models/nvidia/'+file.path,'models/nvidia/'+file.path);
  for(const file of ['three.core.js','three.module.js','GLTFLoader.js','BufferGeometryUtils.js','SkeletonUtils.js','OrbitControls.js','LICENSE.txt'])files.set('/vendor/three/'+file,'vendor/three/'+file);
  for(const file of ['nvidia-library.js','nvidia-assets.js','nvidia-catalog.js'])files.set('/'+file,file);
  referenceFiles=files;
  return files;
}
const port = Number(process.env.PORT || 3431);
const upstream = new URL(process.env.ROBOT_API_URL || 'http://127.0.0.1:8431');
if (!['http:', 'https:'].includes(upstream.protocol) || upstream.username || upstream.password || upstream.pathname !== '/' || upstream.search || upstream.hash) throw new Error('ROBOT_API_URL must be a credential-free HTTP(S) origin');
if (upstream.protocol === 'http:' && !['127.0.0.1', 'localhost', 'backend'].includes(upstream.hostname)) throw new Error('Remote gateway requires TLS');
const routes = /^\/api\/v1\/(edge\/audit|health|ready|auth\/(login|logout|me|identity\/(config|start|callback|mobile\/exchange))|admin\/physical-operation(?:\/(allow|block))?|missions(?:\/(?:catalog|context|[a-f0-9-]{36}(?:\/(?:approve|export))?))?|humanoid\/(?:model(?:\.urdf)?|state|heartbeat|proposals(?:\/[a-f0-9-]{36}\/approve)?)|status|tasks(?:\/[a-f0-9-]{36}\/(approve|cancel))?|heartbeat|stop|reset)$/;
const security = {
  'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
  'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()', 'X-Frame-Options': 'DENY'
};
export async function handler(req, res) {
  for (const [key,value] of Object.entries(security)) res.setHeader(key,value);
  try {
    const url = new URL(req.url || '/', 'http://frontend.invalid');
    if (url.pathname.startsWith('/api/')) {
      res.setHeader('Cache-Control','no-store');
      const identityRoute=url.pathname.startsWith('/api/v1/auth/identity/');
      const identityCallback=url.pathname==='/api/v1/auth/identity/callback';
      const queryKeys=[...url.searchParams.keys()];
      const validIdentityQuery=identityCallback&&req.method==='GET'&&url.search.length<=8192&&queryKeys.every(key=>['code','state','error','error_description','iss'].includes(key)&&url.searchParams.getAll(key).length===1);
      if (!routes.test(url.pathname) || url.search&&!validIdentityQuery || !['GET','POST'].includes(req.method)) {res.writeHead(404).end(); return;}
      const headers = {accept:'application/json'};
      for (const name of ['origin','content-type','x-csrf-token']) if (req.headers[name]) headers[name] = req.headers[name];
      const cookie = (req.headers.cookie || '').split(';').map(x=>x.trim()).filter(x=>/^robot_session=/.test(x)||identityRoute&&/^robot_identity=/.test(x)).join('; ');
      if (cookie) headers.cookie = cookie;
      const chunks=[]; let size=0;
      for await (const chunk of req) { size+=chunk.length; if(size>(url.pathname==='/api/v1/missions/context'?262144:16384)) {res.writeHead(413).end();return;} chunks.push(chunk); }
      const response = await fetch(new URL(url.pathname+url.search,upstream), {method:req.method,headers,
        // A journal write plus its bounded read-only confirmation can take six
        // seconds. Leave two seconds for ingress, below the client's ten-second
        // deadline. Never retry a mutation after an uncertain acknowledgement.
        ...(req.method==='POST'?{body:Buffer.concat(chunks)}:{}), redirect:'manual',signal:AbortSignal.timeout(identityCallback?18000:8000)});
      const cookies=response.headers.getSetCookie().filter(x=>/^robot_session=/.test(x)||identityRoute&&/^robot_identity=/.test(x));
      if (response.status >= 300 && response.status < 400) {
        const location=response.headers.get('location');
        const mobile = identityCallback && req.method === 'GET' && response.status === 303 &&
          /^onesarobot:\/\/account\/callback\?state=native_[a-zA-Z0-9_-]{43}&(?:code=[^&#]+|error=cancelled)$/.test(location || '');
        if(mobile || (identityCallback&&response.status===303&&['/console','/console?identity=failed','/console?identity=access','/console?identity=unavailable'].includes(location))){
          if(cookies.length)res.setHeader('Set-Cookie',cookies);
          res.writeHead(303,{Location:location}).end();return;
        }
        res.writeHead(502).end();return;
      }
      const body=Buffer.from(await response.arrayBuffer());
      if(cookies.length) res.setHeader('Set-Cookie',cookies);
      res.setHeader('Content-Type',response.headers.get('content-type') || 'application/json');
      if(url.pathname==='/api/v1/humanoid/model.urdf'&&response.ok)res.setHeader('Content-Disposition','attachment; filename="onesa-humanoid-reference.urdf"');
      res.writeHead(response.status).end(body);
      return;
    }
    if (!['GET','HEAD'].includes(req.method)) {res.writeHead(405).end();return;}
    const files = new Map([['/','index.html'],['/index.html','index.html'],['/console','console.html'],['/console/','console.html'],['/console.html','console.html'],['/privacy','privacy.html'],['/privacy/','privacy.html'],['/privacy.html','privacy.html'],['/onesa-foundation.css','onesa-foundation.css'],['/theme.css','theme.css'],['/app.js','app.js'],['/inspection.js','inspection.js'],['/humanoid.js','humanoid.js'],['/images/pump-inspection.svg','images/pump-inspection.svg'],['/client.js','client.js'],['/brand/onesa-orbit.png','brand/onesa-orbit.png'],['/robots.txt','robots.txt']]);
    files.set('/favicon.ico','favicon.ico');
    for(const width of [640,1280])files.set('/images/robot-concept-'+width+'.webp','images/robot-concept-'+width+'.webp');
    for(const mode of ['light','dark'])files.set('/images/robot-workspace-example-'+mode+'.png','images/robot-workspace-example-'+mode+'.png');
    for(const file of ['onesa-robot.svg','onesa-robot.ico','onesa-robot-16.png','onesa-robot-32.png','onesa-robot-64.png','onesa-robot-180.png'])files.set('/brand/icons/v1/'+file,'brand/icons/v1/'+file);
    const file = files.get(url.pathname) || (await staticReferenceFiles()).get(url.pathname);
    if (!file) {res.writeHead(404).end();return;}
    const body=await readFile(resolve(root,file));
    const type={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png','.webp':'image/webp','.txt':'text/plain; charset=utf-8','.svg':'image/svg+xml','.ico':'image/vnd.microsoft.icon','.glb':'model/gltf-binary','.json':'application/json'}[extname(file)];
    res.setHeader('Content-Type',type);res.setHeader('Cache-Control',file.endsWith('.glb')?'public, max-age=31536000, immutable':'no-cache');
    res.writeHead(200).end(req.method==='HEAD'?undefined:body);
  } catch (error) {
    const codes=new Set(['ECONNRESET','ECONNREFUSED','ENOTFOUND','EAI_AGAIN','ETIMEDOUT','UND_ERR_CONNECT_TIMEOUT','UND_ERR_SOCKET']);
    const category=error?.name==='TimeoutError'?'upstream_timeout':codes.has(error?.cause?.code)?error.cause.code:'gateway_unavailable';
    // Fixed categories only: no URL, operator, cookie, password or error message.
    console.error(JSON.stringify({event:'robot_gateway_failure',category}));
    if (!res.headersSent) res.writeHead(503,{'Content-Type':'application/json','Cache-Control':'no-store','X-Onesa-Gateway-Error':category});
    res.end('{"detail":"Gateway unavailable"}');
  }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
const server = http.createServer(handler);
server.requestTimeout=5000; server.headersTimeout=5000; server.maxConnections=128;
server.listen(port,'0.0.0.0');
for(const signal of ['SIGTERM','SIGINT']) process.on(signal,()=>{server.close(()=>process.exit(0));setTimeout(()=>process.exit(1),5000).unref();});

}
