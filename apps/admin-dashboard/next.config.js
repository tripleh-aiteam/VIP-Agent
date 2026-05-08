/** @type {import('next').NextConfig} */
const nextConfig = {
  // Vercel handles output automatically
  // standalone mode only needed for Docker
  transpilePackages: ["@triple-h/chatbot"],
  webpack: (config) => {
    const path = require("path");
    config.resolve.alias = {
      ...(config.resolve.alias || {}),
      "@triple-h/chatbot": path.resolve(__dirname, "../../packages/chatbot/src"),
    };
    return config;
  },
};

module.exports = nextConfig;
