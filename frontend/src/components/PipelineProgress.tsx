'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { Check } from 'lucide-react';
import { getCourse, resumeCourse, getPipelineStreamUrl, getSseTicket } from '@/lib/api';
import { Button } from '@/components/ui/button';

const STAGES = ['plan', 'research', 'verify', 'write', 'edit', 'complete'];

const STAGE_DISPLAY: Record<string, string> = {
  plan: 'Plan',
  research: 'Research',
  verify: 'Verify',
  write: 'Write',
  edit: 'Edit',
  complete: 'Complete',
};

// Map SSE stage names to our internal STAGES keys
const SSE_STAGE_MAP: Record<string, string> = {
  planning: 'plan',
  researching: 'research',
  verifying: 'verify',
  writing: 'write',
  editing: 'edit',
  completed: 'complete',
};

function getStageStatus(currentStage: string, stage: string): 'completed' | 'active' | 'pending' {
  const normalizedCurrent = SSE_STAGE_MAP[currentStage] || currentStage;
  const currentIdx = STAGES.indexOf(normalizedCurrent);
  const stageIdx = STAGES.indexOf(stage);
  if (stageIdx < currentIdx) return 'completed';
  if (stageIdx === currentIdx) return 'active';
  return 'pending';
}

interface LogEntry {
  message: string;
}

interface PipelineProgressProps {
  courseId: string;
  token: string | null;
  onComplete: () => void;
}

