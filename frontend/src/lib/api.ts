import { Course } from './types';

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

export async function generateCourse(id: string): Promise<Course> {
  const res = await fetch(`${API_BASE}/api/courses/${id}/generate`, {
    method: 'POST',
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to generate course' }));
    throw new Error(error.detail || 'Failed to generate course');
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
