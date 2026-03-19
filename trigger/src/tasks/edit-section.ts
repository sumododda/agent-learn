import { task } from "@trigger.dev/sdk/v3";
import { editSection } from "../lib/api-client";

export const editSectionTask = task({
  id: "edit-section",
  retry: { maxAttempts: 2, factor: 2, minTimeoutInMs: 1000, maxTimeoutInMs: 30000 },
  run: async (payload: { courseId: string; sectionPosition: number }) => {
    const result = await editSection(payload.courseId, payload.sectionPosition);
    return result;
  },
});
