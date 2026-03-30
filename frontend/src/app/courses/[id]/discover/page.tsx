'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { getDiscoverStreamUrl, getCourse, getSseTicket } from '@/lib/api';
import { Navbar } from '@/components/Navbar';
import { Card, CardContent } from '@/components/ui/card';

function safeHref(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return url;
  } catch {}
  return '#';
}

interface SourceItem {
  url: string;
  title: string;
  snippet?: string;
  authors?: string[];
  year?: number | null;
  venue?: string | null;
  citations?: number | null;
}

interface QueryGroup {
  index: number;
  query: string;
  sources: SourceItem[];
  done: boolean;
  providers?: string[];
}

interface PlannedSection {
  position: number;
  title: string;
  summary: string;
}

type ActiveStep = 'searching' | 'synthesizing' | 'planning' | 'complete' | 'error' | null;

function upsertQueryGroup(
  prev: QueryGroup[],
  index: number,
  query: string,
  providers?: string[],
): QueryGroup[] {
  const existing = prev.find((item) => item.index === index);
  if (existing) {
    return prev
      .map((item) => (
        item.index === index
          ? {
              ...item,
              query: query || item.query,
              providers: providers ?? item.providers,
            }
          : item
      ))
      .sort((a, b) => a.index - b.index);
  }
  return [...prev, { index, query, providers, sources: [], done: false }].sort((a, b) => a.index - b.index);
}

function appendSourceToGroup(prev: QueryGroup[], index: number, source: SourceItem): QueryGroup[] {
  const existing = prev.find((item) => item.index === index);
  if (!existing) {
    return [...prev, { index, query: '', sources: [source], done: false }].sort((a, b) => a.index - b.index);
  }
  return prev.map((item) => (
    item.index === index
      ? { ...item, sources: [...item.sources, source] }
      : item
  ));
}

function markQueryDone(prev: QueryGroup[], index: number): QueryGroup[] {
  const existing = prev.find((item) => item.index === index);
  if (!existing) {
    return [...prev, { index, query: '', sources: [], done: true }].sort((a, b) => a.index - b.index);
  }
  return prev.map((item) => (
    item.index === index
      ? { ...item, done: true }
      : item
  ));
}

