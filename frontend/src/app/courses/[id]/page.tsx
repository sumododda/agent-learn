'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { getCourse, generateCourse, regenerateCourse } from '@/lib/api';
import { Course } from '@/lib/types';
import PipelineProgress from '@/components/PipelineProgress';

export default function OutlineReviewPage() {
  const params = useParams();
  const router = useRouter();
  const { getToken } = useAuth();
  const courseId = params.id as string;

  const [course, setCourse] = useState<Course | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Trigger.dev run tracking
  const [runId, setRunId] = useState<string | null>(null);

  // Polling-based progress tracking (fallback when Trigger public key unavailable)
  const [pollingActive, setPollingActive] = useState(false);
  const [sectionsWithContent, setSectionsWithContent] = useState(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Comments
  const [overallComment, setOverallComment] = useState('');
  const [sectionComments, setSectionComments] = useState<Record<number, string>>({});

  const triggerPublicApiKey = process.env.NEXT_PUBLIC_TRIGGER_PUBLIC_API_KEY || '';

  useEffect(() => {
    async function loadCourse() {
      try {
        const token = await getToken();
        const data = await getCourse(courseId, token);
        setCourse(data);

        // If course is already generating, start polling
        if (data.status === 'generating') {
          setGenerating(true);
          if (!triggerPublicApiKey) {
            setPollingActive(true);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load course');
      } finally {
        setLoading(false);
      }
    }
    loadCourse();
  }, [courseId, getToken, triggerPublicApiKey]);

  // Poll course status when generating without Trigger realtime
  useEffect(() => {
    if (!pollingActive) return;

    async function pollStatus() {
      try {
        const token = await getToken();
        const data = await getCourse(courseId, token);
        setCourse(data);

        const withContent = data.sections.filter(
          (s) => s.content && s.content.trim().length > 0
        ).length;
        setSectionsWithContent(withContent);

        if (data.status === 'completed' || data.status === 'completed_partial') {
          setPollingActive(false);
          router.push(`/courses/${courseId}/learn`);
        } else if (data.status === 'failed') {
          setPollingActive(false);
          setGenerating(false);
          setError('Course generation failed. You can try again.');
        }
      } catch {
        // Poll failure is non-critical, will retry
      }
    }

    pollRef.current = setInterval(pollStatus, 5000);
    pollStatus(); // Initial poll

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [pollingActive, courseId, getToken, router]);

  async function handleApprove() {
    setGenerating(true);
    setError(null);
    try {
      const token = await getToken();
      const result = await generateCourse(courseId, token);
      if (result.run_id) {
        setRunId(result.run_id);
        // Start polling fallback if no Trigger public key
        if (!triggerPublicApiKey) {
          setPollingActive(true);
        }
      } else {
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
      const token = await getToken();
      const comments = Object.entries(sectionComments)
        .filter(([, comment]) => comment.trim())
        .map(([position, comment]) => ({ position: Number(position), comment }));

      const updated = await regenerateCourse(
        courseId,
        overallComment.trim() || undefined,
        comments.length > 0 ? comments : undefined,
        token
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
  const sortedSections = [...course.sections].sort((a, b) => a.position - b.position);
  for (const section of sortedSections) {
    sectionTitles[section.position] = section.title;
  }

  const isGenerating = generating || runId !== null || course.status === 'generating';
  const totalSections = sortedSections.length;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-1">{course.topic}</h1>
      <p className="text-gray-400 mb-6">{totalSections} sections · Review your course outline</p>

      {/* Show Trigger.dev realtime progress when available */}
      {runId && triggerPublicApiKey && (
        <PipelineProgress
          runId={runId}
          accessToken={triggerPublicApiKey}
          sectionTitles={sectionTitles}
          onComplete={handlePipelineComplete}
        />
      )}

      {/* Polling-based progress fallback */}
      {isGenerating && !triggerPublicApiKey && (
        <div className="mt-2 p-5 bg-gray-900 border border-gray-700 rounded-lg">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <div className="w-4 h-4 border-2 border-purple-500 border-t-transparent rounded-full animate-spin" />
              <span className="text-purple-400 font-medium text-sm">Generating lessons...</span>
            </div>
            <span className="text-gray-500 text-sm">
              {sectionsWithContent} / {totalSections} sections
            </span>
          </div>

          {/* Progress bar */}
          <div className="w-full bg-gray-800 rounded-full h-2 mb-4">
            <div
              className="h-2 rounded-full bg-purple-500 transition-all duration-700"
              style={{ width: `${totalSections > 0 ? (sectionsWithContent / totalSections) * 100 : 0}%` }}
            />
          </div>

          {/* Per-section status */}
          <div className="space-y-1">
            {sortedSections.map((section) => {
              const hasContent = section.content && section.content.trim().length > 0;
              return (
                <div key={section.id} className="flex items-center justify-between px-3 py-1.5 rounded text-sm">
                  <span className={hasContent ? 'text-green-400' : 'text-gray-500'}>
                    {section.position}. {section.title}
                  </span>
                  {hasContent && (
                    <span className="text-xs text-green-400">Done</span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Only show outline review UI when not generating */}
      {!isGenerating && (
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
            {sortedSections.map((section) => (
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
