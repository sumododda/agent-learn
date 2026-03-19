import { task } from "@trigger.dev/sdk/v3";
import { verifySection } from "../lib/api-client";

export const verifySectionTask = task({
  id: "verify-section",
  retry: { maxAttempts: 2, factor: 2, minTimeoutInMs: 1000, maxTimeoutInMs: 30000 },
  run: async (payload: { courseId: string; sectionPosition: number }) => {
    const result = await verifySection(payload.courseId, payload.sectionPosition);
    return result;
  },
});
