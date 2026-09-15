import { defineConfig } from "vite";

export default defineConfig(({ mode }) => ({
  define: {
    "import.meta.env.VITE_PUBLIC_DEMO": JSON.stringify(mode === "public" ? "true" : "false")
  },
  plugins: [{
    name: "public-atlas-html",
    transformIndexHtml(html) {
      if (mode !== "public") return html;
      return html.replace('<html lang="en">', '<html lang="en" data-public-demo="true">')
        .replace('href="http://localhost:8000/datasets"', 'href="#sources"')
        .replace("Open data catalogue", "Data sources");
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
}));
