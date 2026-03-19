'use client';

import { useState } from 'react';
import { getEvidence } from '@/lib/api';
import { EvidenceCard } from '@/lib/types';

interface EvidencePanelProps {
  courseId: string;
  sectionPosition: number;
}

function TierBadge({ tier }: { tier: 1 | 2 | 3 }) {
  const config: Record<number, { label: string; color: string }> = {
    1: { label: 'Official', color: 'bg-green-900 text-green-300 border-green-700' },
    2: { label: 'Blog', color: 'bg-blue-900 text-blue-300 border-blue-700' },
    3: { label: 'Forum', color: 'bg-yellow-900 text-yellow-300 border-yellow-700' },
  };
  const { label, color } = config[tier] || config[3];
  return (
    <span className={`inline-block px-2 py-0.5 text-xs font-medium rounded border ${color}`}>
      {label}
    </span>
  );
}

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const percent = Math.round(confidence * 100);
  let color = 'text-red-400';
  if (percent >= 80) color = 'text-green-400';
  else if (percent >= 50) color = 'text-yellow-400';

  return (
    <span className={`text-xs font-medium ${color}`}>
      {percent}%
    </span>
  );
}

export default function EvidencePanel({ courseId, sectionPosition }: EvidencePanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [cards, setCards] = useState<EvidenceCard[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetched, setFetched] = useState(false);

  async function handleToggle() {
    if (!expanded && !fetched) {
      setLoading(true);
      setError(null);
      try {
        const data = await getEvidence(courseId, sectionPosition);
        setCards(data);
        setFetched(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load evidence');
      } finally {
        setLoading(false);
      }
    }
    setExpanded(!expanded);
  }

  return (
    <div className="mt-4 border border-gray-800 rounded-lg overflow-hidden">
      <button
        onClick={handleToggle}
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-900 hover:bg-gray-800 transition-colors text-sm"
      >
        <span className="text-gray-400 font-medium">
          Evidence Cards
          {fetched && <span className="text-gray-600 ml-2">({cards.length} cards)</span>}
        </span>
        <span className="text-gray-500">
          {expanded ? '\u25B2' : '\u25BC'}
        </span>
      </button>

      {expanded && (
        <div className="p-4 bg-gray-950 space-y-4">
          {loading && (
            <p className="text-gray-500 text-sm text-center">Loading evidence...</p>
          )}

          {error && (
            <p className="text-red-400 text-sm text-center">{error}</p>
          )}

          {fetched && cards.length === 0 && !loading && (
            <p className="text-gray-600 text-sm text-center">No evidence cards for this section.</p>
          )}

          {cards.map((card) => (
            <div
              key={card.id}
              className={`p-4 rounded-lg border ${
                card.verified
                  ? 'bg-gray-900 border-gray-700'
                  : 'bg-gray-900 border-red-900'
              }`}
            >
              <div className="flex items-start justify-between gap-3 mb-2">
                <p className="text-sm text-white font-medium flex-1">{card.claim}</p>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <TierBadge tier={card.source_tier} />
                  <ConfidenceBadge confidence={card.confidence} />
                </div>
              </div>

              <a
                href={card.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-purple-400 hover:text-purple-300 text-xs underline break-all"
              >
                {card.source_title}
              </a>

              <blockquote className="mt-2 pl-3 border-l-2 border-gray-700 text-gray-400 text-xs italic">
                {card.passage}
              </blockquote>

              {card.explanation && (
                <p className="mt-2 text-gray-500 text-xs">{card.explanation}</p>
              )}

              <div className="mt-2 flex items-center gap-3">
                <span className={`text-xs font-medium ${card.verified ? 'text-green-400' : 'text-red-400'}`}>
                  {card.verified ? 'Verified' : 'Rejected'}
                </span>
                {card.verification_note && (
                  <span className="text-xs text-gray-500">{card.verification_note}</span>
                )}
                {card.caveat && (
                  <span className="text-xs text-yellow-500">Caveat: {card.caveat}</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
