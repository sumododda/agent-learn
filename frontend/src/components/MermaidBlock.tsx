'use client';

import { useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';

// Initialize once at module level
mermaid.initialize({
  startOnLoad: false,
  theme: 'dark',
});

let diagramCounter = 0;

export default function MermaidBlock({ definition }: { definition: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [svg, setSvg] = useState<string | null>(null);

  useEffect(() => {
    const id = `mermaid-diagram-${diagramCounter++}`;
    let cancelled = false;

    mermaid
      .render(id, definition)
      .then(({ svg: renderedSvg }) => {
        if (!cancelled) {
          setSvg(renderedSvg);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || 'Failed to render diagram');
        }
      });

    return () => {
      cancelled = true;
    };
  }, [definition]);

  if (error) {
    return (
      <pre className="text-red-400 text-sm bg-gray-900 p-3 rounded-lg overflow-x-auto">
        {`Diagram error: ${error}\n\n${definition}`}
      </pre>
    );
  }

  if (!svg) {
    return (
      <div className="flex items-center justify-center p-4 text-gray-500 text-sm">
        Rendering diagram...
      </div>
    );
  }

  // SVG is generated locally by the mermaid library from diagram definitions,
  // not from user-supplied HTML — safe to render directly.
  return (
    <div
      ref={containerRef}
      className="my-4 flex justify-center"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
