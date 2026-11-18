import {call, signIn, stopSimulation, resetSimulation} from './session.mjs';

const ACCOUNT = /^robot_[a-f0-9]{64}$/;
const TASK = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const TERMINAL = new Set(['completed', 'cancelled']);
const RETURN_KEY = 'robot-task-return-v1';
const SIGNED_OUT_KEY = 'robot-signed-out-v1';
const FRESH_MS = 1500;

export function taskLink(value) {
  try {
    const url = new URL(value);
    const id = url.pathname.slice(1);
    return url.protocol === 'onesarobot:' && url.host === 'tasks' &&
      !url.username && !url.password && !url.search && !url.hash && TASK.test(id) ? id : null;
  } catch { return null; }
}

function checkedAccount(user) {
  if (!ACCOUNT.test(user?.id || '') || user.authentication_method !== 'onesa' ||
      !['viewer', 'operator', 'administrator'].includes(user.role) ||
      typeof user.name !== 'string' || !user.name || user.name.length > 200 ||
      typeof user.csrf !== 'string' || !/^[A-Za-z0-9_-]{1,256}$/.test(user.csrf)) {
    throw Error('The operator account could not be verified. Sign in again.');
  }
  return user;
}

export function checkedObservation(status, history, humanoid) {
  const tasks = history?.tasks;
  if (status?.mode !== 'simulation' || status.physical_actuation !== false ||
      typeof status.stopped !== 'boolean' || !Number.isFinite(status.position_radians) ||
      !Number.isFinite(status.sampled_at) ||
      !(status.active_task === null || TASK.test(status.active_task || '')) ||
      !Array.isArray(tasks) || tasks.length > 50 ||
      !tasks.every(task => TASK.test(task?.id || '') &&
        ['proposed', 'running', 'completed', 'cancelled'].includes(task.state) &&
        Number.isFinite(task.position) && Number.isFinite(task.created) && typeof task.reason === 'string') ||
      new Set(tasks.map(task => task.id)).size !== tasks.length ||
      tasks.some(task => task.state === 'running' && task.id !== status.active_task) ||
      tasks.some(task => task.id === status.active_task && task.state !== 'running') ||
      (status.stopped && (status.active_task !== null || tasks.some(task => !TERMINAL.has(task.state)))) ||
      humanoid?.connection !== 'simulated_actuators' || humanoid.physical_actuation !== false ||
      humanoid.stopped !== status.stopped ||
      !Object.hasOwn(humanoid, 'active_command') || !Object.hasOwn(humanoid, 'pending') ||
      (status.stopped && (humanoid.active_command !== null || humanoid.pending !== null))) {
    throw Error('Simulator observation could not be verified. Controls remain unavailable.');
  }
  return {status, tasks, humanoid};
}

