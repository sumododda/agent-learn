export interface Section {
  id: string;
  position: number;
  title: string;
  summary: string;
  content: string | null;
}

export interface Course {
  id: string;
  topic: string;
  instructions: string | null;
  status: string;
  sections: Section[];
}

export interface GenerateResponse {
  id: string;
  status: string;
  sections: Section[];
  run_id: string | null;
}

export interface PipelineMetadata {
  stage: string;
  current_section: number | null;
  sections: Record<number, string>;
}

export interface ProgressData {
  current_section: number;
  completed_sections: number[];
  last_accessed_at: string;
}

export interface CourseWithProgress extends Course {
  progress: ProgressData | null;
}
