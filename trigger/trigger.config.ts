import { defineConfig } from "@trigger.dev/sdk/v3";

export default defineConfig({
  project: "proj_ybhtnefuzlrfiyjgafzj",
  dirs: ["./src/tasks"],
  maxDuration: 300, // 5 minutes max per task run
  retries: {
    enabledInDev: false,
    default: {
      maxAttempts: 3,
      factor: 2,
      minTimeoutInMs: 1000,
      maxTimeoutInMs: 30000,
    },
  },
});
