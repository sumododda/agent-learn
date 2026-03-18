# agent-learn

agent-learn is an AI-powered learning product that generates a structured course around a learner's topic, goals, current level, and preferences.

Instead of returning isolated answers, agent-learn proposes a course outline, gets learner approval, researches each section against real sources, assembles lessons with citations and examples, and delivers them progressively while saving progress for continued study.

This README is the working product and system plan. It is intentionally more ambitious than an MVP brief. The goal is to document the intended product shape, the operating principles behind it, and the architectural decisions that future implementation plans should build from.

## Why This Exists

Most online learning products force learners into static content. They are often:
- too shallow for learners who want depth
- too broad for learners with a specific goal
- too technical for the learner's current level
- too generic to feel worth continuing

agent-learn is designed to generate a coherent course around one learner rather than forcing the learner to adapt to a fixed course.

## Product Definition

At a high level, agent-learn should do four things well:
- turn a topic into a structured curriculum
- adapt that curriculum to the learner's context
- ground lessons in real sources instead of unsupported generation
- keep the course coherent over time as the learner studies, asks follow-up questions, and reviews material

The product should feel like a private course builder with memory, not a chat thread with better formatting.

## Core User Experience

1. The learner enters a topic they want to study.
2. The app offers one optional freeform instructions field for constraints and preferences.
3. The system proposes a course outline with modules and section boundaries.
4. The learner reviews, edits, reorders, adds, or removes modules before generation starts.
5. After approval, course generation runs in the background.
6. As sections finish, they become available immediately.
7. The learner studies the course over time, answers quizzes, asks follow-up questions, and resumes where they left off.
8. The system updates its understanding of the learner from both explicit feedback and observed behavior.

Example preference inputs:
- `Keep it practical and example-heavy`
- `Assume I know Python basics`
- `Use diagrams when they clarify complex flows`
- `I want enough depth to build something real`

## Product Principles

- **Approval-driven:** expensive generation starts only after the learner approves the outline.
- **Source-grounded:** factual content should be traceable to supporting evidence.
- **Coherent:** the final result should read like one course, not stitched-together answers.
- **Progressive:** learners should be able to start reading before the entire course is finished.
- **Persistent:** progress, preferences, course history, and follow-up context should be saved.
- **Adaptive:** the system should improve fit over time from quiz performance, interactions, and corrections.
- **Lightweight at the surface:** the learner experience should stay simple even if the system behind it is sophisticated.

## Personalization Model

agent-learn uses progressive profiling instead of heavy onboarding.

- **At course start:** collect the topic and an optional freeform preferences field.
- **Default behavior:** assume a reasonable intermediate baseline if the learner gives no extra input.
- **Behavioral refinement:** use quiz accuracy, reading patterns, skips, revisits, and follow-up questions as signals for adaptation.
- **Explicit correction:** let the learner correct the system whenever inferred preferences or level are wrong.
- **Micro-feedback:** offer lightweight feedback moments such as `too easy`, `about right`, and `too hard`.

Behavioral signals are useful for refinement, but they should complement direct feedback rather than replace it entirely.

## End-to-End System Shape

The system is organized around a planning and content pipeline with explicit review and delivery stages.

```text
topic input
-> outline planning
-> learner approval
-> section research
-> evidence assembly
-> verification
-> lesson writing
-> editorial smoothing
-> diagram + quiz generation
-> progressive delivery
-> ongoing adaptation and review
```

The architecture should preserve two properties:
- bounded responsibility per component
- structured handoffs instead of loose natural-language summaries wherever accuracy matters

## Architecture Overview

The planned implementation uses LangChain Deep Agents as the agent harness. Deep Agents is built on LangGraph and provides built-in planning (`write_todos`), a pluggable filesystem backend for context management, subagent spawning with context isolation, and persistent memory via LangGraph's Memory Store.

The system is organized as a supervisor Deep Agent that delegates bounded tasks to specialized subagents. Each subagent operates with its own isolated context window, so intermediate research and generation work does not bloat the supervisor's context. Deterministic workflow steps handle approval gates, validation, retries, and persistence.

### 1. Supervisor Agent

