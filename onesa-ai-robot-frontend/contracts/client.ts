import type {paths,components} from './schema.js';
export type Proposal = components['schemas']['Proposal'];
export type Login = components['schemas']['Login'];
export type Confirmation = components['schemas']['Confirmation'];
export type SimulationStatus = components['schemas']['StatusResponse'];
export type TaskList = components['schemas']['TaskList'];
type ApiPath = keyof paths;
const routes = {
 missions:'/api/v1/missions',mission:'/api/v1/missions/{task_id}',missionCatalog:'/api/v1/missions/catalog',missionApprove:'/api/v1/missions/{task_id}/approve',missionExport:'/api/v1/missions/{task_id}/export',context:'/api/v1/missions/context',
 me:'/api/v1/auth/me',login:'/api/v1/auth/login',logout:'/api/v1/auth/logout',status:'/api/v1/status',
 identityConfig:'/api/v1/auth/identity/config',identityStart:'/api/v1/auth/identity/start',
 tasks:'/api/v1/tasks',heartbeat:'/api/v1/heartbeat',stop:'/api/v1/stop',reset:'/api/v1/reset',
 humanoidModel:'/api/v1/humanoid/model',humanoidState:'/api/v1/humanoid/state',humanoidPropose:'/api/v1/humanoid/proposals',humanoidApprove:'/api/v1/humanoid/proposals/{command_id}/approve',humanoidHeartbeat:'/api/v1/humanoid/heartbeat',
 approve:'/api/v1/tasks/{task_id}/approve',cancel:'/api/v1/tasks/{task_id}/cancel'
 ,physicalPermission:'/api/v1/admin/physical-operation',allowPhysical:'/api/v1/admin/physical-operation/allow',blockPhysical:'/api/v1/admin/physical-operation/block'
} as const satisfies Record<string,ApiPath>;
export class RobotClient {
 csrf = '';
 authenticationMethod: 'operator' | 'onesa' = 'operator';
 canOperate = true;
 confirmationExpires = 0;
 canConfirm(){return this.canOperate&&(this.authenticationMethod!=='onesa'||this.confirmationExpires>Date.now()/1000);}
 confirmation(password:string,confirmed:boolean):Confirmation{return this.authenticationMethod==='onesa'?{...(confirmed?{confirmed:true as const}:{})}:{password};}
 pending = new AbortController();
 cancelPending(){this.pending.abort();this.pending=new AbortController();}
 async request<T>(path:string,body?:unknown,keepalive=false):Promise<T> {
  const lifecycle=this.pending.signal,request=new AbortController();
  const cancel=()=>request.abort();
  lifecycle.addEventListener('abort',cancel,{once:true});
  if(lifecycle.aborted)cancel();
  const deadline=setTimeout(cancel,10000);
  try{
  const response=await fetch(path,{signal:request.signal,method:body===undefined?'GET':'POST',credentials:'same-origin',keepalive,
   headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':this.csrf},
   ...(body===undefined?{}:{body:JSON.stringify(body)})});
  let data:unknown;
  try {data=await response.json();}catch{throw new Error('Gateway returned an invalid response');}
  if(!response.ok){const error=new Error(typeof (data as {detail?:unknown}).detail==='string'?(data as {detail:string}).detail:'Request rejected; check the configured safety limits.');Object.assign(error,{status:response.status});throw error;}
  return data as T;
  }finally{clearTimeout(deadline);lifecycle.removeEventListener('abort',cancel);}
 }
 missionCatalog(){return this.request<components['schemas']['MissionCatalog']>(routes.missionCatalog);}
 missions(){return this.request<{missions:{id:string;asset:string;scenario:string;outcome:components['schemas']['MissionDetail']['record']['outcome'];created:number;finding_count:number}[]}>(routes.missions);}
 mission(id:string){return this.request<components['schemas']['MissionDetail']>(routes.mission.replace('{task_id}',encodeURIComponent(id)));}
 planMission(value:components['schemas']['MissionRequest']){return this.request<components['schemas']['MissionDetail']>(routes.missions,value);}
 approveMission(id:string,value:components['schemas']['MissionConfirmation']){return this.request<components['schemas']['MissionDetail']>(routes.missionApprove.replace('{task_id}',encodeURIComponent(id)),value);}
 exportMission(id:string){return this.request<components['schemas']['MissionDetail']>(routes.missionExport.replace('{task_id}',encodeURIComponent(id)));}
 importContext(value:components['schemas']['ContextRequest']){return this.request<{id:string;asset:components['schemas']['MissionCatalog']['assets'][number]}>(routes.context,value);}
 me(){return this.request<components['schemas']['SessionResponse']>(routes.me);}
 identityConfig(){return this.request<components['schemas']['IdentityConfigurationResponse']>(routes.identityConfig);}
 beginIdentity(step_up=false){return this.request<components['schemas']['IdentityStartResponse']>(routes.identityStart,{step_up});}
 login(value:Login){return this.request<components['schemas']['SessionResponse']>(routes.login,value);}
 logout(){return this.request<components['schemas']['LogoutResponse']>(routes.logout,{});}
 status(){return this.request<SimulationStatus>(routes.status);}
 tasks(){return this.request<TaskList>(routes.tasks);}
 propose(value:Proposal){return this.request<components['schemas']['TaskResponse']>(routes.tasks,value);}
 approve(id:string,value:Confirmation){return this.request<components['schemas']['TaskResponse']>(routes.approve.replace('{task_id}',encodeURIComponent(id)),value);}
 cancel(id:string){return this.request<components['schemas']['ActionResponse']>(routes.cancel.replace('{task_id}',encodeURIComponent(id)),{});}
 heartbeat(){return this.request<components['schemas']['ActionResponse']>(routes.heartbeat,{});}
 humanoidModel(){return this.request<components['schemas']['Model']>(routes.humanoidModel);}
 humanoidState(){return this.request<paths['/api/v1/humanoid/state']['get']['responses'][200]['content']['application/json']>(routes.humanoidState);}
 proposeHumanoid(value:components['schemas']['MotionRequest']){return this.request<components['schemas']['MotionPlan']>(routes.humanoidPropose,value);}
 approveHumanoid(id:string,value:components['schemas']['MissionConfirmation']){return this.request<components['schemas']['MotionPlan']>(routes.humanoidApprove.replace('{command_id}',encodeURIComponent(id)),value);}
 humanoidHeartbeat(command_id:string){return this.request<components['schemas']['ActionResponse']>(routes.humanoidHeartbeat,{command_id});}
 stop(keepalive=false){return this.request<components['schemas']['ActionResponse']>(routes.stop,{},keepalive);}
 reset(value:Confirmation){return this.request<components['schemas']['ActionResponse']>(routes.reset,value);}
 physicalPermission(){return this.request<components['schemas']['PhysicalPermissionResponse']>(routes.physicalPermission);}
 allowPhysical(value:components['schemas']['PhysicalPermissionConfirmation']){return this.request<components['schemas']['PhysicalPermissionResponse']>(routes.allowPhysical,value);}
 blockPhysical(){return this.request<components['schemas']['PhysicalPermissionResponse']>(routes.blockPhysical,{});}
}
