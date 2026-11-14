export const ORIGIN = 'https://robot.onesa.eu';
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

export async function signIn(openBrowser, api = call) {
  const start = await api(AUTH + '/start', {method:'POST', body:{mobile:true}});
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
