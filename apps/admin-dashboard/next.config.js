/** @type {import('next').NextConfig} */
const nextConfig = {
  // Vercel handles output automatically
  // standalone mode only needed for Docker
  // @triple-h/chatbot is declared as a file:../../packages/chatbot dependency
  // in package.json, so npm install resolves it via node_modules. We still
  // need transpilePackages because the package ships TypeScript source
  // (no compiled dist/ in this monorepo path) and Next.js needs to transpile it.
  transpilePackages: ["@triple-h/chatbot"],
  // Same-origin API proxy for remote (Tailscale) access: the browser calls
  // https://vip.<tailnet>.ts.net/api/... and THIS Next.js server forwards it to the
  // private backend on localhost:8000 — so port 8000 is never exposed. Paired with
  // NEXT_PUBLIC_API_BASE_URL=/api. The backend has no /api prefix, so :path* strips it.
  async rewrites() {
    const backend = process.env.BACKEND_ORIGIN || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
};

module.exports = nextConfig;
