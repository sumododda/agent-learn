import { ChatMessage, ChatModel, Course, CourseWithProgress, EvidenceCard, ProgressData, ProviderConfig, ProviderDefinition } from './types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

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

export async function generateCourse(id: string, token?: string | null): Promise<{ job_id: string }> {
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

export async function resumeCourse(id: string, token?: string | null): Promise<{ job_id: string; checkpoint: number }> {
  const res = await fetch(`${API_BASE}/api/courses/${id}/resume`, {
    method: 'POST',
    headers: authHeaders(token),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to resume course' }));
    throw new Error(error.detail || 'Failed to resume course');
  }
  return res.json();
}

export async function createCourseStream(
  topic: string,
  instructions?: string,
  token?: string | null,
): Promise<string> {
  const res = await fetch(`${API_BASE}/api/courses?stream=true`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ topic, instructions: instructions || null }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to create course' }));
    throw new Error(error.detail || 'Failed to create course');
  }

  // Read SSE stream to find the 'created' event with course_id
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) throw new Error('Stream ended before course was created');
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6));
          if (data.course_id) {
            reader.cancel(); // Don't need the rest, discover page reconnects
            return data.course_id;
          }
        } catch {}
      }
    }
    // Keep unprocessed partial line
    buffer = lines[lines.length - 1];
  }
}

export async function getSseTicket(token: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/auth/sse-ticket`, {
    method: 'POST',
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error('Failed to get SSE ticket');
  const data = await res.json();
  return data.ticket;
}

export function getDiscoverStreamUrl(courseId: string, ticket: string): string {
  return `${API_BASE}/api/courses/${courseId}/discover/stream?token=${encodeURIComponent(ticket)}`;
}

export function getPipelineStreamUrl(courseId: string, ticket: string): string {
  return `${API_BASE}/api/courses/${courseId}/pipeline/stream?token=${encodeURIComponent(ticket)}`;
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

export async function deleteCourse(id: string, token?: string | null): Promise<void> {
  const res = await fetch(`${API_BASE}/api/courses/${id}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error('Delete failed');
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

export async function getEvidence(
  courseId: string,
  sectionPosition: number,
  token?: string | null
): Promise<EvidenceCard[]> {
  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(
    `${API_BASE}/api/courses/${courseId}/evidence?section_position=${sectionPosition}`,
    { headers, cache: 'no-store' }
  );
  if (!res.ok) return [];
  return res.json();
}

export async function getChatModels(token?: string | null): Promise<ChatModel[]> {
  try {
    const res = await fetch(`${API_BASE}/api/chat/models`, {
      headers: authHeaders(token ?? null),
    });
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

export async function getChatHistory(
  courseId: string,
  token?: string | null,
  before?: string
): Promise<ChatMessage[]> {
  const params = new URLSearchParams();
  if (before) params.set('before', before);
  const url = `${API_BASE}/api/courses/${courseId}/chat${params.toString() ? '?' + params.toString() : ''}`;
  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(url, { headers, cache: 'no-store' });
  if (!res.ok) return [];
  return res.json();
}

export async function sendChatMessage(
  courseId: string,
  message: string,
  model: string,
  sectionContext: number,
  token?: string | null
): Promise<Response> {
  return fetch(`${API_BASE}/api/courses/${courseId}/chat`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ message, model, section_context: sectionContext }),
  });
}

export async function getProviderRegistry(token: string | null): Promise<Record<string, ProviderDefinition>> {
  const res = await fetch(`${API_BASE}/api/providers/registry`, {
    headers: authHeaders(token),
  });
  if (!res.ok) return {};
  return res.json();
}

export async function getProviders(token: string | null): Promise<ProviderConfig[]> {
  const res = await fetch(`${API_BASE}/api/providers`, {
    headers: authHeaders(token),
  });
  if (!res.ok) return [];
  return res.json();
}

