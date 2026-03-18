# Milestones

## Milestone 1 — Topic to Draft Course (vertical slice)

The smallest thing that proves the approval flow and draft lesson pipeline work end-to-end.

- **Frontend:** topic input + optional learner instructions → outline display → approve/edit → lesson viewer
- **Backend:** single FastAPI endpoint
- **Agent:** supervisor Deep Agent + planner subagent + writer subagent (draft generation only; no research or verification yet)
- **DB:** Supabase with `courses` and `sections` tables
- No auth, no background jobs, no streaming. Synchronous generation.
- **Proves:** a learner can provide a topic and preferences, approve an outline, and receive a coherent draft course.

## Milestone 2 — Grounded Content

Make the content trustworthy.

- Add research subagent (web search / retrieval)
- Evidence cards as structured intermediate format
- Verification step before writing
- Lightweight editorial smoothing pass for terminology, transitions, and repetition
- Source citations rendered in lessons
- **Proves:** factual claims are traceable, not hallucinated.

## Milestone 3 — Production Delivery

Make it work at real-world speed and reliability.

- Background generation (Trigger.dev or Inngest)
- Progressive delivery via SSE (sections unlock as they complete)
- Auth (Clerk or Supabase Auth)
- Persist learner progress and support resuming unfinished courses across sessions
- Checkpointing and retry on failures
- **Proves:** a learner can start reading while generation continues.

## Milestone 4 — Interactive Learning

Make it a learning product, not just content generation.

- Quiz generation per section
- Follow-up Q&A within course context
- Diagrams (Mermaid)
- **Proves:** learners engage with material, not just read it.

## Milestone 5 — Adaptive

Make it get smarter over time.

- Behavioral profiling (quiz accuracy, skips, revisits)
- Per-concept mastery tracking
- Spaced repetition scheduling
- Alternative explanations for struggling learners
- **Proves:** the system adapts to the learner.
