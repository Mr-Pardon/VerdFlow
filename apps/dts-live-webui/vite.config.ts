import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/",
  publicDir: fileURLToPath(new URL("../../assets/branding/generated", import.meta.url)),
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    port: 4174,
    strictPort: false,
  },
  build: {
    target: "es2022",
    sourcemap: false,
    emptyOutDir: true,
    outDir: fileURLToPath(
      new URL("../dts-live-connector/src/verdflow/dts_live_connector/static", import.meta.url),
    ),
  },
});
