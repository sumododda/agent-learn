'use client';

import { useEffect, useRef, useState } from 'react';
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

function stageBadgeColor(stage: string): string {
  switch (stage) {
    case 'completed':
      return 'text-green-400';
    case 'failed':
      return 'text-red-400';
    case 'writing':
    case 'editing':
      return 'text-purple-400';
    case 'researching':
    case 'verifying':
      return 'text-blue-400';
    default:
      return 'text-gray-400';
  }
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
      } catch (err) {
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
      <div className="mt-6 p-4 bg-gray-900 border border-red-800 rounded-lg">
        <p className="text-red-400 text-sm">Pipeline error: {error}</p>
      </div>
    );
  }

  if (!pipelineStatus) {
    return (
      <div className="mt-6 p-4 bg-gray-900 border border-gray-700 rounded-lg">
        <p className="text-gray-400 text-sm">Waiting for pipeline to start...</p>
      </div>
    );
  }

  const overallStage = pipelineStatus.stage;
  const currentSection = pipelineStatus.section;
  const totalSections = pipelineStatus.total;

  const isComplete = course?.status === 'completed' || course?.status === 'completed_partial';
  const isFailed = course?.status === 'failed';

  // Estimate completed sections from current section and stage
  const completedSections = overallStage === 'completed'
    ? totalSections
    : Math.max(0, currentSection - 1);

  const sortedSections = course
    ? [...course.sections].sort((a, b) => a.position - b.position)
    : [];

  return (
    <div className="mt-6 p-4 bg-gray-900 border border-gray-700 rounded-lg">
      {/* Overall status */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <span className="text-gray-500 text-xs uppercase tracking-wider">
            Pipeline
          </span>
          <span
            className={`ml-2 text-sm font-medium ${
              isFailed ? 'text-red-400' : isComplete ? 'text-green-400' : 'text-purple-400'
            }`}
          >
            {isFailed
              ? 'Failed'
              : isComplete
              ? 'Complete'
              : STAGE_LABELS[overallStage] || overallStage}
          </span>
        </div>
        {totalSections > 0 && (
          <span className="text-gray-500 text-sm">
            {completedSections} / {totalSections} sections
          </span>
        )}
      </div>

      {/* Progress bar */}
      {totalSections > 0 && (
        <div className="w-full bg-gray-800 rounded-full h-2 mb-4">
          <div
            className={`h-2 rounded-full transition-all duration-500 ${
              isFailed ? 'bg-red-500' : isComplete ? 'bg-green-500' : 'bg-purple-500'
            }`}
            style={{
              width: `${(completedSections / totalSections) * 100}%`,
            }}
          />
        </div>
      )}

      {/* Per-section status */}
      {sortedSections.length > 0 && (
        <div className="space-y-1">
          {sortedSections.map((section) => {
            const pos = section.position;
            const hasContent = section.content && section.content.trim().length > 0;
            const isActive = currentSection === pos;

            return (
              <div
                key={section.id}
                className={`flex items-center justify-between px-3 py-1.5 rounded text-sm ${
                  isActive ? 'bg-gray-800' : ''
                }`}
              >
                <span
                  className={
                    hasContent
                      ? 'text-green-400'
                      : isActive
                      ? 'text-white'
                      : 'text-gray-500'
                  }
                >
                  {pos}. {section.title}
                </span>
                {hasContent && (
                  <span className="text-xs text-green-400">Done</span>
                )}
                {isActive && !hasContent && (
                  <span className={`text-xs ${stageBadgeColor(overallStage)}`}>
                    {STAGE_LABELS[overallStage] || overallStage}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
