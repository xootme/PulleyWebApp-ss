export default {
  async fetch(request) {
    const url = new URL(request.url);
    const path = url.pathname;

    // Tool routing table
    const tools = {
      '/tools/pulleys': 'https://pulleywebapp.onrender.com',
    };

    // Route tool pages
    if (path === '/tools' || path === '/tools/') {
      return fetch('https://tools-hub.onrender.com/', request);
    }

    for (const [prefix, origin] of Object.entries(tools)) {
      if (path.startsWith(prefix)) {
        const stripped = path.slice(prefix.length) || '/';
        const target = new URL(origin);
        target.pathname = stripped;
        target.search = url.search;
        return fetch(new Request(target.toString(), request));
      }
    }

    // These paths only exist on Render, never on GreenGeeks
    // Route them directly — no Referer needed
    const renderOnly = ['/static/', '/api/', '/download/', '/preview/', '/admin'];
    if (renderOnly.some(p => path.startsWith(p))) {
      return fetch('https://pulleywebapp.onrender.com' + path + url.search, request);
    }

    // Everything else — GreenGeeks
    return fetch(request);
  }
}
