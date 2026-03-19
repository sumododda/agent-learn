import { task } from "@trigger.dev/sdk/v3";
import { discoverAndPlan } from "../lib/api-client";

export const discoverAndPlanTask = task({
  id: "discover-and-plan",
  retry: { maxAttempts: 3, factor: 2, minTimeoutInMs: 1000, maxTimeoutInMs: 30000 },
  run: async (payload: { courseId: string }) => {
    const result = await discoverAndPlan(payload.courseId);
    return result;
  },
});
