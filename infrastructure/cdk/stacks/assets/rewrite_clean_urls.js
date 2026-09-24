// CloudFront Function (viewer-request): rewrites clean URLs to the
// matching static-export file. Next.js `output: "export"` (no
// `trailingSlash`) writes each route as `<route>.html` (e.g.
// `standings.html`), not `<route>/index.html` -- and S3 via an Origin
// Access Control origin (not S3's own website-hosting endpoint) never
// resolves a directory index on its own. Without this, a real top-level
// navigation to a clean URL like `/standings` 404s at the origin,
// CloudFront's custom error response falls back to `/index.html`, and
// that root page's hardcoded `router.replace("/dashboard")` silently
// swallows the original destination -- found live, 2026-09-23, as every
// new page appearing to "bounce back" to the dashboard.
function handler(event) {
    var request = event.request;
    var uri = request.uri;

    if (uri === "/" || uri.includes(".")) {
        return request;
    }
    if (uri.endsWith("/")) {
        request.uri = uri + "index.html";
        return request;
    }
    request.uri = uri + ".html";
    return request;
}