export default function DiscoverPage() {
  const params = useParams();
  const router = useRouter();
  const { getToken } = useAuth();
  const courseId = params.id as string;

  const [topic, setTopic] = useState<string>('');
  const [webQueries, setWebQueries] = useState<QueryGroup[]>([]);
  const [academicQueries, setAcademicQueries] = useState<QueryGroup[]>([]);
  const [webSourceCount, setWebSourceCount] = useState(0);
  const [academicSourceCount, setAcademicSourceCount] = useState(0);
  const [keyConcepts, setKeyConcepts] = useState<string[]>([]);
  const [sections, setSections] = useState<PlannedSection[]>([]);
  const [activeStep, setActiveStep] = useState<ActiveStep>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [ungrounded, setUngrounded] = useState(false);
  const [hasAcademicResearch, setHasAcademicResearch] = useState(false);

  const feedRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const receivedEvents = useRef(false);
  const totalSources = webSourceCount + academicSourceCount;

  // Auto-scroll to bottom
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [webQueries, academicQueries, keyConcepts, sections, activeStep]);

  // Static fallback: fetch course data if no SSE events within 5s
  const fallbackToStatic = useCallback(async () => {
    if (receivedEvents.current) return;
    try {
      const token = await getToken();
      const course = await getCourse(courseId, token);
      setTopic(course.topic);
      setHasAcademicResearch(!!course.academic_search?.enabled);
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

      void getCourse(courseId, token)
        .then((course) => {
          if (cancelled) return;
          setTopic((prev) => prev || course.topic);
          setHasAcademicResearch(!!course.academic_search?.enabled);
        })
        .catch(() => {});

      let ticket: string;
      try {
        ticket = await getSseTicket(token);
      } catch {
        fallbackToStatic();
        return;
      }
      if (cancelled) return;

      const url = getDiscoverStreamUrl(courseId, ticket);
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
          setWebQueries((prev) => upsertQueryGroup(prev, data.index ?? prev.length, data.query));
        } catch {}
      });

      es.addEventListener('source', (e: MessageEvent) => {
        receivedEvents.current = true;
        try {
          const data = JSON.parse(e.data);
          setWebQueries((prev) => appendSourceToGroup(prev, data.query_index ?? 0, {
            url: data.url,
            title: data.title,
            snippet: data.snippet,
          }));
          setWebSourceCount((prev) => prev + 1);
        } catch {}
      });

      es.addEventListener('query_done', (e: MessageEvent) => {
        receivedEvents.current = true;
        try {
          const data = JSON.parse(e.data);
          setWebQueries((prev) => markQueryDone(prev, data.index ?? 0));
        } catch {}
      });

      es.addEventListener('academic_query', (e: MessageEvent) => {
        receivedEvents.current = true;
        try {
          const data = JSON.parse(e.data);
          setHasAcademicResearch(true);
          setActiveStep('searching');
          setAcademicQueries((prev) => upsertQueryGroup(prev, data.index ?? prev.length, data.query, data.providers));
        } catch {}
      });

      es.addEventListener('academic_source', (e: MessageEvent) => {
        receivedEvents.current = true;
        try {
          const data = JSON.parse(e.data);
          setHasAcademicResearch(true);
          setAcademicQueries((prev) => appendSourceToGroup(prev, data.query_index ?? 0, {
            url: data.url,
            title: data.title,
            snippet: data.snippet,
            authors: data.authors,
            year: data.year,
            venue: data.venue,
            citations: data.citations,
          }));
          setAcademicSourceCount((prev) => prev + 1);
        } catch {}
      });

      es.addEventListener('academic_query_done', (e: MessageEvent) => {
        receivedEvents.current = true;
        try {
          const data = JSON.parse(e.data);
          setAcademicQueries((prev) => markQueryDone(prev, data.index ?? 0));
        } catch {}
      });

      es.addEventListener('discovery_done', (e: MessageEvent) => {
        receivedEvents.current = true;
        try {
          const data = JSON.parse(e.data);
          if (data.web_sources !== undefined) setWebSourceCount(data.web_sources);
          if (data.academic_sources !== undefined) setAcademicSourceCount(data.academic_sources);
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
          {/* Web search section */}
          {webQueries.length > 0 && (
            <Card>
              <CardContent>
                <div className="flex items-center gap-2 mb-3">
                  {activeStep === 'searching' && <PulsingDot />}
                  <span className="text-xs uppercase tracking-wider text-muted-foreground font-medium">
                    Web Search
                  </span>
                </div>
                <div className="space-y-3">
                  {webQueries.map((q) => (
                    <div key={`web-${q.index}`}>
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
                                href={safeHref(s.url)}
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

          {/* Academic research section */}
          {hasAcademicResearch && (
            <Card>
              <CardContent>
                <div className="flex items-center gap-2 mb-3">
                  {activeStep === 'searching' && <PulsingDot />}
                  <span className="text-xs uppercase tracking-wider text-muted-foreground font-medium">
                    Academic Research
                  </span>
                </div>
                {academicQueries.length > 0 ? (
                  <div className="space-y-3">
                    {academicQueries.map((q) => (
                      <div key={`academic-${q.index}`}>
                        <div className="flex flex-wrap items-center gap-2">
                          <code className="text-sm font-mono text-foreground bg-muted px-2 py-0.5 rounded">
                            {q.query}
                          </code>
                          {q.done && (
                            <span className="text-xs text-green-500">
                              {q.sources.length} paper{q.sources.length !== 1 ? 's' : ''}
                            </span>
                          )}
                        </div>
                        {q.providers && q.providers.length > 0 && (
                          <p className="text-xs text-muted-foreground mt-1">
                            Providers: {q.providers.join(', ')}
                          </p>
                        )}
                        {q.sources.length > 0 && (
                          <div className="ml-4 mt-1.5 space-y-2">
                            {q.sources.map((s, j) => (
                              <div key={j} className="text-sm">
                                <a
                                  href={safeHref(s.url)}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-primary hover:underline"
                                >
                                  {s.title || s.url}
                                </a>
                                {(s.year || s.venue || s.citations != null) && (
                                  <p className="text-xs text-muted-foreground mt-0.5">
                                    {[s.year, s.venue, s.citations != null ? `${s.citations} citations` : null]
                                      .filter(Boolean)
                                      .join(' • ')}
                                  </p>
                                )}
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
                ) : (
                  <p className="text-sm text-muted-foreground">Preparing academic research...</p>
                )}
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
                  Found {webSourceCount} web source{webSourceCount !== 1 ? 's' : ''}
                  {hasAcademicResearch && ` and ${academicSourceCount} academic paper${academicSourceCount !== 1 ? 's' : ''}`}
                  {' '}across {(webQueries.length || academicQueries.length)} quer{(webQueries.length || academicQueries.length) !== 1 ? 'ies' : 'y'}
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
