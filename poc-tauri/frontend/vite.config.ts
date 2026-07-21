import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Tauri expects a fixed port; Vite serves the same build the desktop shell loads.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    // Don't watch the Rust build output — it churns during `tauri` builds and
    // locks DLLs on Windows, which crashes Vite's file watcher (EBUSY).
    watch: { ignored: ["**/src-tauri/**"] },
  },
  clearScreen: false,
});
