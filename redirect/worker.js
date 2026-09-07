// Redirect Worker for the OLD address, ecnl-dashboard.zhenyisx.workers.dev.
//
// The site moved to the NextOneTwoLabs Cloudflare account, at the custom domain
// ecnl.nextonetwo.com. This forwards every old link straight there (one hop),
// keeping the path and query string. Browsers carry
// the #fragment over a redirect when the Location has none, so deep links such
// as #tab=teams&team=55477 still land on the right page.
//
// Deployed to the personal account only (see redirect/wrangler.toml); the org
// account builds the real site from the repository root.
export default {
  fetch(request) {
    const url = new URL(request.url);
    url.hostname = 'ecnl.nextonetwo.com';
    return Response.redirect(url.toString(), 301);
  },
};
