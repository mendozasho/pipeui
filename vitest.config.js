import { defineConfig } from "vitest/config";

// Dev-time only. The frontend ships as no-build-step CDN React + Babel standalone;
// this config exists solely so the global-script components can be imported and
// exercised under jsdom. esbuild handles the JSX transform; .jsx files are loaded
// as JSX automatically by Vite/esbuild.
export default defineConfig({
  esbuild: {
    // The frontend uses the classic React.createElement runtime (global React),
    // matching the CDN setup. test-setup.js puts React on the global scope.
    jsx: "transform",
    jsxFactory: "React.createElement",
    jsxFragment: "React.Fragment",
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/pipeui/frontend/test-setup.js"],
    include: ["src/pipeui/frontend/**/*.test.jsx"],
  },
});