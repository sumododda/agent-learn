'use client';

import { useEffect, useRef } from 'react';
import { useRealtimeRun } from '@trigger.dev/react-hooks';
import { PipelineMetadata } from '@/lib/types';

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
  runId: string;
  accessToken: string;
  sectionTitles: Record<number, string>;
  onComplete?: () => void;
}

export default function PipelineProgress({
  runId,
  accessToken,
  sectionTitles,
  onComplete,
}: PipelineProgressProps) {
  const { run, error } = useRealtimeRun(runId, {
    accessToken,
    enabled: !!runId,
  });

  const pipeline = run?.metadata?.pipeline as PipelineMetadata | undefined;
  const runStatus = run?.status;

  // Notify parent when the run completes (via effect to avoid calling during render)
  const completeCalled = useRef(false);
  useEffect(() => {
    if (runStatus === 'COMPLETED' && onComplete && !completeCalled.current) {
      completeCalled.current = true;
      onComplete();
    }
  }, [runStatus, onComplete]);

  if (!run && !error) {
    return (
      <div className="mt-6 p-4 bg-gray-900 border border-gray-700 rounded-lg">
        <p className="text-gray-400 text-sm">Connecting to pipeline...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mt-6 p-4 bg-gray-900 border border-red-800 rounded-lg">
        <p className="text-red-400 text-sm">
          Failed to connect to pipeline: {error.message}
        </p>
      </div>
    );
  }

  if (!pipeline) {
    return (
      <div className="mt-6 p-4 bg-gray-900 border border-gray-700 rounded-lg">
        <p className="text-gray-400 text-sm">Waiting for pipeline to start...</p>
      </div>
    );
  }

  const overallStage = pipeline.stage;
  const currentSection = pipeline.current_section;
  const sections = pipeline.sections || {};

  const totalSections = Object.keys(sectionTitles).length;
  const completedSections = Object.values(sections).filter(
    (s) => s === 'completed'
  ).length;

  const isRunFailed = runStatus === 'FAILED';
  const isRunComplete = runStatus === 'COMPLETED';

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
              isRunFailed ? 'text-red-400' : isRunComplete ? 'text-green-400' : 'text-purple-400'
            }`}
          >
            {isRunFailed
              ? 'Failed'
              : isRunComplete
              ? 'Complete'
              : STAGE_LABELS[overallStage] || overallStage}
          </span>
        </div>
        <span className="text-gray-500 text-sm">
          {completedSections} / {totalSections} sections
        </span>
      </div>

      {/* Progress bar */}
      <div className="w-full bg-gray-800 rounded-full h-2 mb-4">
        <div
          className={`h-2 rounded-full transition-all duration-500 ${
            isRunFailed ? 'bg-red-500' : isRunComplete ? 'bg-green-500' : 'bg-purple-500'
          }`}
          style={{
            width: `${totalSections > 0 ? (completedSections / totalSections) * 100 : 0}%`,
          }}
        />
      </div>

      {/* Per-section status */}
      <div className="space-y-1">
        {Object.entries(sectionTitles)
          .sort(([a], [b]) => Number(a) - Number(b))
          .map(([posStr, title]) => {
            const pos = Number(posStr);
            const sectionStage = sections[pos] || 'pending';
            const isActive = currentSection === pos;

            return (
              <div
                key={pos}
                className={`flex items-center justify-between px-3 py-1.5 rounded text-sm ${
                  isActive ? 'bg-gray-800' : ''
                }`}
              >
                <span
                  className={
                    sectionStage === 'completed'
                      ? 'text-green-400'
                      : isActive
                      ? 'text-white'
                      : 'text-gray-500'
                  }
                >
                  {pos}. {title}
                </span>
                <span className={`text-xs ${stageBadgeColor(sectionStage)}`}>
                  {sectionStage === 'pending'
                    ? ''
                    : STAGE_LABELS[sectionStage] || sectionStage}
                </span>
              </div>
            );
          })}
      </div>
    </div>
  );
}
