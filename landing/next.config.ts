import type { NextConfig } from "next";
import release from "./src/lib/extension-release.json";

const nextConfig: NextConfig = {
  async headers() {
    return [{
      source: release.downloadPath,
      headers: [
        { key: "Content-Disposition", value: `attachment; filename="${release.filename}"` },
        { key: "X-Content-Type-Options", value: "nosniff" },
      ],
    }];
  },
};

export default nextConfig;
