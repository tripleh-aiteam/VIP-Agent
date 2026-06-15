/** @type {import('next').NextConfig} */

// Origins allowed to iframe the Twin Portal (the VIP boss dashboard).
// Override/extend in prod with FRAME_ANCESTORS (space-separated origins).
const FRAME_ANCESTORS =
  process.env.FRAME_ANCESTORS ||
  "'self' http://localhost:3000 https://oasisvip.vercel.app";

const nextConfig = {
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
        ],
      },
    ];
  },
};

module.exports = nextConfig;
