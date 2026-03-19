/**
 * Typed HTTP client for calling Python backend internal API endpoints.
 *
 * All requests include the X-Internal-Token header for authentication.
 * Each function corresponds to a POST /api/internal/* endpoint.
 */

const INTERNAL_API_URL = process.env.INTERNAL_API_URL ?? "http://localhost:8000";
const INTERNAL_API_TOKEN = process.env.INTERNAL_API_TOKEN ?? "";

// ---------------------------------------------------------------------------
// Response types (match Python Pydantic schemas in backend/app/schemas.py)
// ---------------------------------------------------------------------------

export interface SectionInfo {
  id: string;
  position: number;
  title: string;
  summary: string;
}

export interface ResearchBriefInfo {
  id: string;
  section_position: number | null;
  questions: string[];
  source_policy: Record<string, unknown>;
}

export interface DiscoverAndPlanResponse {
  sections: SectionInfo[];
  research_briefs: ResearchBriefInfo[];
  ungrounded: boolean;
}

export interface EvidenceCard {
  id: string;
  section_position: number;
  claim: string;
  source_url: string;
  source_title: string;
  source_tier: number;
  passage: string;
  retrieved_date: string;
  confidence: number;
  caveat: string | null;
  explanation: string;
  verified: boolean;
}

export interface ResearchSectionResponse {
  evidence_cards: EvidenceCard[];
}

export interface VerificationResult {
  cards_verified: number;
  cards_total: number;
  needs_more_research: boolean;
  gaps: string[];
}

export interface VerifySectionResponse {
  verification_result: VerificationResult;
}

export interface Citation {
  number: number;
  claim: string;
  source_url: string;
  source_title: string;
}

export interface WriteSectionResponse {
  content: string;
  citations: Citation[];
}

export interface BlackboardUpdates {
  new_glossary_terms: Record<string, unknown>;
  new_concept_ownership: Record<string, unknown>;
  topics_covered: string[];
  key_points_summary: string;
  new_sources: Record<string, string>[];
}

export interface EditSectionResponse {
  edited_content: string;
  blackboard_updates: BlackboardUpdates;
}

// ---------------------------------------------------------------------------
// Request types
// ---------------------------------------------------------------------------

interface CourseIdPayload {
  course_id: string;
}

interface SectionPayload {
  course_id: string;
  section_position: number;
}

// ---------------------------------------------------------------------------
// HTTP helper
// ---------------------------------------------------------------------------

class InternalApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: string,
    public readonly endpoint: string,
  ) {
    super(`Internal API error: ${endpoint} returned ${status} — ${body}`);
    this.name = "InternalApiError";
  }
}

async function post<TBody, TResponse>(
  path: string,
  body: TBody,
): Promise<TResponse> {
  const url = `${INTERNAL_API_URL}${path}`;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Token": INTERNAL_API_TOKEN,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text().catch(() => "(no body)");
    throw new InternalApiError(response.status, text, path);
  }

  return (await response.json()) as TResponse;
}

// ---------------------------------------------------------------------------
// Public API functions
// ---------------------------------------------------------------------------

/**
 * Run discovery research and planning for a course.
 * Creates sections and research briefs in the database.
 */
export async function discoverAndPlan(
  courseId: string,
): Promise<DiscoverAndPlanResponse> {
  return post<CourseIdPayload, DiscoverAndPlanResponse>(
    "/api/internal/discover-and-plan",
    { course_id: courseId },
  );
}

/**
 * Research a single section — gathers evidence cards from web sources.
 */
export async function researchSection(
  courseId: string,
  sectionPosition: number,
): Promise<ResearchSectionResponse> {
  return post<SectionPayload, ResearchSectionResponse>(
    "/api/internal/research-section",
    { course_id: courseId, section_position: sectionPosition },
  );
}

/**
 * Verify evidence gathered for a section — checks source quality and claim accuracy.
 */
export async function verifySection(
  courseId: string,
  sectionPosition: number,
): Promise<VerifySectionResponse> {
  return post<SectionPayload, VerifySectionResponse>(
    "/api/internal/verify-section",
    { course_id: courseId, section_position: sectionPosition },
  );
}

/**
 * Write the content for a single section based on verified evidence.
 */
export async function writeSection(
  courseId: string,
  sectionPosition: number,
): Promise<WriteSectionResponse> {
  return post<SectionPayload, WriteSectionResponse>(
    "/api/internal/write-section",
    { course_id: courseId, section_position: sectionPosition },
  );
}

/**
 * Edit a written section — applies style/quality improvements.
 */
export async function editSection(
  courseId: string,
  sectionPosition: number,
): Promise<EditSectionResponse> {
  return post<SectionPayload, EditSectionResponse>(
    "/api/internal/edit-section",
    { course_id: courseId, section_position: sectionPosition },
  );
}
