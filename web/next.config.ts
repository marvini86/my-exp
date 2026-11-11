import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  // Lean Docker image: .next/standalone carries only the server and the dependencies in use.
  output: "standalone",
  // No next/image in the project: never load sharp (native binary of the build machine).
  images: { unoptimized: true },
  experimental: {
    serverActions: {
      // Document upload (the API accepts up to 10 MB) goes through a Server Action.
      bodySizeLimit: "11mb",
    },
    // The proxy buffers the body in memory (10 MB by default): without this the upload would be truncated.
    proxyClientMaxBodySize: "11mb",
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          // The Google button checks the origin through the Referer: "same-origin" would not send it.
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