The supervisor is the top-level Deep Agent created via `create_deep_agent()`. It owns:
- course lifecycle state
- learner approval gates
- job coordination via the built-in `write_todos` planning tool
- checkpointing and retries (handled by LangGraph's runtime)
- progressive delivery events
- shared course state persisted through the filesystem backend

It delegates bounded tasks to specialized subagents rather than running the entire workflow inside one model context. Deep Agents' context isolation ensures the supervisor only receives final outputs from subagents, not their full intermediate tool-call histories.

### 2. Planner

The planner turns a topic plus learner context into an outline.

Its job is to:
- identify the major concepts the learner will need
- decide module boundaries
- order sections based on conceptual dependencies
- produce research briefs for each section

Planning should follow a question-first approach: identify the key questions each section needs to answer before research begins.

Each research brief should contain:
- section goal
- learner level and tone
- must-answer questions
- explicit out-of-scope topics
- source-quality expectations
- completion criteria

### 3. Section Research Subagents

Each research subagent is spawned via the supervisor's `task` tool, scoped to one section at a time with its own isolated context window.

Its job is to gather high-quality material and convert it into structured evidence rather than lesson prose. Multiple research subagents can run in parallel for independent sections, reducing overall generation latency.

Each evidence card should capture:
- claim
- source URL
- supporting passage or section reference
- publication date or freshness signal when relevant
- confidence or support notes
- caveats or disagreements
- plain-language explanation for downstream use

### 4. Verifier

The verifier checks whether the evidence is good enough for writing.

It should look for:
- unsupported claims
- conflicting claims
- low-quality sources
- outdated sources where freshness matters
- unanswered research questions
- overlap with other sections

The verifier should enforce that factual claims in lessons can be traced back to evidence, while still allowing synthesis, transitions, and pedagogical framing that remain consistent with the evidence base.

### 5. Writer

The writer produces lesson prose from the approved outline and verified evidence.

The writer should be instructed to treat verified evidence cards as its primary source of factual content. Verification should catch unsupported additions rather than pretending prompts alone can guarantee perfect grounding.

### 6. Editor

The editor is responsible for coherence, not factual validation.

It should:
- normalize terminology
- smooth transitions
- equalize depth across sections
- remove repetitive explanations
- ensure lessons follow the expected structure

Keeping editing distinct from factual verification preserves clearer accountability when quality problems appear.

### 7. Diagram Generation

Lessons should include diagrams when they improve comprehension.

The current plan is to use Mermaid.js because it is text-based, easy to generate, and easy to render in web clients and export flows.

Diagram generation should:
- receive structured lesson context
- generate Mermaid code
- validate syntax before rendering
- retry or simplify when generation fails
- degrade gracefully by shipping the lesson without a diagram if needed

### 8. Quiz Generation

Each section should include a short knowledge check tied to the concepts covered in that lesson.

Quiz generation should be based on the verified lesson scope and evidence-backed concepts, not generic trivia. The system can begin with straightforward difficulty heuristics and evolve toward richer adaptive behavior if the product accumulates enough high-quality response data.

## Grounding and Consistency Strategy

agent-learn relies on multiple layers of grounding and coordination rather than a single prompt trick.

### Research Briefs

Research briefs keep section-level work tightly scoped and reduce wandering generation.

### Source Policy

Source quality should be tiered:
- **Tier 1:** papers, standards, official docs, vendor docs, primary references
- **Tier 2:** strong technical articles and tutorials
- **Tier 3:** forums, repositories, or community discussions used as supporting context rather than sole authority

Important factual claims should be backed by more than one source when the topic allows it.

### Evidence Cards

Evidence cards create a structured boundary between retrieval and prose generation.

### Shared Course State

Components should coordinate through structured shared state rather than ad hoc cross-agent chat. Deep Agents' pluggable filesystem backend provides the storage layer for this state. The supervisor and subagents read and write structured files (JSON or markdown) through the built-in filesystem tools (`read_file`, `write_file`, `edit_file`), and LangGraph's Memory Store provides cross-thread persistence so state survives across sessions.

Planned shared state includes:
- learner profile
- approved outline
- concept registry
- glossary
- source log
- open questions
- section status
- progress and mastery state

### Coverage and Coherence Controls

The system should track concept ownership and section overlap so the course does not repeat the same material in slightly different words.

This likely includes:
- section-to-concept mapping
- prerequisite ordering checks
- overlap detection
- repetition guards

The exact representation can evolve during implementation, but the underlying goal is stable: every major concept should have a clear home in the course and appear in a sensible order.

## Background Processing and Progressive Delivery

Once the learner approves the outline, generation should continue asynchronously.

Operational goals:
- generate sections independently where parallelism is safe (Deep Agents supports parallel subagent execution)
- persist each section as it completes via the filesystem backend
- stream progress updates to the app (LangGraph runtime supports native streaming)
- retry failed section jobs without blocking the whole course
- resume interrupted work from checkpoints (LangGraph provides built-in checkpointing)

The current plan is to use durable background execution through a job system such as Trigger.dev or Inngest for queue management, with SSE for real-time progress updates in the app. Deep Agents' LangGraph runtime handles agent-level checkpointing and resumption natively.

## Adaptive Learning and Review

agent-learn should adapt after initial generation rather than treating the course as fixed forever.

### During the Course

The system should adjust explanations based on:
- quiz performance
- concepts repeatedly missed
- sections reread or skipped
- follow-up questions that reveal confusion or prior knowledge

When the learner struggles, the system should be able to:
- generate alternative explanations
- add bridging context between prerequisites and new material
- offer targeted review items

### After Delivery

The course should support ongoing review through spaced repetition and mastery tracking.

Planned direction:
- maintain per-concept mastery estimates
- schedule review prompts over time
- update scheduling based on quiz and recall performance
- keep follow-up answers consistent with the generated course and learner history

Advanced psychometric models may become useful later, but they depend on having enough clean learner-response data and well-behaved assessment items. The plan should preserve room for them without pretending they are free on day one.

## Follow-Up Q&A

Follow-up questions should be answered in the context of:
- what the learner has already studied
- the current course structure
- the learner's apparent mastery and confusion areas
- the course's existing terminology and explanations

This is not generic chat. The product should answer within the context of the course itself.

## Lesson Structure

Each lesson page should include:
- title
- why this matters
- main explanation with citations where appropriate
- diagram when useful
- examples
- key takeaways
- short knowledge check
- what comes next

## Decision Status

The README mixes committed design decisions with areas that may be tuned during implementation.

### Committed Direction

- approval before expensive generation
- source-grounded lesson creation
- background generation with progressive delivery
- persistent learner and course state
- structured handoffs between planning, research, verification, and writing
- adaptive follow-up and review as part of the product, not bolt-ons

### Planned but Tunable

- exact agent boundaries
- how much shared course state needs to be explicitly modeled
- whether verification and editorial review remain fully separate in every flow
- how aggressive the system should be about diagram generation
- which mastery model is appropriate once enough data exists
- which retrieval backends are necessary for quality and coverage

### Open Questions Worth Testing

- whether one writer plus one reviewer is more coherent than multiple section writers plus an editor
- how much concept-registry machinery is needed before it becomes operational overhead
- which topics truly benefit from diagrams versus cleaner prose and examples
- where explicit learner correction beats inferred adaptation

## Tech Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js (App Router) |
| Backend | Python FastAPI |
| Database | Supabase (PostgreSQL + pgvector) |
| Auth | Clerk or Supabase Auth |
| AI/LLM | Anthropic Claude + OpenAI via LiteLLM |
| Agent Harness | LangChain Deep Agents (`deepagents`) with LangGraph runtime |
| Background Jobs | Trigger.dev or Inngest |
| Real-time | SSE for progress updates; Supabase Realtime where product state benefits from it |
| Deployment | Vercel for frontend, Railway for backend and workers |

### Why This Stack

- **FastAPI:** good fit for Python-based orchestration, retrieval, and evaluation work
- **Deep Agents:** provides the agent harness with built-in planning, subagent spawning, filesystem-based context management, and persistent memory — all on top of LangGraph's runtime for checkpointing, streaming, and durable execution
- **LiteLLM:** keeps model routing flexible; Deep Agents is model-agnostic and works with any LLM that supports tool calling
- **Supabase:** provides a pragmatic starting point for storage, auth, and vector search
- **Trigger.dev or Inngest:** handles durable background execution and retries at the job queue level
- **SSE:** simple fit for progressive server-to-client updates

## Scope

### Included

- topic input
- optional learner instructions
- outline approval flow
- personalized course generation
- section-level research and evidence capture
- verification and editorial smoothing
- diagrams when helpful
- quizzes and review loops
- follow-up Q&A inside the course context
- saved courses, progress, and return sessions

### Not the Focus

- marketplace/community
- heavy LMS administration
- certificates
- instructor tooling
- live classes
- complex collaboration
- advanced multimedia authoring

## Success Criteria

The product is succeeding when:
- learners approve outlines with only light edits
- generated lessons feel coherent and appropriately scoped
- supported claims are easy to trace back to sources
- sections can be delivered progressively without the course feeling fragmented
- learners come back to continue unfinished courses
- follow-up questions stay consistent with the course
- adaptive review helps learners retain and revisit material

## Main Risks

- **Generic content:** mitigated by strong planning, section briefs, and verification.
- **Overlapping or contradictory sections:** mitigated by structured concept ownership and editorial review.
- **Slow generation:** mitigated by background execution and progressive delivery.
- **Weak personalization:** mitigated by combining defaults, behavioral signals, and explicit corrections.
- **Operational complexity from too many agents:** mitigated by keeping workflow boundaries explicit and collapsing components when decomposition stops paying off.
- **Source quality variance by topic:** mitigated by source policy, verification, and topic-aware retrieval choices.

## Goal

Build a product that can take a topic, understand the learner's context, propose the right structure, and generate a source-grounded course that feels coherent, practical, and worth continuing over time.
