/** @type {import('next').NextConfig} */
const nextConfig = {
  // Vercel handles output automatically
  // standalone mode only needed for Docker
  // @triple-h/chatbot is declared as a file:../../packages/chatbot dependency
  // in package.json, so npm install resolves it via node_modules. We still
  // need transpilePackages because the package ships TypeScript source
  // (no compiled dist/ in this monorepo path) and Next.js needs to transpile it.
  transpilePackages: ["@triple-h/chatbot"],
};

module.exports = nextConfig;
