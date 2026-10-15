// cct-tools-router — routes the tools to Cloud Run, everything else to GreenGeeks.
// The app trusts the forwarding headers only with the X-CCT-Edge secret (edge_proxy.py).
const PULLEYS = 'https://pulley-925396938485.us-central1.run.app';
// Sprocket (SprocketWebApp-ss): its pages use relative URLs, so it lives wholly under
// /tools/sprocket/ and needs none of the root app paths below.
const SPROCKETS = 'https://sprocket-925396938485.us-central1.run.app';
// Gear (GearWebApp-ss): relative URLs too, wholly under /tools/gears/ (its ADR-040).
const GEARS = 'https://gear-925396938485.us-central1.run.app';
// Worm Gear (WormGearWebApp): relative URLs too, wholly under /tools/worm-gears/.
const WORMGEARS = 'https://wormgear-925396938485.us-central1.run.app';
// The hosted MCP gateway (Documents\CCT_MCP_Gateway): MCP at /mcp and its OAuth
// at the site's root, so AI apps can connect with cheapcadtools.com alone.
const MCP = 'https://mcp-925396938485.us-central1.run.app';
const MCP_EXACT = ['/mcp', '/authorize', '/token', '/register', '/revoke',
                   '/.well-known/oauth-authorization-server'];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (MCP_EXACT.includes(path) || path.startsWith('/mcp/')
        || path.startsWith('/.well-known/oauth-protected-resource')) {
      return toApp(request, env, MCP, path, url);
    }
    if (path === '/tools' || path === '/tools/') {
      // tools-hub.onrender.com is retired; the home page lists the tools.
      return Response.redirect(`${url.origin}/`, 301);
    }
    if (path === '/tools/sprocket') {
      // Relative URLs resolve against the trailing slash.
      return Response.redirect(`${url.origin}/tools/sprocket/${url.search}`, 301);
    }
    if (path.startsWith('/tools/sprocket/')) {
      return toApp(request, env, SPROCKETS, path.slice('/tools/sprocket'.length), url);
    }
    if (path === '/tools/gears') {
      return Response.redirect(`${url.origin}/tools/gears/${url.search}`, 301);
    }
    if (path.startsWith('/tools/gears/')) {
      return toApp(request, env, GEARS, path.slice('/tools/gears'.length), url);
    }
    if (path === '/tools/worm-gears') {
      return Response.redirect(`${url.origin}/tools/worm-gears/${url.search}`, 301);
    }
    if (path.startsWith('/tools/worm-gears/')) {
      return toApp(request, env, WORMGEARS, path.slice('/tools/worm-gears'.length), url);
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
