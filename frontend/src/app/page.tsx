'use client';

import { useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '@clerk/nextjs';
import { createCourse } from '@/lib/api';

function HomePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { getToken } = useAuth();
  const initialTopic = searchParams.get('topic') || '';

  const [topic, setTopic] = useState(initialTopic);
  const [instructions, setInstructions] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!topic.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const token = await getToken();
      const course = await createCourse(topic.trim(), instructions.trim() || undefined, token);
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

      <form onSubmit={handleSubmit} className="w-full max-w-lg space-y-4">
        <input
          type="text"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="e.g. Kubernetes networking"
          className="w-full px-4 py-3 bg-gray-900 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-purple-500"
          disabled={loading}
        />
        <textarea
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="Optional: Keep it practical, assume I know Docker basics..."
          rows={3}
          className="w-full px-4 py-3 bg-gray-900 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 resize-none"
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
