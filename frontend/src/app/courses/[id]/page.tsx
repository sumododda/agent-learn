'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { getCourse, generateCourse, regenerateCourse } from '@/lib/api';
import { Course } from '@/lib/types';
import PipelineProgress from '@/components/PipelineProgress';

export default function OutlineReviewPage() {
  const params = useParams();
  const router = useRouter();
  const courseId = params.id as string;

  const [course, setCourse] = useState<Course | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Trigger.dev run tracking
  const [runId, setRunId] = useState<string | null>(null);

  // Comments
  const [overallComment, setOverallComment] = useState('');
  const [sectionComments, setSectionComments] = useState<Record<number, string>>({});

  useEffect(() => {
    async function loadCourse() {
      try {
        const data = await getCourse(courseId);
        setCourse(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load course');
      } finally {
        setLoading(false);
      }
    }
    loadCourse();
  }, [courseId]);

  async function handleApprove() {
    setGenerating(true);
    setError(null);
    try {
      const result = await generateCourse(courseId);
      if (result.run_id) {
        setRunId(result.run_id);
      } else {
        // Fallback: no run_id means pipeline may have been triggered differently
        router.push(`/courses/${courseId}/learn`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate');
      setGenerating(false);
    }
  }

  const handlePipelineComplete = useCallback(() => {
    router.push(`/courses/${courseId}/learn`);
  }, [router, courseId]);

  async function handleRegenerate() {
    setRegenerating(true);
    setError(null);
    try {
      const comments = Object.entries(sectionComments)
        .filter(([, comment]) => comment.trim())
        .map(([position, comment]) => ({ position: Number(position), comment }));

      const updated = await regenerateCourse(
        courseId,
        overallComment.trim() || undefined,
        comments.length > 0 ? comments : undefined
      );
      setCourse(updated);
      setOverallComment('');
      setSectionComments({});
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to regenerate');
    } finally {
      setRegenerating(false);
    }
  }

  const hasComments = overallComment.trim() || Object.values(sectionComments).some(c => c.trim());
  const busy = generating || regenerating;

  if (loading) return <div className="text-center text-gray-400 mt-20">Loading course...</div>;
  if (error && !course) return <div className="text-center text-red-400 mt-20">{error}</div>;
  if (!course) return <div className="text-center text-gray-400 mt-20">Course not found</div>;

  if (course.status === 'completed') {
    router.push(`/courses/${courseId}/learn`);
    return null;
  }

  // Build section titles map for the progress component
  const sectionTitles: Record<number, string> = {};
  for (const section of course.sections) {
    sectionTitles[section.position] = section.title;
  }

  // Access token from env for Trigger.dev React hooks
  const triggerPublicApiKey = process.env.NEXT_PUBLIC_TRIGGER_PUBLIC_API_KEY || '';

  return (
    <div>
      <h1 className="text-2xl font-bold mb-1">{course.topic}</h1>
      <p className="text-gray-400 mb-6">{course.sections.length} sections · Review your course outline</p>

      {/* Show pipeline progress when generating */}
      {runId && triggerPublicApiKey && (
        <PipelineProgress
          runId={runId}
          accessToken={triggerPublicApiKey}
          sectionTitles={sectionTitles}
          onComplete={handlePipelineComplete}
        />
      )}

      {/* Only show outline review UI when not yet generating */}
      {!runId && (
        <>
          {/* Overall comment */}
          <div className="mb-6">
            <textarea
              value={overallComment}
              onChange={(e) => setOverallComment(e.target.value)}
              placeholder="Overall feedback on the outline... (optional)"
              rows={2}
              disabled={busy}
              className="w-full px-4 py-3 bg-gray-900 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 resize-none text-sm"
            />
          </div>

          {/* Sections with per-section comments */}
          <div className="space-y-4 mb-8">
            {course.sections
              .sort((a, b) => a.position - b.position)
              .map((section) => (
                <div key={section.id} className="border-l-2 border-gray-700 pl-4 py-2">
                  <div className="text-white font-medium">{section.position}. {section.title}</div>
                  <div className="text-gray-400 text-sm mb-2">{section.summary}</div>
                  <input
                    type="text"
                    value={sectionComments[section.position] || ''}
                    onChange={(e) =>
                      setSectionComments((prev) => ({ ...prev, [section.position]: e.target.value }))
                    }
                    placeholder="Comment on this section..."
                    disabled={busy}
                    className="w-full px-3 py-1.5 bg-gray-900 border border-gray-800 rounded text-sm text-gray-300 placeholder-gray-600 focus:outline-none focus:border-purple-500"
                  />
                </div>
              ))}
          </div>

          {error && <p className="text-red-400 text-sm mb-4">{error}</p>}

          <div className="flex gap-3">
            <button
              onClick={handleApprove}
              disabled={busy}
              className="flex-1 py-3 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg font-medium transition-colors"
            >
              {generating ? 'Generating lessons...' : 'Approve & Generate'}
            </button>
            <button
              onClick={handleRegenerate}
              disabled={busy}
              className="px-6 py-3 bg-gray-800 hover:bg-gray-700 border border-gray-600 rounded-lg text-gray-300 transition-colors disabled:opacity-50"
            >
              {regenerating ? 'Regenerating...' : hasComments ? 'Regenerate with Feedback' : 'Regenerate'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