export default function PipelineProgress({
  courseId,
  token,
  onComplete,
}: PipelineProgressProps) {
  const [currentStage, setCurrentStage] = useState<string>('planning');
  const [currentSection, setCurrentSection] = useState(0);
  const [totalSections, setTotalSections] = useState(0);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isStale, setIsStale] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [connected, setConnected] = useState(false);
  const completeCalled = useRef(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  const addLog = useCallback((message: string) => {
    setLogs(prev => [...prev, { message }]);
  }, []);

  // Fallback: single getCourse fetch when SSE fails
  const fallbackFetch = useCallback(async () => {
    try {
      const data = await getCourse(courseId, token);
      if (data.pipeline_status) {
        setCurrentStage(data.pipeline_status.stage);
        setCurrentSection(data.pipeline_status.section);
        setTotalSections(data.pipeline_status.total);
      }
      if (
        (data.status === 'completed' || data.status === 'completed_partial') &&
        !completeCalled.current
      ) {
        completeCalled.current = true;
        onComplete();
      }
      if (data.status === 'failed' || data.pipeline_status?.error) {
        setError(data.pipeline_status?.error || 'Pipeline failed');
      }
      if (data.status === 'stale') {
        setIsStale(true);
      }
    } catch {
      // Fallback fetch failed silently
    }
  }, [courseId, token, onComplete]);

  useEffect(() => {
    if (!token) {
      fallbackFetch();
      return;
    }

    let cancelled = false;

    async function connect() {
      if (!token || cancelled) return;
      let ticket: string;
      try {
        ticket = await getSseTicket(token);
      } catch {
        fallbackFetch();
        return;
      }
      if (cancelled) return;

      const url = getPipelineStreamUrl(courseId, ticket);
      const es = new EventSource(url);
      eventSourceRef.current = es;

      es.onopen = () => {
        setConnected(true);
      };

      es.addEventListener('status', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          setCurrentStage(data.stage || 'planning');
          if (data.section !== undefined) setCurrentSection(data.section);
          if (data.total !== undefined) setTotalSections(data.total);
          if (data.message) addLog(data.message);
        } catch {
          // Ignore parse errors
        }
      });

      es.addEventListener('complete', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          setCurrentStage('completed');
          if (data.message) addLog(data.message);
        } catch {
          // Ignore parse errors
        }
        if (!completeCalled.current) {
          completeCalled.current = true;
          onComplete();
        }
        es.close();
      });

      es.addEventListener('stale', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          if (data.message) addLog(data.message);
        } catch {
          // Ignore parse errors
        }
        setIsStale(true);
        es.close();
      });

      es.addEventListener('error', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          setError(data.message || data.error || 'Pipeline error');
          if (data.message) addLog(data.message);
        } catch {
          // Ignore parse errors
        }
        es.close();
      });

      es.onerror = () => {
        setConnected(false);
        es.close();
        fallbackFetch();
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, [courseId, token, onComplete, addLog, fallbackFetch]);

  async function handleResume() {
    setResuming(true);
    setIsStale(false);
    setError(null);
    try {
      await resumeCourse(courseId, token);
      // Reconnect SSE by re-mounting — trigger effect by updating logs
      addLog('Resuming pipeline...');
      // Close old EventSource and reconnect
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      if (token) {
        const ticket = await getSseTicket(token);
        const url = getPipelineStreamUrl(courseId, ticket);
        const es = new EventSource(url);
        eventSourceRef.current = es;

        es.onopen = () => setConnected(true);

        es.addEventListener('status', (e: MessageEvent) => {
          try {
            const data = JSON.parse(e.data);
            setCurrentStage(data.stage || 'planning');
            if (data.section !== undefined) setCurrentSection(data.section);
            if (data.total !== undefined) setTotalSections(data.total);
            if (data.message) addLog(data.message);
          } catch {
            // Ignore parse errors
          }
        });

        es.addEventListener('complete', (e: MessageEvent) => {
          try {
            const data = JSON.parse(e.data);
            setCurrentStage('completed');
            if (data.message) addLog(data.message);
          } catch {
            // Ignore parse errors
          }
          if (!completeCalled.current) {
            completeCalled.current = true;
            onComplete();
          }
          es.close();
        });

        es.addEventListener('stale', (e: MessageEvent) => {
          try {
            const data = JSON.parse(e.data);
            if (data.message) addLog(data.message);
          } catch {
            // Ignore parse errors
          }
          setIsStale(true);
          es.close();
        });

        es.addEventListener('error', (e: MessageEvent) => {
          try {
            const data = JSON.parse(e.data);
            setError(data.message || data.error || 'Pipeline error');
            if (data.message) addLog(data.message);
          } catch {
            // Ignore parse errors
          }
          es.close();
        });

        es.onerror = () => {
          setConnected(false);
          es.close();
          fallbackFetch();
        };
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resume');
      setIsStale(true);
    } finally {
      setResuming(false);
    }
  }

  if (error && !isStale) {
    return (
      <div className="mt-6 p-4 bg-destructive/10 border border-destructive rounded-lg">
        <p className="text-destructive text-sm">Pipeline error: {error}</p>
      </div>
    );
  }

  if (isStale) {
    return (
      <div className="mt-6 space-y-4">
        <div className="p-4 bg-yellow-500/10 border border-yellow-500/50 rounded-lg">
          <p className="text-sm text-yellow-600 dark:text-yellow-400 mb-3">
            Pipeline stalled. The generation process stopped unexpectedly.
          </p>
          <Button
            size="sm"
            onClick={handleResume}
            disabled={resuming}
          >
            {resuming ? 'Resuming...' : 'Resume Pipeline'}
          </Button>
        </div>
      </div>
    );
  }

  const overallStage = currentStage;

  return (
    <div className="mt-6 space-y-4">
      {/* Horizontal pipeline nodes */}
      <div className="flex items-center justify-between px-2">
        {STAGES.map((stage, idx) => {
          const status = getStageStatus(overallStage, stage);
          return (
            <div key={stage} className="flex items-center flex-1 last:flex-none">
              {/* Node + label */}
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
                  {status === 'completed' && (
                    <Check className="w-4 h-4 text-white" />
                  )}
                  {status === 'active' && (
                    <div className="w-2 h-2 rounded-full bg-white" />
                  )}
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
                  {STAGE_DISPLAY[stage]}
                </span>
              </div>

              {/* Connecting line */}
              {idx < STAGES.length - 1 && (
                <div className="flex-1 mx-2 mb-6">
                  {(() => {
                    const nextStatus = getStageStatus(overallStage, STAGES[idx + 1]);
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

      {/* Activity log */}
      {logs.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-4 font-mono text-sm max-h-60 overflow-y-auto">
          {logs.map((log, i) => (
            <div key={i} className={i === logs.length - 1 ? 'text-foreground' : 'text-muted-foreground'}>
              {log.message}
            </div>
          ))}
        </div>
      )}

      {/* Footer */}
      {totalSections > 0 && (
        <p className="text-xs text-muted-foreground">
          Section {currentSection} of {totalSections}
        </p>
      )}

      {!completeCalled.current && !error && !isStale && (
        <div className="bg-muted/50 border border-border rounded-lg px-4 py-3 mt-2">
          <p className="text-sm text-muted-foreground">
            Our workers are busy crafting your course behind the scenes. Feel free to close this tab and grab a coffee — your course will be waiting for you in{' '}
            <a href="/library" className="text-primary hover:underline">your library</a> when it&apos;s ready.
          </p>
        </div>
      )}

      {!connected && !completeCalled.current && !error && !isStale && (
        <p className="text-xs text-muted-foreground">Waiting for pipeline to start...</p>
      )}
    </div>
  );
}
