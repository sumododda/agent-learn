'use client';

import { useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { createCourse } from '@/lib/api';

const STYLE_OPTIONS = [
  { id: 'practical', label: 'Hands-on & Practical', instruction: 'Focus on practical examples and real-world applications.' },
  { id: 'beginner', label: 'Beginner Friendly', instruction: 'Explain concepts from the ground up, assume no prior knowledge.' },
  { id: 'deep', label: 'Deep & Technical', instruction: 'Go deep into technical details, internals, and advanced concepts.' },
] as const;

const ENGLISH_LEVELS = [
  { id: 'simple', label: 'Simple English', instruction: 'Use simple, everyday English. Short sentences. Avoid jargon and complex vocabulary.' },
  { id: 'intermediate', label: 'Intermediate', instruction: 'Use clear, straightforward English. Define technical terms when first introduced.' },
  { id: 'advanced', label: 'Advanced / Native', instruction: 'Use natural, fluent English with full technical vocabulary. No need to simplify language.' },
] as const;

function HomePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { getToken } = useAuth();
  const initialTopic = searchParams.get('topic') || '';

  const [topic, setTopic] = useState(initialTopic);
  const [selectedStyles, setSelectedStyles] = useState<Set<string>>(new Set());
  const [englishLevel, setEnglishLevel] = useState<string | null>(null);
  const [extraInstructions, setExtraInstructions] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggleStyle(id: string) {
    setSelectedStyles(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  function buildInstructions(): string | undefined {
    const parts: string[] = [];
    for (const opt of STYLE_OPTIONS) {
      if (selectedStyles.has(opt.id)) {
        parts.push(opt.instruction);
      }
    }
    const level = ENGLISH_LEVELS.find(l => l.id === englishLevel);
    if (level) {
      parts.push(level.instruction);
    }
    if (extraInstructions.trim()) {
      parts.push(extraInstructions.trim());
    }
    return parts.length > 0 ? parts.join(' ') : undefined;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!topic.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const token = await getToken();
      const course = await createCourse(topic.trim(), buildInstructions(), token);
      router.push(`/courses/${course.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh]">
      <h1 className="text-3xl font-bold mb-2">What do you want to learn?</h1>
      <p className="text-gray-400 mb-8">Enter a topic and we&apos;ll generate a personalized course for you.</p>

      <form onSubmit={handleSubmit} className="w-full max-w-lg space-y-5">
        <input
          type="text"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="e.g. Kubernetes networking"
          className="w-full px-4 py-3 bg-gray-900 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-purple-500"
          disabled={loading}
        />

        {/* Style chips */}
        <div>
          <div className="text-gray-500 text-xs uppercase tracking-wider mb-2">Course style</div>
          <div className="flex flex-wrap gap-2">
            {STYLE_OPTIONS.map((opt) => {
              const active = selectedStyles.has(opt.id);
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => toggleStyle(opt.id)}
                  disabled={loading}
                  className={`px-3.5 py-1.5 rounded-full text-sm transition-colors border ${
                    active
                      ? 'bg-purple-600/20 border-purple-500 text-purple-300'
                      : 'bg-gray-900 border-gray-700 text-gray-400 hover:border-gray-500 hover:text-gray-300'
                  }`}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* English level */}
        <div>
          <div className="text-gray-500 text-xs uppercase tracking-wider mb-2">English level</div>
          <div className="flex flex-wrap gap-2">
            {ENGLISH_LEVELS.map((lvl) => {
              const active = englishLevel === lvl.id;
              return (
                <button
                  key={lvl.id}
                  type="button"
                  onClick={() => setEnglishLevel(active ? null : lvl.id)}
                  disabled={loading}
                  className={`px-3.5 py-1.5 rounded-full text-sm transition-colors border ${
                    active
                      ? 'bg-purple-600/20 border-purple-500 text-purple-300'
                      : 'bg-gray-900 border-gray-700 text-gray-400 hover:border-gray-500 hover:text-gray-300'
                  }`}
                >
                  {lvl.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Extra instructions */}
        <textarea
          value={extraInstructions}
          onChange={(e) => setExtraInstructions(e.target.value)}
          placeholder="Any other preferences? e.g. &quot;Assume I know Python&quot;, &quot;Include code examples&quot;..."
          rows={2}
          className="w-full px-4 py-3 bg-gray-900 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 resize-none text-sm"
          disabled={loading}
        />

        <button
          type="submit"
          disabled={loading || !topic.trim()}
          className="w-full py-3 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg font-medium transition-colors"
        >
          {loading ? 'Generating outline...' : 'Generate Course'}
        </button>
        {error && <p className="text-red-400 text-sm text-center">{error}</p>}
      </form>
    </div>
  );
}

export default function HomePage() {
  return (
    <Suspense fallback={<div className="text-center text-gray-400 mt-20">Loading...</div>}>
      <HomePageInner />
    </Suspense>
  );
}
