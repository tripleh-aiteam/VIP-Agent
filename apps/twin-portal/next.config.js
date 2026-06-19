/** @type {import('next').NextConfig} */

// Origins allowed to iframe the Twin Portal (the VIP boss dashboard).
// Prod default is the boss dashboard only; localhost is added in dev so local
// embedding works without extra config. Override fully with FRAME_ANCESTORS.
const FRAME_ANCESTORS =
  process.env.FRAME_ANCESTORS ||
  (process.env.NODE_ENV !== "production"
    ? "'self' http://localhost:3000 https://oasisvip.vercel.app"
    : "'self' https://oasisvip.vercel.app");

const nextConfig = {
  // @triple-h/chatbot ships TypeScript source, so Next must transpile it.
  transpilePackages: ["@triple-h/chatbot"],
  async headers() {
    return [
      {
        // Only the embed entry is framable; the rest of the portal is not.
        source: "/embed",
        headers: [
          {
            key: "Content-Security-Policy",
            value: `frame-ancestors ${FRAME_ANCESTORS};`,
          },
          // Don't leak the embed URL (token + email in query) to any external
          // resource via the Referer header — defense in depth.
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
