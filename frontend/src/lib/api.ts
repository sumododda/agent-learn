import { Course, CourseWithProgress, GenerateResponse, ProgressData } from './types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

function authHeaders(token?: string | null): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

export async function createCourse(topic: string, instructions?: string, token?: string | null): Promise<Course> {
  const res = await fetch(`${API_BASE}/api/courses`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ topic, instructions: instructions || null }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to create course' }));
    throw new Error(error.detail || 'Failed to create course');
  }
  return res.json();
}

export async function generateCourse(id: string, token?: string | null): Promise<GenerateResponse> {
  const res = await fetch(`${API_BASE}/api/courses/${id}/generate`, {
    method: 'POST',
    headers: authHeaders(token),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to generate course' }));
    throw new Error(error.detail || 'Failed to generate course');
  }
  return res.json();
}

export async function regenerateCourse(
  id: string,
  overallComment?: string,
  sectionComments?: { position: number; comment: string }[],
  token?: string | null
): Promise<Course> {
  const res = await fetch(`${API_BASE}/api/courses/${id}/regenerate`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({
      overall_comment: overallComment || null,
      section_comments: sectionComments?.filter(sc => sc.comment.trim()) || [],
    }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to regenerate' }));
    throw new Error(error.detail || 'Failed to regenerate');
  }
  return res.json();
}

export async function listCourses(token?: string | null): Promise<Course[]> {
  const headers: Record<string, string> = {};
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}/api/courses`, {
    cache: 'no-store',
    headers,
  });
  if (!res.ok) {
    throw new Error('Failed to load courses');
  }
  return res.json();
}

export async function getCourse(id: string, token?: string | null): Promise<Course> {
  const headers: Record<string, string> = {};
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}/api/courses/${id}`, {
    cache: 'no-store',
    headers,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Course not found' }));
    throw new Error(error.detail || 'Course not found');
  }
  return res.json();
}

export async function getProgress(courseId: string, token?: string | null): Promise<ProgressData | null> {
  const headers: Record<string, string> = {};
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}/api/courses/${courseId}/progress`, {
    cache: 'no-store',
    headers,
  });
  if (!res.ok) {
    return null;
  }
  const text = await res.text();
  if (!text || text === 'null') {
    return null;
  }
  return JSON.parse(text);
}

export async function updateProgress(
  courseId: string,
  data: { current_section?: number; completed_section?: number },
  token?: string | null
): Promise<ProgressData> {
  const res = await fetch(`${API_BASE}/api/courses/${courseId}/progress`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to update progress' }));
    throw new Error(error.detail || 'Failed to update progress');
  }
  return res.json();
}

export async function listMyCoursesWithProgress(token?: string | null): Promise<CourseWithProgress[]> {
  const headers: Record<string, string> = {};
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}/api/me/courses`, {
    cache: 'no-store',
    headers,
  });
  if (!res.ok) {
    throw new Error('Failed to load courses');
  }
  return res.json();
}
