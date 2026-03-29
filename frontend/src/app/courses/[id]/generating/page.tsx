'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { Check } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { getPipelineStreamUrl, getCourse, resumeCourse, getSseTicket } from '@/lib/api';
import { Navbar } from '@/components/Navbar';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

type SectionStage = 'pending' | 'researching' | 'verifying' | 'writing' | 'editing' | 'done';

interface SectionState {
  position: number;
  title: string;
  stage: SectionStage;
  events: string[];
  collapsed: boolean;
}

type OverallStage = 'starting' | 'researching' | 'verifying' | 'writing' | 'editing' | 'complete' | 'stale' | 'error';

const PIPELINE_STAGES = ['plan', 'research', 'verify', 'write', 'edit', 'complete'] as const;
const PIPELINE_DISPLAY: Record<string, string> = {
  plan: 'Plan',
  research: 'Research',
  verify: 'Verify',
  write: 'Write',
  edit: 'Edit',
  complete: 'Complete',
};
const OVERALL_TO_PIPELINE: Record<OverallStage, string> = {
  starting: 'plan',
  researching: 'research',
  verifying: 'verify',
  writing: 'write',
  editing: 'edit',
  complete: 'complete',
  stale: '',
  error: '',
};

function getPipelineStageStatus(current: string, stage: string): 'completed' | 'active' | 'pending' {
  const currentIdx = PIPELINE_STAGES.indexOf(current as typeof PIPELINE_STAGES[number]);
  const stageIdx = PIPELINE_STAGES.indexOf(stage as typeof PIPELINE_STAGES[number]);
  if (currentIdx < 0) return 'pending';
  if (stageIdx < currentIdx) return 'completed';
  if (stageIdx === currentIdx) return 'active';
  return 'pending';
}

const STAGE_LABELS: Record<SectionStage, string> = {
  pending: 'Pending',
  researching: 'Researching',
  verifying: 'Verifying',
  writing: 'Writing',
  editing: 'Editing',
  done: 'Done',
};

const STAGE_COLORS: Record<SectionStage, string> = {
  pending: 'bg-muted text-muted-foreground',
  researching: 'bg-blue-500/20 text-blue-500',
  verifying: 'bg-amber-500/20 text-amber-500',
  writing: 'bg-purple-500/20 text-purple-500',
  editing: 'bg-cyan-500/20 text-cyan-500',
  done: 'bg-green-500/20 text-green-500',
};

const OVERALL_LABELS: Record<OverallStage, string> = {
  starting: 'Starting pipeline...',
  researching: 'Researching sections...',
  verifying: 'Verifying content...',
  writing: 'Writing lessons...',
  editing: 'Editing & polishing...',
  complete: 'Complete!',
  stale: 'Pipeline stalled',
  error: 'Error',
};

