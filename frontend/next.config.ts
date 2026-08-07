import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Overridable so a second dev instance (e.g. pointed at an isolated
  // database via NEXT_PUBLIC_API_URL) can run beside the primary one —
  // Next locks the dist dir per project, so two servers need two dirs.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};

export default nextConfig;