/** Foreground-only operator state. Reads may repeat; mutations never replay. */
export function createOperator({api = call, openBrowser, storage, uuid,
  changed = () => {}, now = () => Date.now(), monotonic = () => performance.now(),
  schedule = setTimeout, unschedule = clearTimeout} = {}) {
  let state = {account:null, items:[], selected:null, observation:null,
    busy:false, stopping:false, available:false, error:'', message:''};
  let alive = true, foreground = true, generation = 0, revision = 0;
  let mutation = null, authentication = false, endingSession = false, observing = null, timer = null, staleTimer = null;
  let target = null, observedAt = -Infinity, stopOwner = null;
  let stopWarning = '', verifiedStop = false;
  let writes = Promise.resolve();

  function publish(patch) { if (alive) { state = {...state, ...patch}; changed(snapshot()); } }
  function snapshot() {
    const current = state.available && foreground && monotonic() - observedAt < FRESH_MS;
    const operator = current && state.account?.role !== 'viewer';
    const view = state.observation;
    const idle = view && view.status.active_task === null &&
      view.humanoid.active_command === null && view.humanoid.pending === null;
    return {...state, available:current,
      canPropose:Boolean(operator && idle && !view.status.stopped),
      canReset:Boolean(operator && idle && view.status.stopped && view.tasks.every(t => TERMINAL.has(t.state))),
      canApprove:Boolean(operator && idle && !view.status.stopped && state.selected?.state === 'proposed')};
  }
  function persist(key, value) {
    // Serialize secure writes so a late selection cannot overwrite logout cleanup.
    writes = writes.catch(() => {}).then(() => value === null ? storage.remove(key) : storage.set(key, value));
    return writes;
  }
  function cancelTimer() { if (timer !== null) unschedule(timer); timer = null; }
  function invalidate() {
    revision++; observedAt = -Infinity;
    verifiedStop = false;
    if (staleTimer !== null) unschedule(staleTimer);
    staleTimer = null;
    publish({available:false, observation:null, message:''});
  }
  function signedOut(message) {
    generation++; invalidate(); cancelTimer();
    publish({account:null, items:[], selected:null, error:message || 'Sign in to continue.'});
  }
  function fail(error) {
    invalidate();
    if (error?.status === 401 || error?.status === 403) signedOut(stopWarning || error.message);
    else publish({items:[], selected:null, error:stopWarning || error?.message || 'Service unavailable. Refresh before retrying.'});
  }
  function later() {
    cancelTimer();
    if (alive && foreground && state.account && !authentication && !stopOwner) {
      timer = schedule(() => { timer = null; void observe(); }, 500);
    }
  }
  function current(owner) { return alive && owner.generation === generation && owner.revision === revision; }
  function authority(owner) {
    if (!current(owner) || !foreground || state.account?.id !== owner.accountId) {
      throw Error('Operator state changed. Review the current simulation before trying again.');
    }
  }

  async function observe() {
    if (!alive || !foreground || !state.account || authentication || stopOwner) return;
    if (observing) { await observing; return; }
    const owner = {generation, revision, accountId:state.account.id};
    const started = monotonic();
    const reads = [api('/auth/me'), api('/status'), api('/tasks'), api('/humanoid/state')];
    observing = (async () => {
      try {
        const [user, status, history, humanoid] = await Promise.all(reads);
        if (!current(owner) || !foreground) return;
        checkedAccount(user);
        if (user.id !== owner.accountId) throw Object.assign(Error('Account changed. Sign in again.'), {status:401});
        const view = checkedObservation(status, history, humanoid);
        if (monotonic() - started >= FRESH_MS) throw Error('Simulator observations are stale. Supervision is paused.');
        // Only this operator's observed running task can justify a heartbeat.
        // Never supervise a humanoid command or another session's task implicitly.
        if (user.role !== 'viewer' && !status.stopped &&
            view.tasks.some(task => task.id === status.active_task && task.state === 'running') &&
            humanoid.active_command === null && humanoid.pending === null) {
          const heartbeat = await api('/heartbeat', {method:'POST', csrf:user.csrf});
          if (heartbeat?.status !== 'received' || heartbeat.physical_actuation !== false) {
            throw Error('Supervision acknowledgement could not be verified.');
          }
          if (!current(owner) || !foreground) return;
          if (monotonic() - started >= FRESH_MS) throw Error('Simulator observations are stale. Supervision is paused.');
        }
        let selected = null;
        if (target) {
          if (target.owner && target.owner !== user.id) {
            target = null;
            await persist(RETURN_KEY, null);
          } else {
            selected = view.tasks.find(task => task.id === target.id) || null;
            if (selected) {
              target = {id:selected.id, owner:user.id};
              await persist(RETURN_KEY, JSON.stringify(target));
            }
          }
        }
        if (!current(owner) || !foreground || monotonic() - started >= FRESH_MS) return;
        observedAt = started;
        if (staleTimer !== null) unschedule(staleTimer);
        staleTimer = schedule(() => {
          staleTimer = null;
          if (current(owner)) publish({available:false, message:'', error:'Observation expired. Supervision is paused.'});
        }, Math.max(0, FRESH_MS - (monotonic() - started)));
        const stoppedMessage = verifiedStop && !status.stopped ? {message:''} : {};
        if (!status.stopped) verifiedStop = false;
        publish({account:user, items:view.tasks, selected, observation:view, available:true,
          error:target && !selected ? 'The requested task is unavailable to this account.' : stopWarning,
          ...stoppedMessage});
      } catch (error) { if (current(owner)) fail(error); }
      finally { await Promise.allSettled(reads); }
    })();
    try { await observing; }
    finally { observing = null; later(); }
  }

  async function fresh() {
    if (observing) await observing;
    await observe();
  }
  async function connect(interactive, stepUp = false) {
    if (authentication || endingSession || mutation || stopOwner || !alive) return;
    const expectedId = stepUp ? state.account?.id : null;
    const proof = state.account?.csrf;
    const owner = ++generation;
    authentication = true; invalidate(); cancelTimer(); publish({busy:true, error:''});
    try {
      if (interactive) await signIn(openBrowser, api, {stepUp, csrf:proof});
      const user = checkedAccount(await api('/auth/me'));
      if (owner !== generation || !alive) return;
      if (stepUp && (user.id !== expectedId || !Number.isFinite(user.confirmation_expires_at) ||
          user.confirmation_expires_at <= now()/1000)) throw Error('Same-account Onesa MFA could not be verified.');
      if (target?.owner && target.owner !== user.id) { target = null; await persist(RETURN_KEY, null); }
      if (owner !== generation || !alive) return;
      await persist(SIGNED_OUT_KEY, null);
      if (owner !== generation || !alive) return;
      publish({account:user, items:[], selected:null, message:stepUp ? 'MFA confirmed. Review and confirm the current operation again.' : ''});
    } catch (error) { if (owner === generation) { signedOut(error.message); } }
    finally {
      authentication = false; publish({busy:Boolean(mutation)});
      await fresh();
    }
  }
  async function start(url) {
    try {
      // Old clients stored an unbound CSRF token. Never migrate it by display name.
      await persist('csrf', null);
      const saved = await storage.get(RETURN_KEY);
      if (saved) {
        try { const value = JSON.parse(saved); if (TASK.test(value.id) && ACCOUNT.test(value.owner)) target = value; }
        catch { /* Invalid encrypted return state conveys no authority. */ }
      }
      if (url) await link(url);
      if (await storage.get(SIGNED_OUT_KEY)) return;
      await connect(false);
    } catch (error) { fail(error); }
  }
  async function select(id) {
    const selected = state.items.find(task => task.id === id);
    if (!selected || !state.account) return;
    target = {id, owner:state.account.id};
    publish({selected});
    try { await persist(RETURN_KEY, JSON.stringify(target)); }
    catch { publish({error:'The task return could not be saved securely.'}); }
  }
  async function link(url) {
    const id = taskLink(url);
    if (!id) return false;
    target = {id, owner:state.account?.id || null};
    publish({selected:null});
    await fresh();
    return true;
  }
  function foregroundChanged(value) {
    if (foreground === value) return;
    foreground = value; invalidate(); cancelTimer();
    if (!value) publish({error:'Supervision paused while the app is inactive. The backend watchdog remains independent.'});
    else void fresh();
  }
  async function command(kind, id) {
    if (!['propose', 'approve', 'cancel', 'reset'].includes(kind)) return;
    if (mutation || authentication || stopOwner || !foreground || !state.account) return;
    const view = snapshot();
    if (!view.available || state.account.role === 'viewer') { publish({error:'Refresh the current operator state before making changes.'}); return; }
    if ((kind === 'propose' && !view.canPropose) || (kind === 'reset' && !view.canReset) ||
        (kind === 'approve' && (!view.canApprove || state.selected?.id !== id)) ||
        (kind === 'cancel' && !state.items.some(t => t.id === id && ['proposed','running'].includes(t.state)))) return;
    if (['approve','reset'].includes(kind) &&
        !(Number.isFinite(state.account.confirmation_expires_at) && state.account.confirmation_expires_at > now()/1000)) {
      await connect(true, true);
      return; // Never replay the pre-browser intent after MFA or a lifecycle change.
    }
    const owner = {generation, revision, accountId:state.account.id};
    mutation = owner; stopWarning = ''; publish({busy:true, error:'', message:''});
    try {
      authority(owner);
      const csrf = state.account.csrf;
      if (kind === 'reset') await resetSimulation(csrf, api);
      else if (kind === 'propose') {
        const task = await api('/tasks', {method:'POST', csrf, body:{command_id:uuid(), issued_at:now()/1000, target_radians:0.1, duration_seconds:2}});
        authority(owner);
        if (!TASK.test(task?.id || '') || task.state !== 'proposed') throw Error('Proposal acknowledgement could not be verified.');
        target = {id:task.id, owner:owner.accountId};
      } else await api('/tasks/'+id+'/'+kind, {method:'POST', csrf, ...(kind === 'approve' ? {body:{confirmed:true}} : {})});
      authority(owner);
      invalidate();
      await fresh();
    } catch (error) { if (current(owner)) fail(error); }
    finally { if (mutation === owner) mutation = null; publish({busy:authentication}); }
  }
  function review(kind, id) {
    const owner = {generation, revision, accountId:state.account?.id};
    let used = false;
    return async () => {
      if (used) return;
      used = true;
      try { authority(owner); }
      catch (error) { publish({error:error.message}); return; }
      await command(kind, id);
    };
  }
  async function stop() {
    if (stopOwner || !state.account) return;
    const owner = {generation, accountId:state.account.id};
    stopOwner = owner; stopWarning = ''; invalidate(); cancelTimer(); publish({stopping:true, error:''});
    try {
      const csrf = state.account.csrf;
      await stopSimulation(csrf, (path, options) => {
        if (owner.generation !== generation || state.account?.id !== owner.accountId) throw Error('Operator session changed during Stop.');
        return api(path, options);
      });
      if (owner.generation === generation) {
        verifiedStop = true;
        publish({message:'Simulation Stop verified: no active task; physical actuation disabled.'});
      }
    } catch (error) {
      if (owner.generation === generation) {
        if (error.status === 401 || error.status === 403) signedOut(error.message);
        stopWarning = 'Stop not verified. Do not resume tasks until simulator status is checked. ' + error.message;
        publish({error:stopWarning});
      }
    } finally { stopOwner = null; publish({stopping:false}); await fresh(); }
  }
  async function logout() {
    if (endingSession || authentication) return;
    const csrf = state.account?.csrf;
    endingSession = true; signedOut(''); target = null; stopWarning = ''; publish({busy:true, error:''});
    try {
      await Promise.all([
        persist(SIGNED_OUT_KEY, '1').then(() => persist(RETURN_KEY, null)),
        csrf ? api('/auth/logout', {method:'POST', csrf}) : Promise.resolve()
      ]);
    } catch { publish({error:'Sign-out could not be fully verified. Do not assume saved or other sessions ended.'}); }
    finally { endingSession = false; publish({busy:Boolean(mutation)}); }
  }
  function dispose() { alive = false; generation++; cancelTimer(); if (staleTimer !== null) unschedule(staleTimer); }
  return {snapshot, start, connect, refresh:fresh, select, link, foregroundChanged, command, review, stop, logout, dispose};
}
