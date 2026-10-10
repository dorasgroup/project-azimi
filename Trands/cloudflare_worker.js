export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const pathname = url.pathname;

    // 1. Health check
    if (pathname === '/' || pathname === '/health') {
      return new Response(JSON.stringify({ status: 'ok', service: 'Trend Monitor Proxy' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json; charset=utf-8' }
      });
    }

    // 2. Telegram Proxy: /telegram/s/<channel> -> https://t.me/s/<channel>
    if (pathname.startsWith('/telegram/')) {
      const targetPath = pathname.replace(/^\/telegram/, '');
      const targetUrl = 'https://t.me' + targetPath + url.search;

      const newHeaders = new Headers(request.headers);
      newHeaders.set('Host', 't.me');
      newHeaders.set('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36');
      newHeaders.set('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8');
      newHeaders.set('Accept-Language', 'fa,en;q=0.9');

      return fetch(targetUrl, {
        method: request.method,
        headers: newHeaders,
        body: request.body,
        redirect: 'follow'
      });
    }

    // 3. X (Twitter) Proxy: /x/2/... -> https://api.x.com/2/...
    if (pathname.startsWith('/x/')) {
      const targetPath = pathname.replace(/^\/x/, '');
      const targetUrl = 'https://api.x.com' + targetPath + url.search;

      const newHeaders = new Headers(request.headers);
      newHeaders.set('Host', 'api.x.com');

      return fetch(targetUrl, {
        method: request.method,
        headers: newHeaders,
        body: request.body,
        redirect: 'follow'
      });
    }

    return new Response('Not Found', { status: 404 });
  }
};
