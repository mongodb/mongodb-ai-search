/** @type {import('next').NextConfig} */
const nextConfig = {
  // Allow the BFF API routes to call SearchaaS on localhost in dev.
  // In production, SEARCHAAS_BASE_URL is set via environment variable.
  //
  // NOTE: do NOT set `output: "standalone"` or `output: "export"` here.
  // Amplify Hosting's Next.js adapter builds the default `.next` output and
  // provisions its own SSR compute from it; either override changes the build
  // layout it expects. See deployment/aws/amplify/README.md.
};

module.exports = nextConfig;
