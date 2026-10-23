import { defineConfig, loadEnv } from "vite";
import { fileURLToPath } from "node:url";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const apiTarget = env.VITE_VERDFLOW_API_TARGET || "http://127.0.0.1:8768";

  return {
    base: "./",
    publicDir: fileURLToPath(new URL("../../assets/branding/generated", import.meta.url)),
    plugins: [vue()],
    server: {
      host: "127.0.0.1",
      port: 4173,
      strictPort: false,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: false,
        },
      },
    },
    build: {
      target: "es2022",
      sourcemap: true,
    },
  };
});
