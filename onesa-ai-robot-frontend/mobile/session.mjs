const PRODUCTION_API_ORIGIN = 'https://robot.onesa.eu';

// Expo inlines EXPO_PUBLIC_* values into the native bundle at build time.
// Restrict the value to a bare HTTPS Onesa origin so a staging build cannot
// silently call production and a typo cannot redirect session cookies/CSRF.
export function resolveApiOrigin(value) {
  if (value === undefined || value === null || value === '') return PRODUCTION_API_ORIGIN;
  if (typeof value !== 'string') throw new Error('Robot API origin must be an HTTPS Onesa origin.');
  let url;
  try { url = new URL(value); }
  catch { throw new Error('Robot API origin must be an HTTPS Onesa origin.'); }
  const allowedOrigins = new Set([
    PRODUCTION_API_ORIGIN,
    'https://onesa-ai-robot-backend-staging.onrender.com',
  ]);
  if (url.protocol !== 'https:' || url.username || url.password ||
      (url.pathname !== '' && url.pathname !== '/') || url.search || url.hash ||
      url.port || !allowedOrigins.has(url.origin)) {
    throw new Error('Robot API origin must be an approved HTTPS Onesa origin without credentials, path, query or fragment.');
  }
  return url.origin;
}

export const ORIGIN = resolveApiOrigin(process.env.EXPO_PUBLIC_ROBOT_API_ORIGIN);
export const CALLBACK = 'onesarobot://account/callback';
export const AUTH = '/auth/identity';
export const PRODUCT = 'robot';

export async function call(path, {method = 'GET', body, csrf} = {}) {
  const response = await fetch(ORIGIN + '/api/v1' + path, {
    method, credentials: 'include',
    headers: {Accept: 'application/json', Origin: ORIGIN,
      ...(body === undefined ? {} : {'Content-Type': 'application/json'}),
      ...(csrf ? {'X-CSRF-Token': csrf} : {})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})
  });
  if (!response.ok) {
    const err = new Error(response.status === 401 ? 'Sign in to continue.' :
      response.status === 403 ? 'Your account does not have access to this action.' :
      response.status === 409 ? 'The record changed. Refresh its current status.' :
      'The product service could not complete this request. Refresh before retrying.');
    err.status = response.status;
    throw err;
  }
  return response.status === 204 ? null : response.json();
}


/**
 * Stop is independent of ordinary operator mutations and their loading lock.
 * An acknowledged POST alone is insufficient: read back the simulator latch,
 * active-task state and physical-actuation flag before presenting a safe result.
 */
export async function stopSimulation(csrf, api = call) {
  if (!csrf || typeof csrf !== 'string') throw Error('Sign in before requesting Stop.');
  const reply = await api('/stop', {method:'POST', csrf});
  if (reply?.status !== 'stopped' || reply.physical_actuation !== false) {
    throw Error('Simulator Stop was not acknowledged.');
  }
  const status = await api('/status');
  if (status?.mode !== 'simulation' || status.stopped !== true || status.active_task !== null || status.physical_actuation !== false) {
    throw Error('Simulator Stop state could not be verified.');
  }
  // The status latch and the durable task journal are separate observations.
  // Do not tell the operator that Stop is verified while history still exposes
  // a proposed/running task or when the journal cannot be read.
  const history = await api('/tasks');
  if (!Array.isArray(history?.tasks) ||
      !history.tasks.every(task => ['completed', 'cancelled'].includes(task?.state))) {
    throw Error('Simulator task history could not verify Stop.');
  }
  // The legacy joint and 33-axis humanoid simulators have independent task
  // registries. A green legacy latch alone does not establish whole-body idle.
  const humanoid = await api('/humanoid/state');
  if (humanoid?.connection !== 'simulated_actuators' || humanoid?.stopped !== true || humanoid.active_command !== null ||
      humanoid.pending !== null || humanoid.physical_actuation !== false) {
    throw Error('Humanoid Stop state could not be verified.');
  }
  return status;
}

/**
 * Reset only clears the software Stop latch. Verify every independent software
 * view before telling the operator that the simulator is ready again.
 */
export async function resetSimulation(csrf, api = call) {
  if (!csrf || typeof csrf !== 'string') throw Error('Sign in before resetting the simulator.');
  const reply = await api('/reset', {method:'POST', csrf, body:{confirmed:true}});
  if (reply?.status !== 'reset' || reply.physical_actuation !== false) {
    throw Error('Simulator reset was not acknowledged.');
  }
  const status = await api('/status');
  if (status?.mode !== 'simulation' || status.stopped !== false ||
      status.active_task !== null || status.physical_actuation !== false) {
    throw Error('Simulator reset state could not be verified.');
  }
  const history = await api('/tasks');
  if (!Array.isArray(history?.tasks) ||
      !history.tasks.every(task => ['completed', 'cancelled'].includes(task?.state))) {
    throw Error('Simulator task history could not verify reset.');
  }
  const humanoid = await api('/humanoid/state');
  if (humanoid?.connection !== 'simulated_actuators' || humanoid.stopped !== false ||
      humanoid.active_command !== null || humanoid.pending !== null ||
      humanoid.physical_actuation !== false) {
    throw Error('Humanoid reset state could not be verified.');
  }
  return status;
}

export async function signIn(openBrowser, api = call, {stepUp = false, csrf} = {}) {
  if (stepUp && (typeof csrf !== 'string' || !csrf)) {
    throw Error('Refresh your operator session before confirming with MFA.');
  }
  const start = await api(AUTH + '/start', {
    method:'POST',
    body:{mobile:true, step_up:stepUp},
    ...(stepUp ? {csrf} : {})
  });
  const url = new URL(start.authorize_url || start.authorization_url);
  const state = url.searchParams.get('state');
  if (url.origin !== 'https://auth.onesa.eu' || url.pathname !== '/authorize' ||
      url.username || url.password || url.hash || !/^native_[A-Za-z0-9_-]{43}$/.test(state || '') ||
      url.searchParams.get('code_challenge_method') !== 'S256') throw Error('The sign-in service is unavailable.');
  const result = await openBrowser(url.href, CALLBACK);
  if (result.type !== 'success') throw Error('Sign-in cancelled.');
  const back = new URL(result.url);
  if (back.protocol + '//' + back.host + back.pathname !== CALLBACK || back.username || back.password || back.hash ||
      back.searchParams.getAll('state').length !== 1 || back.searchParams.get('state') !== state ||
      back.searchParams.getAll('code').length !== 1 || !back.searchParams.get('code') ||
      back.searchParams.has('error')) throw Error('Sign-in could not be confirmed. Start again.');
  return api(AUTH + '/mobile/exchange', {method:'POST',body:{state, code:back.searchParams.get('code')}});
}
