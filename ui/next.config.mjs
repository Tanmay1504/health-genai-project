// Next.js config.
//
// The browser only ever calls same-origin `/api/...`. Next.js rewrites those
// requests to the FastAPI backend (API_URL). WHY a proxy instead of calling
// :8000 directly from the browser?
//   * no CORS configuration needed in production,
//   * the backend URL is a server-side setting, not baked into client JS.
// Note: rewrites are resolved at BUILD time for `next build`, so set API_URL
// before building (the Dockerfile does this with a build arg).
const API_URL = process.env.API_URL || "http://localhost:8000";

// DEMO_EXPORT=1 builds the static GitHub Pages demo instead: plain HTML/JS
// files, no server, no rewrites; data comes from public/demo/*.json.
const DEMO_EXPORT = process.env.DEMO_EXPORT === "1";

/** @type {import('next').NextConfig} */
const demoConfig = {
  output: "export",
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || "",
  trailingSlash: true, // GitHub Pages serves /fraud/ as fraud/index.html
  images: { unoptimized: true },
};

/** @type {import('next').NextConfig} */
const serverConfig = {
  output: "standalone", // small self-contained server for the Docker image
  experimental: {
    // LLM-backed calls (appeal drafting on a local 8B model) can take a minute+;
    // the default proxy timeout (30 s) would cut them off.
    proxyTimeout: 300_000,
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default DEMO_EXPORT ? demoConfig : serverConfig;
