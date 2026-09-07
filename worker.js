// Entry point for the deployed Worker. The site itself is the static files in
// public/ (see [assets] in wrangler.toml); this script exists only to send the
// workers.dev address to the canonical custom domain. It runs ahead of the
// static assets for "/" only (run_worker_first in wrangler.toml), so a page
// view costs one Worker request and every other file is served as a free
// static asset.
//
// Browsers carry the #fragment across a redirect, so deep links such as
// #tab=teams&team=55477 still land on the right page.
const CANONICAL_HOST = 'ecnl.nextonetwo.com';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname.endsWith('.workers.dev')) {
      url.hostname = CANONICAL_HOST;
      return Response.redirect(url.toString(), 301);
    }
    return env.ASSETS.fetch(request);
  },
};
