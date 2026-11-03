import assert from 'node:assert/strict';
import {test} from 'node:test';
import {withIdentityDiagnostics} from './browser/owner-identity-diagnostic.mjs';

test('synthetic body diagnostics preserve response/error identity and emit only fixed categories',async()=>{
 const reports=[],failure=new Error('Protocol error: No resource with given identifier; private query, cookie and password must not be copied');
 const json=async()=>{throw failure;};const response={json,status:()=>200};const options={timeout:15000};
 const page={waitForResponse(predicate,received){assert.equal(received,options);assert(predicate(response));return Promise.resolve(response);}};
 await assert.rejects(withIdentityDiagnostics(page,async observed=>{
  const actual=await observed.waitForResponse(value=>value.status()===200,options);assert.equal(actual,response);await actual.json();
 },value=>reports.push(value)),error=>error===failure);
 assert.equal(response.json,json);assert.deepEqual(reports,[{start_body:'body_unavailable',ui_click:'not_started',portal_navigation:'not_started'}]);
});

test('synthetic click diagnostics retain original arguments and restore the locator',async()=>{
 const reports=[],failure=new Error('locator.click: net::ERR_ABORTED at https://private.invalid/?secret=synthetic');
 const click=async options=>{assert.deepEqual(options,{button:'left'});throw failure;};const locator={click};
 const page={locator(selector){assert.equal(selector,'#onesa-sign-in-button');return locator;}};
 await assert.rejects(withIdentityDiagnostics(page,observed=>observed.locator('#onesa-sign-in-button').click({button:'left'}),value=>reports.push(value)),error=>error===failure);
 assert.equal(locator.click,click);assert.deepEqual(reports,[{start_body:'not_started',ui_click:'navigation_aborted',portal_navigation:'not_started'}]);
});

test('a response arriving after early observer exit cannot install a late wrapper',async()=>{
 const failure=new Error('Synthetic early click failure');let emit,pending;
 const json=async()=>({synthetic:true});const response={json};
 const page={waitForResponse(predicate){return new Promise(resolve=>{emit=()=>{assert(predicate(response));resolve(response);};});}};
 await assert.rejects(withIdentityDiagnostics(page,async observed=>{
  pending=observed.waitForResponse(()=>true,{timeout:15000});throw failure;
 },()=>{}),error=>error===failure);
 emit();assert.equal(await pending,response);assert.equal(response.json,json);
});
