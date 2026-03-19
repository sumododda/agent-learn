import { Course, GenerateResponse } from './types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export async function createCourse(topic: string, instructions?: string): Promise<Course> {
  const res = await fetch(`${API_BASE}/api/courses`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ topic, instructions: instructions || null }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to create course' }));
    throw new Error(error.detail || 'Failed to create course');
  }
  return res.json();
}

export async function generateCourse(id: string): Promise<GenerateResponse> {
  const res = await fetch(`${API_BASE}/api/courses/${id}/generate`, {
    method: 'POST',
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
  sectionComments?: { position: number; comment: string }[]
): Promise<Course> {
  const res = await fetch(`${API_BASE}/api/courses/${id}/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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

export async function listCourses(): Promise<Course[]> {
  const res = await fetch(`${API_BASE}/api/courses`, {
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new Error('Failed to load courses');
  }
  return res.json();
}

export async function getCourse(id: string): Promise<Course> {
  const res = await fetch(`${API_BASE}/api/courses/${id}`, {
    cache: 'no-store',
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Course not found' }));
    throw new Error(error.detail || 'Course not found');
  }
  return res.json();
}
