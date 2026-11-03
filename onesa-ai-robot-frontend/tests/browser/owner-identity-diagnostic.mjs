// Synthetic fixture diagnostics only. Never emit exception text or session data.
function category(error){
 const message=String(error?.message||'');
 if(/No resource with given identifier|No data found for resource/.test(message))return 'body_unavailable';
 if(/Execution context was destroyed/.test(message))return 'document_replaced';
 if(/net::ERR_ABORTED/.test(message))return 'navigation_aborted';
 if(/net::ERR_CERT_AUTHORITY_INVALID/.test(message))return 'certificate_refused';
 if(/Timeout|timeout/.test(message))return 'timeout';
 return 'failed';
}

export async function withIdentityDiagnostics(page,action,report=value=>console.info('Synthetic identity phases: '+JSON.stringify(value))){
 let active=true;
 const phases={start_body:'not_started',ui_click:'not_started',portal_navigation:'not_started'},restore=[];
 const observe=async(key,call)=>{phases[key]='pending';try{const value=await call();phases[key]='complete';return value;}catch(error){phases[key]=category(error);throw error;}};
 const wrapped=new WeakSet();
 const observed=new Proxy(page,{get(target,key){
  if(key==='waitForResponse')return(predicate,options)=>target.waitForResponse(response=>{
   const accepted=predicate(response);
   if(accepted&&active&&!wrapped.has(response)){
    wrapped.add(response);const json=response.json;
    response.json=(...args)=>observe('start_body',()=>json.apply(response,args));restore.push(()=>{response.json=json;});
   }
   return accepted;
  },options);
  if(key==='locator')return(selector,...args)=>{
   const locator=target.locator(selector,...args);
   if(['#onesa-sign-in-button','#onesa-confirm-identity'].includes(selector)){
    const click=locator.click;locator.click=(...values)=>observe('ui_click',()=>click.apply(locator,values));restore.push(()=>{locator.click=click;});
   }
   return locator;
  };
  if(key==='waitForURL')return(...args)=>observe('portal_navigation',()=>target.waitForURL(...args));
  const value=Reflect.get(target,key);return typeof value==='function'?value.bind(target):value;
 }});
 try{return await action(observed);}catch(error){try{report({...phases});}catch{}throw error;}
 finally{active=false;for(const reset of restore)reset();}
}
