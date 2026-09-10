import { defineConfig } from "vite";
import { chmodSync, copyFileSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [{
    name: "package-fallback-geojson",
    closeBundle() {
      // Runtime fetch paths are not imports, so Vite will not bundle these.
      const target = resolve(root, "dist/data");
      mkdirSync(target, { recursive: true });
      for (const file of readdirSync(resolve(root, "data"))) {
        if (!file.endsWith(".geojson")) continue;
        const output = resolve(target, file);
        copyFileSync(resolve(root, "data", file), output);
        // Generated snapshots can be owner-only; nginx workers must read them.
        chmodSync(output, 0o644);
      }
    }
  }],
  server: {
    port: 5173,
    proxy: {
      "/geoserver": {
        target: "http://localhost:8080",
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: "dist"
  }
});
