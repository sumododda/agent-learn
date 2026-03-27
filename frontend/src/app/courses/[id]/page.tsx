'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { getCourse, generateCourse, regenerateCourse } from '@/lib/api';
import { Course } from '@/lib/types';
import PipelineProgress from '@/components/PipelineProgress';
import { Navbar } from '@/components/Navbar';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';

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
  const [token, setToken] = useState<string | null>(null);

  // Comments
  const [overallComment, setOverallComment] = useState('');
  const [sectionComments, setSectionComments] = useState<Record<number, string>>({});

  useEffect(() => {
    async function loadCourse() {
      try {
        const t = await getToken();
        setToken(t);
        const data = await getCourse(courseId, t);
        setCourse(data);

        // If course is in any active pipeline state, show progress
        if (['generating', 'researching', 'writing', 'verifying', 'editing', 'stale'].includes(data.status)) {
          setGenerating(true);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load course');
      } finally {
        setLoading(false);
      }
    }
    loadCourse();
  }, [courseId, getToken]);

  async function handleApprove() {
    setGenerating(true);
    setError(null);
    try {
      const t = await getToken();
      setToken(t);
      await generateCourse(courseId, t);
      router.push(`/courses/${courseId}/generating`);
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
      const t = await getToken();
      const comments = Object.entries(sectionComments)
        .filter(([, comment]) => comment.trim())
        .map(([position, comment]) => ({ position: Number(position), comment }));

      await regenerateCourse(
        courseId,
        overallComment.trim() || undefined,
        comments.length > 0 ? comments : undefined,
        t
      );
      // Re-fetch to get the latest course data after regeneration
      const refreshed = await getCourse(courseId, t);
      setCourse(refreshed);
      setOverallComment('');
      setSectionComments({});
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to regenerate');
    } finally {
      setRegenerating(false);
    }
  }

  const hasComments = overallComment.trim() || Object.values(sectionComments).some(c => c.trim());
  const busy = generating || regenerating;

  if (loading) return (
    <>
      <Navbar />
      <div className="text-center text-muted-foreground mt-20">Loading course...</div>
    </>
  );
  if (error && !course) return (
    <>
      <Navbar />
      <div className="text-center text-destructive mt-20">{error}</div>
    </>
  );
  if (!course) return (
    <>
      <Navbar />
      <div className="text-center text-muted-foreground mt-20">Course not found</div>
    </>
  );

  if (course.status === 'completed' || course.status === 'completed_partial') {
    router.push(`/courses/${courseId}/learn`);
    return null;
  }

  const sortedSections = [...course.sections].sort((a, b) => a.position - b.position);
  const isGenerating = generating || course.status === 'generating';
  const totalSections = sortedSections.length;

  return (
    <>
      <Navbar />
      <div className="max-w-[720px] mx-auto px-4 py-8">
        <h1 className="text-2xl font-semibold">{course.topic}</h1>
        <p className="text-sm text-muted-foreground mb-6">{totalSections} sections · Review your course outline</p>

        {/* Pipeline progress via polling */}
        {isGenerating && (
          <PipelineProgress
            courseId={courseId}
            token={token}
            onComplete={handlePipelineComplete}
          />
        )}

        {/* Only show outline review UI when not generating */}
        {!isGenerating && (
          <>
            {/* Overall comment */}
            <div className="mb-6">
              <Textarea
                value={overallComment}
                onChange={(e) => setOverallComment(e.target.value)}
                placeholder="Overall feedback on the outline... (optional)"
                rows={2}
                disabled={busy}
                className="resize-none text-sm"
              />
            </div>

            {/* Section cards */}
            <div className="space-y-3 mb-8">
              {sortedSections.map((section) => (
                <Card key={section.id} className="p-4">
                  <div className="font-medium text-foreground">{section.position}. {section.title}</div>
                  <p className="text-sm text-muted-foreground mt-1">{section.summary}</p>
                  <Input
                    className="mt-2"
                    value={sectionComments[section.position] || ''}
                    onChange={(e) =>
                      setSectionComments((prev) => ({ ...prev, [section.position]: e.target.value }))
                    }
                    placeholder="Comment on this section..."
                    disabled={busy}
                  />
                </Card>
              ))}
            </div>

            {error && <p className="text-sm text-destructive mb-4">{error}</p>}

            <div className="flex gap-3">
              <Button className="flex-1" onClick={handleApprove} disabled={busy}>
                {generating ? 'Generating lessons...' : 'Approve & Generate'}
              </Button>
              <Button variant="outline" onClick={handleRegenerate} disabled={busy}>
                {regenerating ? 'Regenerating...' : hasComments ? 'Regenerate with Feedback' : 'Regenerate'}
              </Button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
