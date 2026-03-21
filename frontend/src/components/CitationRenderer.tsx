'use client';

import React from 'react';
import ReactMarkdown from 'react-markdown';
import { Citation } from '@/lib/types';

function safeHref(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return url;
  } catch {}
  return '#';
}

interface CitationRendererProps {
  content: string;
  citations: Citation[];
}

function transformCitations(text: string): React.ReactNode[] {
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((part, i) => {
    const match = part.match(/^\[(\d+)\]$/);
    if (match) {
      const num = match[1];
      return (
        <a
          key={i}
          href={`#citation-${num}`}
          className="text-primary hover:text-primary/80 text-xs align-super no-underline font-semibold ml-0.5"
          title={`Source ${num}`}
          onClick={(e) => {
            e.preventDefault();
            const el = document.getElementById(`citation-${num}`);
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }}
        >
          [{num}]
        </a>
      );
    }
    return part;
  });
}

export default function CitationRenderer({ content, citations }: CitationRendererProps) {
  return (
    <div>
      <div className="prose prose-invert prose-purple max-w-none">
        <ReactMarkdown
          components={{
            p: ({ children }) => {
              const processed = React.Children.map(children, (child) => {
                if (typeof child === 'string') {
                  return <>{transformCitations(child)}</>;
                }
                return child;
              });
              return <p>{processed}</p>;
            },
            li: ({ children }) => {
              const processed = React.Children.map(children, (child) => {
                if (typeof child === 'string') {
                  return <>{transformCitations(child)}</>;
                }
                return child;
              });
              return <li>{processed}</li>;
            },
          }}
        >
          {content}
        </ReactMarkdown>
      </div>

      {citations.length > 0 && (
        <div className="mt-8 pt-6 border-t border-border">
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-4">Sources</h3>
          <ol className="space-y-3">
            {citations.map((citation) => (
              <li
                key={citation.number}
                id={`citation-${citation.number}`}
                className="flex gap-3 text-sm scroll-mt-4"
              >
                <span className="flex-shrink-0 text-primary font-semibold w-6 text-right">
                  [{citation.number}]
                </span>
                <div className="min-w-0">
                  <a
                    href={safeHref(citation.source_url)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:text-primary/80 underline break-all"
                  >
                    {citation.source_title}
                  </a>
                  <p className="text-muted-foreground mt-0.5 text-xs">{citation.claim}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
