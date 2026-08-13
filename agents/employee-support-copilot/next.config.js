/** @type {import('next').NextConfig} */
const nextConfig = {
  // Allow the BFF API routes to call SearchaaS on localhost in dev.
  // In production, SEARCHAAS_BASE_URL is set via environment variable.

  // Produce a self-contained server bundle (.next/standalone) for the
  // Cloud Run container image — see deployment/google/agents/Dockerfile.
  output: "standalone",
};

module.exports = nextConfig;
