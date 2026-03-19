import { task, metadata, batch } from "@trigger.dev/sdk/v3";
import { setCourseStatus } from "../lib/api-client";
import { discoverAndPlanTask } from "./discover-and-plan";
import { researchSectionTask } from "./research-section";
import { verifySectionTask } from "./verify-section";
import { writeSectionTask } from "./write-section";
import { editSectionTask } from "./edit-section";

export const generateCourseTask = task({
  id: "generate-course",
  retry: { maxAttempts: 1 }, // No retry on parent
  run: async (payload: { courseId: string }) => {
    const { courseId } = payload;

    // Step 1: Discover and plan
    metadata.set("pipeline", { status: "planning", sections: {} });
    const planResult = await discoverAndPlanTask.triggerAndWait({ courseId });
    if (!planResult.ok) {
      metadata.set("pipeline", { status: "failed", error: "Planning failed" });
      await setCourseStatus(courseId, "failed");
      return { status: "failed", error: "Planning failed" };
    }

    const sections = planResult.output.sections;
    const sectionStatuses: Record<number, { stage: string; error?: string }> = {};
    sections.forEach((s) => {
      sectionStatuses[s.position] = { stage: "pending" };
    });

    // Step 2: Research all sections in parallel
    sections.forEach((s) => {
      sectionStatuses[s.position] = { stage: "researching" };
    });
    metadata.set("pipeline", { status: "researching", sections: sectionStatuses });

    const researchPayloads = sections.map((s) => ({
      id: "research-section" as const,
      payload: { courseId, sectionPosition: s.position },
    }));
    const researchResults = await batch.triggerAndWait<typeof researchSectionTask>(researchPayloads);

    // Mark research results
    for (let i = 0; i < sections.length; i++) {
      const pos = sections[i].position;
      if (!researchResults.runs[i].ok) {
        sectionStatuses[pos] = { stage: "failed", error: "Research failed" };
      } else {
        sectionStatuses[pos] = { stage: "researched" };
      }
    }
    metadata.set("pipeline", { status: "researched", sections: sectionStatuses });

    // Step 3: Sequential verify -> write -> edit per section
    for (const section of sections) {
      const pos = section.position;
      if (sectionStatuses[pos].stage === "failed") continue;

      // Verify
      sectionStatuses[pos] = { stage: "verifying" };
      metadata.set("pipeline", { status: "writing", sections: sectionStatuses });
      const verifyResult = await verifySectionTask.triggerAndWait({
        courseId,
        sectionPosition: pos,
      });
      if (!verifyResult.ok) {
        sectionStatuses[pos] = { stage: "failed", error: "Verification failed" };
        continue;
      }

      // Write
      sectionStatuses[pos] = { stage: "writing" };
      metadata.set("pipeline", { status: "writing", sections: sectionStatuses });
      const writeResult = await writeSectionTask.triggerAndWait({
        courseId,
        sectionPosition: pos,
      });
      if (!writeResult.ok) {
        sectionStatuses[pos] = { stage: "failed", error: "Writing failed" };
        continue;
      }

      // Edit
      sectionStatuses[pos] = { stage: "editing" };
      metadata.set("pipeline", { status: "writing", sections: sectionStatuses });
      const editResult = await editSectionTask.triggerAndWait({
        courseId,
        sectionPosition: pos,
      });
      if (!editResult.ok) {
        sectionStatuses[pos] = { stage: "failed", error: "Editing failed" };
        continue;
      }

      sectionStatuses[pos] = { stage: "completed" };
      metadata.set("pipeline", { status: "writing", sections: sectionStatuses });
    }

    // Step 4: Determine final status
    const failedSections = Object.values(sectionStatuses).filter(
      (s) => s.stage === "failed",
    );
    const finalStatus =
      failedSections.length === 0
        ? "completed"
        : failedSections.length === sections.length
          ? "failed"
          : "completed_partial";

    metadata.set("pipeline", { status: finalStatus, sections: sectionStatuses });

    // Set course status in the Python backend
    await setCourseStatus(courseId, finalStatus);

    return { status: finalStatus, sections: sectionStatuses };
  },
});
