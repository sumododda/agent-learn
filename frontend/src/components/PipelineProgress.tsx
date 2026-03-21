'use client';

import { useEffect, useRef, useState } from 'react';
import { Check } from 'lucide-react';
import { getCourse } from '@/lib/api';
import { Course, PipelineStatus } from '@/lib/types';

const STAGE_LABELS: Record<string, string> = {
  researching: 'Researching',
  verifying: 'Verifying',
  writing: 'Writing',
  editing: 'Editing',
  completed: 'Completed',
  failed: 'Failed',
};

const STAGES = ['research', 'verify', 'write', 'edit', 'complete'];

const STAGE_DISPLAY: Record<string, string> = {
  research: 'Research',
  verify: 'Verify',
  write: 'Write',
  edit: 'Edit',
  complete: 'Complete',
};

function getStageStatus(currentStage: string, stage: string): 'completed' | 'active' | 'pending' {
  const stageMap: Record<string, string> = {
    researching: 'research',
    verifying: 'verify',
    writing: 'write',
    editing: 'edit',
    completed: 'complete',
  };
  const normalizedCurrent = stageMap[currentStage] || currentStage;
  const currentIdx = STAGES.indexOf(normalizedCurrent);
  const stageIdx = STAGES.indexOf(stage);
  if (stageIdx < currentIdx) return 'completed';
  if (stageIdx === currentIdx) return 'active';
  return 'pending';
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
  const [course, setCourse] = useState<Course | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const completeCalled = useRef(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    async function poll() {
      try {
        const data = await getCourse(courseId, token);
        setCourse(data);
        if (data.pipeline_status) {
          setPipelineStatus(data.pipeline_status);
        }

        if (
          (data.status === 'completed' || data.status === 'completed_partial') &&
          !completeCalled.current
        ) {
          completeCalled.current = true;
          if (intervalRef.current) clearInterval(intervalRef.current);
          onComplete();
        }

        if (data.status === 'failed' || data.pipeline_status?.error) {
          setError(data.pipeline_status?.error || 'Pipeline failed');
          if (intervalRef.current) clearInterval(intervalRef.current);
        }
      } catch {
        // Poll failure is non-critical, will retry
      }
    }

    poll(); // Initial fetch
    intervalRef.current = setInterval(poll, 4000);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [courseId, token, onComplete]);

  if (error) {
    return (
      <div className="mt-6 p-4 bg-destructive/10 border border-destructive rounded-lg">
        <p className="text-destructive text-sm">Pipeline error: {error}</p>
      </div>
    );
  }

  if (!pipelineStatus) {
    return (
      <div className="mt-6 p-4 bg-card border border-border rounded-lg">
        <p className="text-muted-foreground text-sm">Waiting for pipeline to start...</p>
      </div>
    );
  }

  const overallStage = pipelineStatus.stage;
  const currentSection = pipelineStatus.section;
  const totalSections = pipelineStatus.total;

  const isComplete = course?.status === 'completed' || course?.status === 'completed_partial';
  const isFailed = course?.status === 'failed';

  const sortedSections = course
    ? [...course.sections].sort((a, b) => a.position - b.position)
    : [];

  // Build log entries from course data
  const logs: { timestamp: string; message: string }[] = [];

  // For each section that has content, add a "completed" log
  sortedSections.forEach(section => {
    if (section.content && section.content.trim().length > 0) {
      logs.push({ timestamp: '', message: `\u2713 Section ${section.position}: ${section.title}` });
    }
  });

  // Add current activity
  if (!isComplete && !isFailed && currentSection > 0) {
    const currentSectionObj = sortedSections.find(s => s.position === currentSection);
    const stageName = STAGE_LABELS[overallStage] || overallStage;
    logs.push({ timestamp: '', message: `\u25b8 ${stageName} section ${currentSection}: ${currentSectionObj?.title || ''}` });
  }

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
    </div>
  );
}