export async function saveProvider(
  data: { provider: string; credentials: Record<string, string>; extra_fields: Record<string, string> },
  token: string | null
): Promise<ProviderConfig> {
  const res = await fetch(`${API_BASE}/api/providers`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Save failed' }));
    throw new Error(err.detail || 'Save failed');
  }
  return res.json();
}

export async function updateProvider(
  provider: string,
  data: { credentials?: Record<string, string>; extra_fields?: Record<string, string> },
  token: string | null
): Promise<ProviderConfig> {
  const res = await fetch(`${API_BASE}/api/providers/${provider}`, {
    method: 'PUT',
    headers: authHeaders(token),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Update failed' }));
    throw new Error(err.detail || 'Update failed');
  }
  return res.json();
}

export async function deleteProvider(provider: string, token: string | null): Promise<void> {
  const res = await fetch(`${API_BASE}/api/providers/${provider}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error('Delete failed');
}

export async function testProvider(
  provider: string,
  data: { credentials: Record<string, string>; extra_fields: Record<string, string> },
  token: string | null
): Promise<{ status: string }> {
  const res = await fetch(`${API_BASE}/api/providers/${provider}/test`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Test failed' }));
    throw new Error(err.detail || 'Test failed');
  }
  return res.json();
}

export async function setDefaultProvider(provider: string, token: string | null): Promise<ProviderConfig> {
  const res = await fetch(`${API_BASE}/api/providers/default`, {
    method: 'PUT',
    headers: authHeaders(token),
    body: JSON.stringify({ provider }),
  });
  if (!res.ok) throw new Error('Set default failed');
  return res.json();
}

// ---------------------------------------------------------------------------
// Search provider API
// ---------------------------------------------------------------------------

export async function getSearchProviderRegistry(token: string | null): Promise<Record<string, ProviderDefinition>> {
  const res = await fetch(`${API_BASE}/api/search-providers/registry`, {
    headers: authHeaders(token),
  });
  if (!res.ok) return {};
  return res.json();
}

export async function getSearchProviders(token: string | null): Promise<ProviderConfig[]> {
  const res = await fetch(`${API_BASE}/api/search-providers`, {
    headers: authHeaders(token),
  });
  if (!res.ok) return [];
  return res.json();
}

export async function saveSearchProvider(
  data: { provider: string; credentials: Record<string, string>; extra_fields: Record<string, string> },
  token: string | null
): Promise<ProviderConfig> {
  const res = await fetch(`${API_BASE}/api/search-providers`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Save failed' }));
    throw new Error(err.detail || 'Save failed');
  }
  return res.json();
}

export async function updateSearchProvider(
  provider: string,
  data: { credentials?: Record<string, string>; extra_fields?: Record<string, string> },
  token: string | null
): Promise<ProviderConfig> {
  const res = await fetch(`${API_BASE}/api/search-providers/${provider}`, {
    method: 'PUT',
    headers: authHeaders(token),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Update failed' }));
    throw new Error(err.detail || 'Update failed');
  }
  return res.json();
}

export async function deleteSearchProvider(provider: string, token: string | null): Promise<void> {
  const res = await fetch(`${API_BASE}/api/search-providers/${provider}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error('Delete failed');
}

export async function testSearchProvider(
  provider: string,
  data: { credentials: Record<string, string>; extra_fields: Record<string, string> },
  token: string | null
): Promise<{ status: string }> {
  const res = await fetch(`${API_BASE}/api/search-providers/${provider}/test`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Test failed' }));
    throw new Error(err.detail || 'Test failed');
  }
  return res.json();
}

export async function setDefaultSearchProvider(provider: string, token: string | null): Promise<ProviderConfig> {
  const res = await fetch(`${API_BASE}/api/search-providers/default`, {
    method: 'PUT',
    headers: authHeaders(token),
    body: JSON.stringify({ provider }),
  });
  if (!res.ok) throw new Error('Set default failed');
  return res.json();
}
