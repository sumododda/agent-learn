import { task } from "@trigger.dev/sdk/v3";
import { writeSection } from "../lib/api-client";

export const writeSectionTask = task({
  id: "write-section",
  retry: { maxAttempts: 3, factor: 2, minTimeoutInMs: 1000, maxTimeoutInMs: 30000 },
  run: async (payload: { courseId: string; sectionPosition: number }) => {
    const result = await writeSection(payload.courseId, payload.sectionPosition);
    return result;
  },
});
