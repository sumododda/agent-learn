import { task } from "@trigger.dev/sdk/v3";
import { researchSection } from "../lib/api-client";

export const researchSectionTask = task({
  id: "research-section",
  retry: { maxAttempts: 3, factor: 2, minTimeoutInMs: 1000, maxTimeoutInMs: 30000 },
  run: async (payload: { courseId: string; sectionPosition: number }) => {
    const result = await researchSection(payload.courseId, payload.sectionPosition);
    return result;
  },
});
