/** @type {import('next').NextConfig} */
const nextConfig = {
  // Allow the BFF API routes to call SearchaaS on localhost in dev.
  // In production, SEARCHAAS_BASE_URL is set via environment variable.
};

module.exports = nextConfig;
