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