export default function GeneratingPage() {
  const params = useParams();
  const router = useRouter();
  const { getToken } = useAuth();
  const courseId = params.id as string;

  const [topic, setTopic] = useState<string>('');
  const [sectionStates, setSectionStates] = useState<SectionState[]>([]);
  const [overallStage, setOverallStage] = useState<OverallStage>('starting');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [resuming, setResuming] = useState(false);
  const [hasAcademicSearch, setHasAcademicSearch] = useState(false);

  const feedRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const completeCalled = useRef(false);

  // Auto-scroll to bottom
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [sectionStates, overallStage]);

  // Initialize section states from course data
  const initSections = useCallback(async () => {
    try {
      const token = await getToken();
      const course = await getCourse(courseId, token);
      setTopic(course.topic);
      setHasAcademicSearch(!!course.academic_search?.enabled);
      if (course.sections && course.sections.length > 0) {
        setSectionStates((prev) => {
          if (prev.length > 0) return prev; // Already initialized
          return course.sections
            .sort((a, b) => a.position - b.position)
            .map((s) => ({
              position: s.position,
              title: s.title,
              stage: s.content ? 'done' : ('pending' as SectionStage),
              events: [],
              collapsed: true,
            }));
        });

        // If already completed, redirect
        if (course.status === 'completed' || course.status === 'completed_partial') {
          if (!completeCalled.current) {
            completeCalled.current = true;
            setOverallStage('complete');
            setTimeout(() => router.push(`/courses/${courseId}/learn`), 2500);
          }
        }
        if (course.status === 'stale') {
          setOverallStage('stale');
        }
      }
    } catch {
      // Silent
    }
  }, [courseId, getToken, router]);

  const updateSection = useCallback(
    (position: number, stage: SectionStage, event?: string) => {
      setSectionStates((prev) => {
        const updated = prev.map((s) => {
          if (s.position === position) {
            return {
              ...s,
              stage,
              events: event ? [...s.events, event] : s.events,
              collapsed: stage === 'done',
            };
          }
          return s;
        });
        return updated;
      });
    },
    []
  );

  const connectSSE = useCallback(
    async (token: string) => {
      let ticket: string;
      try {
        ticket = await getSseTicket(token);
      } catch {
        initSections();
        return null;
      }
      const url = getPipelineStreamUrl(courseId, ticket);
      const es = new EventSource(url);
      eventSourceRef.current = es;

      // Existing events
      es.addEventListener('status', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          if (data.stage) {
            const stageMap: Record<string, OverallStage> = {
              planning: 'starting',
              researching: 'researching',
              verifying: 'verifying',
              writing: 'writing',
              editing: 'editing',
            };
            setOverallStage(stageMap[data.stage] || 'starting');
          }
        } catch {}
      });

      es.addEventListener('complete', () => {
        setOverallStage('complete');
        if (!completeCalled.current) {
          completeCalled.current = true;
          setTimeout(() => router.push(`/courses/${courseId}/learn`), 2500);
        }
        es.close();
      });

      es.addEventListener('stale', () => {
        setOverallStage('stale');
        es.close();
      });

      es.addEventListener('error', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          setErrorMessage(data.message || data.error || 'Pipeline error');
        } catch {
          setErrorMessage('Pipeline error');
        }
        setOverallStage('error');
        es.close();
      });

      // Granular events
      es.addEventListener('pipeline_start', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          if (data.topic) setTopic(data.topic);
        } catch {}
        setOverallStage('starting');
      });

      es.addEventListener('research_start', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          setOverallStage('researching');
          if (data.section !== undefined) {
            updateSection(data.section, 'researching', 'Starting research...');
          }
        } catch {}
      });

      es.addEventListener('research_done', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          if (data.section !== undefined) {
            const msg = data.sources_found
              ? `Research complete: ${data.sources_found} sources found`
              : 'Research complete';
            updateSection(data.section, 'researching', msg);
          }
        } catch {}
      });

      es.addEventListener('verify_start', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          setOverallStage('verifying');
          if (data.section !== undefined) {
            updateSection(data.section, 'verifying', 'Verifying sources...');
          }
        } catch {}
      });

      es.addEventListener('verify_done', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          if (data.section !== undefined) {
            const msg = data.verified_count !== undefined
              ? `Verified ${data.verified_count} claims`
              : 'Verification complete';
            updateSection(data.section, 'verifying', msg);
          }
        } catch {}
      });

      es.addEventListener('write_start', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          setOverallStage('writing');
          if (data.section !== undefined) {
            updateSection(data.section, 'writing', 'Writing lesson content...');
          }
        } catch {}
      });

      es.addEventListener('write_done', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          if (data.section !== undefined) {
            updateSection(data.section, 'writing', 'Lesson written');
          }
        } catch {}
      });

      es.addEventListener('edit_start', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          setOverallStage('editing');
          if (data.section !== undefined) {
            updateSection(data.section, 'editing', 'Editing & polishing...');
          }
        } catch {}
      });

      es.addEventListener('edit_done', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          if (data.section !== undefined) {
            updateSection(data.section, 'done', 'Section complete');
          }
        } catch {}
      });

      es.addEventListener('pipeline_complete', () => {
        setOverallStage('complete');
        // Mark all sections as done
        setSectionStates((prev) =>
          prev.map((s) => ({ ...s, stage: 'done' as SectionStage, collapsed: true }))
        );
        if (!completeCalled.current) {
          completeCalled.current = true;
          setTimeout(() => router.push(`/courses/${courseId}/learn`), 2500);
        }
        es.close();
      });

      es.onerror = () => {
        es.close();
        // Fallback: fetch course state
        initSections();
      };

      return es;
    },
    [courseId, router, updateSection, initSections]
  );

  useEffect(() => {
    let cancelled = false;

    async function start() {
      // Initialize sections from course data first
      await initSections();

      const token = await getToken();
      if (!token || cancelled) return;

      await connectSSE(token);
    }

    start();

    return () => {
      cancelled = true;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, [courseId, getToken, initSections, connectSSE]);

  async function handleResume() {
    setResuming(true);
    setOverallStage('starting');
    setErrorMessage(null);
    completeCalled.current = false;
    try {
      const token = await getToken();
      await resumeCourse(courseId, token);
      // Reconnect SSE
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
      if (token) {
        await connectSSE(token);
      }
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Failed to resume');
      setOverallStage('stale');
    } finally {
      setResuming(false);
    }
  }

  function toggleSection(position: number) {
    setSectionStates((prev) =>
      prev.map((s) =>
        s.position === position ? { ...s, collapsed: !s.collapsed } : s
      )
    );
  }

  const doneCount = sectionStates.filter((s) => s.stage === 'done').length;
  const totalCount = sectionStates.length;

  return (
    <>
      <Navbar />
      {overallStage !== 'complete' && overallStage !== 'error' && overallStage !== 'stale' && (
        <div className="w-full bg-primary/10 border-b border-primary/20 px-4 py-2 text-center text-sm text-primary">
          Our workers are busy crafting your course behind the scenes{hasAcademicSearch ? ', scouring research papers from Semantic Scholar, arXiv & OpenAlex' : ''}. Feel free to close this tab and grab a coffee — your course will be waiting for you in your library when it&apos;s ready.
        </div>
      )}
      <div className="max-w-[720px] mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-semibold">{topic || 'Generating...'}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {OVERALL_LABELS[overallStage]}
            {totalCount > 0 && overallStage !== 'complete' && overallStage !== 'error' && overallStage !== 'stale' && (
              <span className="ml-2">
                ({doneCount}/{totalCount} sections)
              </span>
            )}
          </p>
        </div>

        {/* Horizontal pipeline stages */}
        <div className="flex items-center justify-between px-2 mb-6">
          {PIPELINE_STAGES.map((stage, idx) => {
            const pipelineStage = OVERALL_TO_PIPELINE[overallStage] || '';
            const status = getPipelineStageStatus(pipelineStage, stage);
            return (
              <div key={stage} className="flex items-center flex-1 last:flex-none">
                <div className="flex flex-col items-center gap-1.5">
                  <div
                    className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${
                      status === 'completed'
                        ? 'bg-green-500'
                        : status === 'active'
                        ? 'bg-primary'
                        : 'border border-border'
                    }`}
                  >
                    {status === 'completed' && <Check className="w-4 h-4 text-white" />}
                    {status === 'active' && <div className="w-2 h-2 rounded-full bg-white" />}
                  </div>
                  <span
                    className={`text-xs font-medium ${
                      status === 'completed'
                        ? 'text-green-500'
                        : status === 'active'
                        ? 'text-primary'
                        : 'text-muted-foreground'
                    }`}
                  >
                    {PIPELINE_DISPLAY[stage]}
                  </span>
                </div>
                {idx < PIPELINE_STAGES.length - 1 && (
                  <div className="flex-1 mx-2 mb-6">
                    {(() => {
                      const nextStatus = getPipelineStageStatus(pipelineStage, PIPELINE_STAGES[idx + 1]);
                      if (status === 'completed' && nextStatus === 'completed') {
                        return <div className="h-0.5 bg-green-500 w-full" />;
                      }
                      if (status === 'completed' && nextStatus === 'active') {
                        return <div className="h-0.5 bg-primary w-full" />;
                      }
                      return <div className="w-full border-t border-dashed border-border" />;
                    })()}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Section progress */}
        {totalCount > 0 && overallStage !== 'complete' && overallStage !== 'error' && overallStage !== 'stale' && (
          <p className="text-xs text-muted-foreground mb-4">
            {doneCount}/{totalCount} sections complete
          </p>
        )}

        {/* Section cards feed */}
        <div ref={feedRef} className="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
          {sectionStates.map((section) => (
            <Card key={section.position}>
              <CardContent>
                <button
                  type="button"
                  className="flex items-center justify-between w-full text-left"
                  onClick={() => toggleSection(section.position)}
                >
                  <div className="flex items-center gap-3">
                    <span className="text-xs font-mono text-muted-foreground w-5 text-right shrink-0">
                      {section.position}.
                    </span>
                    <span className="text-sm font-medium text-foreground">{section.title}</span>
                  </div>
                  <span
                    className={`text-xs font-medium px-2 py-0.5 rounded-full shrink-0 ${
                      STAGE_COLORS[section.stage]
                    }`}
                  >
                    {section.stage !== 'pending' && section.stage !== 'done' && (
                      <span className="inline-block w-1.5 h-1.5 rounded-full bg-current mr-1 animate-pulse" />
                    )}
                    {STAGE_LABELS[section.stage]}
                  </span>
                </button>

                {/* Collapsible event log */}
                {!section.collapsed && section.events.length > 0 && (
                  <div className="mt-3 ml-8 space-y-1 border-l-2 border-border pl-3">
                    {section.events.map((event, i) => (
                      <p
                        key={i}
                        className={`text-xs ${
                          i === section.events.length - 1
                            ? 'text-foreground'
                            : 'text-muted-foreground'
                        }`}
                      >
                        {event}
                      </p>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Completion message */}
        {overallStage === 'complete' && (
          <div className="mt-6 p-4 bg-green-500/10 border border-green-500/50 rounded-lg text-center">
            <p className="text-sm text-green-600 dark:text-green-400 font-medium">
              Course ready! Redirecting...
            </p>
          </div>
        )}

        {/* Stale state */}
        {overallStage === 'stale' && (
          <div className="mt-6 p-4 bg-yellow-500/10 border border-yellow-500/50 rounded-lg">
            <p className="text-sm text-yellow-600 dark:text-yellow-400 mb-3">
              Pipeline stalled. The generation process stopped unexpectedly.
            </p>
            <Button size="sm" onClick={handleResume} disabled={resuming}>
              {resuming ? 'Resuming...' : 'Resume Pipeline'}
            </Button>
          </div>
        )}

        {/* Error state */}
        {overallStage === 'error' && errorMessage && (
          <div className="mt-6 p-4 bg-destructive/10 border border-destructive rounded-lg">
            <p className="text-sm text-destructive">{errorMessage}</p>
          </div>
        )}
      </div>
    </>
  );
}

function PulsingDot() {
  return (
    <span className="relative flex h-2.5 w-2.5">
      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75" />
      <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-primary" />
    </span>
  );
}
