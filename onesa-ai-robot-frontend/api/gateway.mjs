// Vercel rewrites add the captured path to the query string before invoking
// this function at /api/gateway. Local/direct tests may instead retain the
// original /api/v1 path. Reconstruct only one bounded, path-safe binding.
import {handler} from '../server.mjs';
export default async function gateway(req, res) {
  const url = new URL(req.url || '/', 'http://frontend.invalid');
  const bindings = url.searchParams.getAll('__onesa_route');
  const original = url.pathname.startsWith('/api/v1/')
    ? url.pathname.slice('/api/v1/'.length)
    : null;
  const route = bindings.length === 1 ? bindings[0] : '';
  const validRoute = route.length > 0 && route.length <= 256 &&
    /^[A-Za-z0-9_][A-Za-z0-9._/-]*$/.test(route) &&
    !route.includes('..') && !route.includes('//') && !route.endsWith('/');
  if (validRoute && (url.pathname === '/api/gateway' || original === route)) {
    // Keep the caller's query intact; the server rejects it except for the
    // exact identity callback allow-list.
    url.searchParams.delete('__onesa_route');
    req.url = '/api/v1/' + route + url.search;
  }
  return handler(req, res);
}
