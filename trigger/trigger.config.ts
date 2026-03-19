import { defineConfig } from "@trigger.dev/sdk";

export default defineConfig({
  project: "agent-learn",
  dirs: ["./src/tasks"],
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
