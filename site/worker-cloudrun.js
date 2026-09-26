// cct-tools-router — routes the tools to Cloud Run, everything else to GreenGeeks.
// The app trusts the forwarding headers only with the X-CCT-Edge secret (edge_proxy.py).
const PULLEYS = 'https://pulley-925396938485.us-central1.run.app';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === '/tools' || path === '/tools/') {
      return fetch('https://tools-hub.onrender.com/', request);
    }
    if (path.startsWith('/tools/pulleys')) {
      return toApp(request, env, PULLEYS, path.slice('/tools/pulleys'.length) || '/', url);
    }
    // The app's own root paths: assets, API, downloads, sign-in and buy pages.
    const appPaths = ['/static/', '/api/', '/download/', '/preview/', '/admin', '/account/'];
    if (appPaths.some(p => path.startsWith(p))) {
      return toApp(request, env, PULLEYS, path, url);
    }
    return fetch(request);                       // GreenGeeks (WordPress)
  },
};

function toApp(request, env, origin, path, url) {
  const target = new URL(origin);
  target.pathname = path;
  target.search = url.search;
  const headers = new Headers(request.headers);
  // Set, not append: whatever the visitor sent in these is discarded.
  headers.set('X-Forwarded-For', request.headers.get('CF-Connecting-IP') || '');
  headers.set('X-Forwarded-Host', url.host);
  headers.set('X-Forwarded-Proto', 'https');
  headers.set('X-CCT-Edge', env.EDGE_SECRET);
  return fetch(target.toString(), {
    method: request.method,
    headers,
    body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
    redirect: 'manual',                        // the app's redirects go to the browser
  });
}
