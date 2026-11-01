import http from 'node:http';
import {handler} from '../../server.mjs';

// Intercepted Playwright requests can precede the browser's Cookie header.
// This server observes real HTTP while serving the actual built frontend.
export async function loopbackFrontend(baseURL, intercept) {
  const target = new URL(baseURL);
  if (target.protocol !== 'http:' || !['127.0.0.1','localhost','[::1]'].includes(target.hostname) || target.username || target.password || target.pathname !== '/' || target.search || target.hash) {
    throw new Error('Synthetic frontend fixtures require a credential-free loopback origin');
  }
  const errors = [];
  const server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url || '/', 'http://loopback.invalid');
      if (url.origin !== 'http://loopback.invalid' || !['GET','HEAD'].includes(request.method)) {
        response.writeHead(405, {'Cache-Control':'no-store'}).end();
        return;
      }
      if (await intercept(request, response)) return;
      // Unhandled API calls never reach an actual backend, even if a route fixture fails.
      if (url.pathname.startsWith('/api/')) {
        response.writeHead(405, {'Cache-Control':'no-store'}).end();
        return;
      }
      await handler(request, response);
    } catch (error) {
      errors.push(error.message);
      if (!response.headersSent) response.writeHead(500);
      response.end();
    }
  });
  server.requestTimeout = 5000;
  server.headersTimeout = 5000;
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  return {
    origin: `http://127.0.0.1:${server.address().port}`,
    errors,
    close: async () => {
      const closed = new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
      server.closeAllConnections();
      await closed;
    },
  };
}
