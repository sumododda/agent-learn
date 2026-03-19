export interface Citation {
  number: number;
  claim: string;
  source_url: string;
  source_title: string;
}

export interface Section {
  id: string;
  position: number;
  title: string;
  summary: string;
  content: string | null;
  citations: Citation[] | null;
}

export interface Course {
  id: string;
  topic: string;
  instructions: string | null;
  status: string;
  sections: Section[];
  ungrounded: boolean;
}

export interface EvidenceCard {
  id: string;
  section_position: number;
  claim: string;
  source_url: string;
  source_title: string;
  source_tier: 1 | 2 | 3;
  passage: string;
  retrieved_date: string;
  confidence: number;
  caveat: string | null;
  explanation: string;
  verified: boolean;
  verification_note: string | null;
}

export interface BlackboardState {
  glossary: Record<string, { definition: string; defined_in_section: number }>;
  concept_ownership: Record<string, number>;
  coverage_map: Record<number, string[]>;
  key_points: Record<number, string>;
  source_log: Array<{ url: string; title: string; sections_used_in: number[] }>;
  open_questions: string[];
}

export interface PipelineStatus {
  course_id: string;
  stage: string;
  current_section: number | null;
  sections: Record<number, string>;
}
