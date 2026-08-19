import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
// `import.meta.dirname` avoids the CommonJS `__dirname` shim under native ESM.

/**
 * Unit and component tests.
 *
 * jsdom rather than a real browser: everything here is logic, formatting and
 * rendering. The things that genuinely need a browser — MapLibre's WebGL
 * canvas, the real network — are covered by the Playwright suite instead.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": new URL("./src", import.meta.url).pathname },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    // Playwright specs live under tests/e2e and are driven by Playwright.
    exclude: ["node_modules/**", "tests/e2e/**", ".next/**", "out/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/app/layout.tsx", "src/**/*.d.ts"],
    },
  },
});
