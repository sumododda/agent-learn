'use client';

import { useEffect, useState, useRef } from 'react';
import { getEvidence } from '@/lib/api';
import { EvidenceCard } from '@/lib/types';

function safeHref(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return url;
  } catch {}
  return '#';
}

interface EvidencePanelProps {
  courseId: string;
  sectionPosition: number;
}

function TierBadge({ tier }: { tier: 1 | 2 | 3 }) {
  const config: Record<number, { label: string; color: string }> = {
    1: { label: 'Official', color: 'text-green-600 dark:text-green-400' },
    2: { label: 'Blog', color: 'text-blue-600 dark:text-blue-400' },
    3: { label: 'Forum', color: 'text-yellow-600 dark:text-yellow-400' },
  };
  const { label, color } = config[tier] || config[3];
  return (
    <span className={`text-xs bg-muted px-1.5 py-0.5 rounded ${color}`}>
      {label}
    </span>
  );
}

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const percent = Math.round(confidence * 100);
  let color = 'text-destructive';
  if (percent >= 80) color = 'text-green-600 dark:text-green-400';
  else if (percent >= 50) color = 'text-yellow-600 dark:text-yellow-400';

  return (
    <span className={`text-xs font-medium ${color}`}>
      {percent}%
    </span>
  );
}

export default function EvidencePanel({ courseId, sectionPosition }: EvidencePanelProps) {
  const [cards, setCards] = useState<EvidenceCard[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastFetchedRef = useRef<string>('');

  useEffect(() => {
    const key = `${courseId}:${sectionPosition}`;
    if (lastFetchedRef.current === key) return;
    lastFetchedRef.current = key;

    async function fetchEvidence() {
      setLoading(true);
      setError(null);
      try {
        const data = await getEvidence(courseId, sectionPosition);
        setCards(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load evidence');
      } finally {
        setLoading(false);
      }
    }
    fetchEvidence();
  }, [courseId, sectionPosition]);

  return (
    <div className="h-full">
      {loading && (
        <p className="text-muted-foreground text-sm text-center py-6">Loading sources...</p>
      )}

      {error && (
        <p className="text-destructive text-sm text-center py-6">{error}</p>
      )}

      {!loading && !error && cards.length === 0 && (
        <p className="text-muted-foreground text-sm text-center py-6">No sources for this section.</p>
      )}

      {cards.map((card) => (
        <div
          key={card.id}
          className="border-b border-border p-3"
        >
          <div className="flex items-start justify-between gap-2 mb-1.5">
            <p className="text-sm font-medium text-foreground flex-1 leading-snug">{card.claim}</p>
            <div className="flex items-center gap-1.5 shrink-0">
              <TierBadge tier={card.source_tier} />
              <ConfidenceBadge confidence={card.confidence} />
            </div>
          </div>

          <a
            href={safeHref(card.source_url)}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-muted-foreground truncate block hover:text-foreground transition-colors"
          >
            {card.source_title}
          </a>

          <blockquote className="mt-1.5 pl-2.5 border-l-2 border-border text-muted-foreground text-xs italic">
            {card.passage}
          </blockquote>

          {card.explanation && (
            <p className="mt-1.5 text-muted-foreground text-xs">{card.explanation}</p>
          )}

          <div className="mt-1.5 flex items-center gap-2">
            <span className={`text-xs font-medium ${card.verified ? 'text-green-600 dark:text-green-400' : 'text-destructive'}`}>
              {card.verified ? 'Verified' : 'Rejected'}
            </span>
            {card.verification_note && (
              <span className="text-xs text-muted-foreground">{card.verification_note}</span>
            )}
            {card.caveat && (
              <span className="text-xs text-yellow-600 dark:text-yellow-400">Caveat: {card.caveat}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
