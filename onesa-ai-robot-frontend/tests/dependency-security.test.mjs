import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {dirname,join} from 'node:path';
import test from 'node:test';

const require=createRequire(import.meta.url);
const fromRedocly=createRequire(require.resolve('@redocly/openapi-core/package.json'));

test('installed Redocly uses only the fixed upstream matcher through its callable adapter',async()=>{
 const callable=fromRedocly('minimatch');
 const fromAdapter=createRequire(fromRedocly.resolve('minimatch'));
 assert.equal(fromRedocly('minimatch/package.json').name,'@onesa/robot-minimatch-callable');
 assert.equal(fromAdapter('minimatch-fixed/package.json').version,'11.0.1');
 assert.equal(callable,fromAdapter('minimatch-fixed').minimatch);
 assert.equal(callable('contracts/openapi.json','contracts/*.json'),true);
 assert.equal(callable('private/record.json','contracts/*.json'),false);
 const installed=dirname(fromRedocly.resolve('minimatch/package.json'));
 for(const file of ['index.cjs','UPSTREAM.json','UPSTREAM_LICENSE.md'])
  assert.deepEqual(await readFile(join(installed,file)),await readFile(new URL('../security/minimatch-callable/'+file,import.meta.url)));
 assert.deepEqual(await readFile(join(installed,'UPSTREAM_LICENSE.md')),await readFile(join(dirname(fromAdapter.resolve('minimatch-fixed/package.json')),'LICENSE.md')));
 const lock=JSON.parse(await readFile(new URL('../package-lock.json',import.meta.url)));
 const upstream=JSON.parse(await readFile(new URL('../security/minimatch-callable/UPSTREAM.json',import.meta.url)));
 assert.equal(lock.packages['node_modules/minimatch-fixed'].integrity,upstream.integrity);
 const archive=await readFile(new URL('../vendor/onesa-robot-minimatch-callable-1.0.0.tgz',import.meta.url));
 assert.equal(lock.packages['node_modules/minimatch'].integrity,'sha512-'+createHash('sha512').update(archive).digest('base64'));
});

test('the actual contract resolver preserves URL-header allowlists with the fixed matcher',async()=>{
 const {readFileFromUrl}=fromRedocly('./lib/utils.js');
 const requests=[];
 const config={headers:[
  {matches:'example.invalid/api/**',name:'x-contract-fixture',value:'synthetic-only'},
  {matches:'different.invalid/**',name:'x-unrelated-fixture',value:'must-not-forward'},
 ],customFetch:async(url,options)=>{
  requests.push({url,headers:options.headers});
  return {ok:true,text:async()=>'{"openapi":"3.1.0"}',headers:new Map([['content-type','application/json']])};
 }};
 await readFileFromUrl('https://example.invalid/api/schema.json',config);
 await readFileFromUrl('https://example.invalid/unrelated/schema.json',config);
 assert.deepEqual(requests[0].headers,{'x-contract-fixture':'synthetic-only'});
 assert.deepEqual(requests[1].headers,{});
});

test('fixed matcher bounds adversarial glob preprocessing in an isolated process',()=>{
 const result=spawnSync(process.execPath,['-e',
  "const m=require('minimatch');for(let i=1;i<=40;i++)m('safe/path','a/'+('**/../'.repeat(i))+'*.json',{optimizationLevel:2});"
 ],{cwd:new URL('..',import.meta.url),timeout:2000,encoding:'utf8'});
 assert.equal(result.error,undefined);
 assert.equal(result.status,0,result.stderr);
});
