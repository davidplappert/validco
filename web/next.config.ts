import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // A static export is what makes S3 + CloudFront viable: no Node server, no
  // Lambda@Edge, nothing to keep running. `next build` emits web/out/, which
  // CDK uploads to the site bucket verbatim.
  output: "export",
  // S3 serves /walk/index.html for /walk/ but not for /walk, so emit the
  // directory form and let CloudFront's default root object do the rest.
  trailingSlash: true,
  images: {
    // The export target has no image optimisation server.
    unoptimized: true,
  },
  reactStrictMode: true,
};

export default nextConfig;
