'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { getDiscoverStreamUrl, getCourse } from '@/lib/api';
import { Navbar } from '@/components/Navbar';
import { Card, CardContent } from '@/components/ui/card';

interface SourceItem {
  url: string;
  title: string;
  snippet?: string;
}

interface QueryGroup {
  query: string;
  sources: SourceItem[];
  done: boolean;
}

interface PlannedSection {
  position: number;
  title: string;
  summary: string;
}

type ActiveStep = 'searching' | 'synthesizing' | 'planning' | 'complete' | 'error' | null;

export default function DiscoverPage() {
  const params = useParams();
  const router = useRouter();
  const { getToken } = useAuth();
  const courseId = params.id as string;

  const [topic, setTopic] = useState<string>('');
  const [queries, setQueries] = useState<QueryGroup[]>([]);
  const [totalSources, setTotalSources] = useState(0);
  const [keyConcepts, setKeyConcepts] = useState<string[]>([]);
  const [sections, setSections] = useState<PlannedSection[]>([]);
  const [activeStep, setActiveStep] = useState<ActiveStep>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [ungrounded, setUngrounded] = useState(false);

  const feedRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const receivedEvents = useRef(false);

  // Auto-scroll to bottom
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [queries, keyConcepts, sections, activeStep]);

  // Static fallback: fetch course data if no SSE events within 5s
  const fallbackToStatic = useCallback(async () => {
    if (receivedEvents.current) return;
    try {
      const token = await getToken();
      const course = await getCourse(courseId, token);
      setTopic(course.topic);
      if (course.sections && course.sections.length > 0) {
        setSections(
          course.sections
            .sort((a, b) => a.position - b.position)
            .map((s) => ({ position: s.position, title: s.title, summary: s.summary }))
        );
        setActiveStep('complete');
        setTimeout(() => router.push(`/courses/${courseId}`), 2500);
      }
    } catch {
      // Fallback fetch failed silently
    }
  }, [courseId, getToken, router]);

  useEffect(() => {
    let cancelled = false;

    async function connect() {
      const token = await getToken();
      if (!token || cancelled) return;

      const url = getDiscoverStreamUrl(courseId, token);
      const es = new EventSource(url);
      eventSourceRef.current = es;

      // Start fallback timer
      fallbackTimerRef.current = setTimeout(() => {
        fallbackToStatic();
      }, 5000);

      es.addEventListener('query', (e: MessageEvent) => {
        receivedEvents.current = true;
        if (fallbackTimerRef.current) clearTimeout(fallbackTimerRef.current);
        try {
          const data = JSON.parse(e.data);
          setActiveStep('searching');
          setQueries((prev) => [...prev, { query: data.query, sources: [], done: false }]);
        } catch {}
      });

      es.addEventListener('source', (e: MessageEvent) => {
        receivedEvents.current = true;
        try {
          const data = JSON.parse(e.data);
          setQueries((prev) => {
            const updated = [...prev];
            const idx = data.query_index;
            if (idx !== undefined && idx < updated.length) {
              const target = { ...updated[idx] };
              target.sources = [...target.sources, { url: data.url, title: data.title, snippet: data.snippet }];
              updated[idx] = target;
            } else if (updated.length > 0) {
              const last = { ...updated[updated.length - 1] };
              last.sources = [...last.sources, { url: data.url, title: data.title, snippet: data.snippet }];
              updated[updated.length - 1] = last;
            }
            return updated;
          });
          setTotalSources((prev) => prev + 1);
        } catch {}
      });

      es.addEventListener('query_done', () => {
        receivedEvents.current = true;
        setQueries((prev) => {
          if (prev.length === 0) return prev;
          const updated = [...prev];
          updated[updated.length - 1] = { ...updated[updated.length - 1], done: true };
          return updated;
        });
      });

      es.addEventListener('discovery_done', (e: MessageEvent) => {
        receivedEvents.current = true;
        try {
          const data = JSON.parse(e.data);
          if (data.total_sources !== undefined) setTotalSources(data.total_sources);
          if (data.topic) setTopic(data.topic);
        } catch {}
      });

      es.addEventListener('synthesizing', () => {
        receivedEvents.current = true;
        setActiveStep('synthesizing');
      });

      es.addEventListener('synthesis_done', (e: MessageEvent) => {
        receivedEvents.current = true;
        try {
          const data = JSON.parse(e.data);
          if (data.key_concepts) setKeyConcepts(data.key_concepts);
        } catch {}
      });

      es.addEventListener('planning', () => {
        receivedEvents.current = true;
        setActiveStep('planning');
      });

      es.addEventListener('section', (e: MessageEvent) => {
        receivedEvents.current = true;
        try {
          const data = JSON.parse(e.data);
          setSections((prev) => [
            ...prev,
            { position: data.position, title: data.title, summary: data.summary },
          ]);
        } catch {}
      });

      es.addEventListener('complete', (e: MessageEvent) => {
        receivedEvents.current = true;
        try {
          const data = JSON.parse(e.data);
          if (data.topic) setTopic(data.topic);
        } catch {}
        setActiveStep('complete');
        es.close();
        setTimeout(() => {
          if (!cancelled) router.push(`/courses/${courseId}`);
        }, 2500);
      });

      es.addEventListener('ungrounded', () => {
        receivedEvents.current = true;
        setUngrounded(true);
      });

      es.addEventListener('error', (e: MessageEvent) => {
        receivedEvents.current = true;
        try {
          const data = JSON.parse(e.data);
          setErrorMessage(data.message || data.error || 'Discovery failed');
        } catch {
          setErrorMessage('Discovery failed');
        }
        setActiveStep('error');
        es.close();
      });

      es.onerror = () => {
        es.close();
        fallbackToStatic();
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      if (fallbackTimerRef.current) {
        clearTimeout(fallbackTimerRef.current);
      }
    };
  }, [courseId, getToken, router, fallbackToStatic]);

  return (
    <>
      <Navbar />
      <div className="max-w-[720px] mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-semibold">{topic || 'Discovering...'}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {activeStep === 'complete'
              ? 'Discovery complete'
              : activeStep === 'error'
                ? 'An error occurred'
                : 'Researching sources and building your course outline...'}
          </p>
        </div>

        {/* Feed */}
        <div ref={feedRef} className="space-y-4 max-h-[70vh] overflow-y-auto pr-1">
          {/* Search queries section */}
          {queries.length > 0 && (
            <Card>
              <CardContent>
                <div className="flex items-center gap-2 mb-3">
                  {activeStep === 'searching' && <PulsingDot />}
                  <span className="text-xs uppercase tracking-wider text-muted-foreground font-medium">
                    Search Queries
                  </span>
                </div>
                <div className="space-y-3">
                  {queries.map((q, i) => (
                    <div key={i}>
                      <div className="flex items-center gap-2">
                        <code className="text-sm font-mono text-foreground bg-muted px-2 py-0.5 rounded">
                          {q.query}
                        </code>
                        {q.done && (
                          <span className="text-xs text-green-500">
                            {q.sources.length} sources
                          </span>
                        )}
                      </div>
                      {q.sources.length > 0 && (
                        <div className="ml-4 mt-1.5 space-y-1">
                          {q.sources.map((s, j) => (
                            <div key={j} className="text-sm">
                              <a
                                href={s.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-primary hover:underline"
                              >
                                {s.title || s.url}
                              </a>
                              {s.snippet && (
                                <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                                  {s.snippet}
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Discovery summary */}
          {totalSources > 0 && activeStep !== 'searching' && (
            <Card>
              <CardContent>
                <span className="text-xs uppercase tracking-wider text-muted-foreground font-medium">
                  Discovery Summary
                </span>
                <p className="text-sm text-foreground mt-1">
                  Found {totalSources} source{totalSources !== 1 ? 's' : ''} across{' '}
                  {queries.length} quer{queries.length !== 1 ? 'ies' : 'y'}
                </p>
              </CardContent>
            </Card>
          )}

          {/* Synthesis section */}
          {(activeStep === 'synthesizing' || keyConcepts.length > 0) && (
            <Card>
              <CardContent>
                <div className="flex items-center gap-2 mb-2">
                  {activeStep === 'synthesizing' && keyConcepts.length === 0 && <PulsingDot />}
                  <span className="text-xs uppercase tracking-wider text-muted-foreground font-medium">
                    Key Concepts
                  </span>
                </div>
                {keyConcepts.length > 0 ? (
                  <div className="flex flex-wrap gap-2">
                    {keyConcepts.map((concept, i) => (
                      <span
                        key={i}
                        className="text-sm bg-primary/10 text-primary px-2.5 py-1 rounded-full"
                      >
                        {concept}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">Synthesizing sources...</p>
                )}
              </CardContent>
            </Card>
          )}

          {/* Planning section */}
          {(activeStep === 'planning' || sections.length > 0) && (
            <Card>
              <CardContent>
                <div className="flex items-center gap-2 mb-3">
                  {activeStep === 'planning' && <PulsingDot />}
                  <span className="text-xs uppercase tracking-wider text-muted-foreground font-medium">
                    Course Outline
                  </span>
                </div>
                <div className="space-y-2">
                  {sections.map((s) => (
                    <div
                      key={s.position}
                      className="transition-opacity duration-500"
                      style={{ opacity: 1 }}
                    >
                      <div className="flex items-baseline gap-2">
                        <span className="text-xs font-mono text-muted-foreground w-5 text-right shrink-0">
                          {s.position}.
                        </span>
                        <div>
                          <span className="text-sm font-medium text-foreground">{s.title}</span>
                          <p className="text-xs text-muted-foreground mt-0.5">{s.summary}</p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Ungrounded warning */}
          {ungrounded && (
            <div className="p-3 bg-yellow-500/10 border border-yellow-500/50 rounded-lg">
              <p className="text-sm text-yellow-600 dark:text-yellow-400">
                Some topics may have limited source coverage. The course will still be generated, but some sections may rely more on general knowledge.
              </p>
            </div>
          )}

          {/* Completion message */}
          {activeStep === 'complete' && (
            <div className="p-4 bg-green-500/10 border border-green-500/50 rounded-lg text-center">
              <p className="text-sm text-green-600 dark:text-green-400 font-medium">
                Outline ready! Redirecting...
              </p>
            </div>
          )}

          {/* Error message */}
          {activeStep === 'error' && errorMessage && (
            <div className="p-4 bg-destructive/10 border border-destructive rounded-lg">
              <p className="text-sm text-destructive">{errorMessage}</p>
            </div>
          )}
        </div>
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
