'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { getPipelineStatus } from '@/lib/api';
import { PipelineStatus } from '@/lib/types';

const POLL_INTERVAL_MS = 3000;

const STAGE_ORDER = ['researching', 'verifying', 'writing', 'editing', 'completed'];

interface PipelineProgressProps {
  courseId: string;
  sections: { position: number; title: string }[];
}

function StageIndicator({ stage }: { stage: string }) {
  if (stage === 'completed') {
    return (
      <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-green-600 text-white text-xs" title="Completed">
        &#10003;
      </span>
    );
  }

  const spinnerColor: Record<string, string> = {
    researching: 'border-blue-400',
    verifying: 'border-yellow-400',
    writing: 'border-purple-400',
    editing: 'border-cyan-400',
  };

  const color = spinnerColor[stage] || 'border-gray-400';

  return (
    <span
      className={`inline-block w-5 h-5 rounded-full border-2 border-t-transparent animate-spin ${color}`}
      title={stage}
    />
  );
}

function StageLabel({ stage }: { stage: string }) {
  const labels: Record<string, { text: string; color: string }> = {
    researching: { text: 'Researching', color: 'text-blue-400' },
    verifying: { text: 'Verifying', color: 'text-yellow-400' },
    writing: { text: 'Writing', color: 'text-purple-400' },
    editing: { text: 'Editing', color: 'text-cyan-400' },
    completed: { text: 'Completed', color: 'text-green-400' },
  };

  const label = labels[stage] || { text: stage, color: 'text-gray-400' };

  return <span className={`text-xs font-medium ${label.color}`}>{label.text}</span>;
}

export default function PipelineProgress({ courseId, sections }: PipelineProgressProps) {
  const router = useRouter();
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(true);

  const poll = useCallback(async () => {
    try {
      const data = await getPipelineStatus(courseId);
      setStatus(data);

      const allCompleted = sections.every(
        (s) => data.sections[s.position] === 'completed'
      );

      if (allCompleted) {
        setPolling(false);
        router.push(`/courses/${courseId}/learn`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load status');
      setPolling(false);
    }
  }, [courseId, sections, router]);

  useEffect(() => {
    if (!polling) return;

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [polling, poll]);

  const completedCount = status
    ? sections.filter((s) => status.sections[s.position] === 'completed').length
    : 0;
  const progressPercent = sections.length > 0 ? Math.round((completedCount / sections.length) * 100) : 0;

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-lg font-semibold text-white">Generating Lessons</h2>
          <span className="text-sm text-gray-400">
            {completedCount} / {sections.length} sections
          </span>
        </div>
        <div className="w-full h-2 bg-gray-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-purple-600 rounded-full transition-all duration-500"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}

      <div className="space-y-2">
        {sections.map((section) => {
          const sectionStage = status?.sections[section.position] || 'pending';
          const isActive = sectionStage !== 'pending' && sectionStage !== 'completed';
          const isCompleted = sectionStage === 'completed';

          return (
            <div
              key={section.position}
              className={`flex items-center gap-3 p-3 rounded-lg border transition-colors ${
                isActive
                  ? 'bg-gray-800 border-gray-600'
                  : isCompleted
                  ? 'bg-gray-900 border-green-800'
                  : 'bg-gray-900 border-gray-800'
              }`}
            >
              <div className="flex-shrink-0 w-6 text-center">
                {sectionStage === 'pending' ? (
                  <span className="inline-block w-5 h-5 rounded-full border-2 border-gray-700" />
                ) : (
                  <StageIndicator stage={sectionStage} />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <span className={`text-sm ${isCompleted ? 'text-green-300' : isActive ? 'text-white' : 'text-gray-500'}`}>
                  {section.position}. {section.title}
                </span>
              </div>
              <div className="flex-shrink-0">
                {sectionStage !== 'pending' && <StageLabel stage={sectionStage} />}
              </div>
            </div>
          );
        })}
      </div>

      {status && (
        <div className="text-center text-gray-500 text-xs">
          {polling
            ? 'Updating every 3 seconds...'
            : error
            ? 'Polling stopped due to error.'
            : 'All sections completed.'}
        </div>
      )}
    </div>
  );
}
