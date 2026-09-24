import type { NextConfig } from "next";

// Static export (design.md section 3.3): the built app is plain HTML/JS/CSS
// served from S3 + CloudFront (design.md section 15.2), never a Node
// server. Every page therefore fetches its data client-side, with cookies
// (`credentials: "include"`), against the separately-deployed FastAPI BFF
// (Task 13) -- there is no Next.js middleware or server component data
// fetching available in this mode, which is why route protection
// (`lib/auth.ts`) is a client-side redirect, not a server-side guard.
const nextConfig: NextConfig = {
  output: "export",
  images: {
    unoptimized: true, // next/image's optimizer needs a server; static export has none.
  },
};

export default nextConfig;
