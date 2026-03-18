'use client';

import { useEffect, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { getCourse, generateCourse } from '@/lib/api';
import { Course } from '@/lib/types';

export default function OutlineReviewPage() {
  const params = useParams();
  const router = useRouter();
  const courseId = params.id as string;

  const [course, setCourse] = useState<Course | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadCourse() {
      try {
        const data = await getCourse(courseId);
        setCourse(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load course');
      } finally {
        setLoading(false);
      }
    }
    loadCourse();
  }, [courseId]);

  async function handleApprove() {
    setGenerating(true);
    setError(null);
    try {
      await generateCourse(courseId);
      router.push(`/courses/${courseId}/learn`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate');
      setGenerating(false);
    }
  }

  function handleRegenerate() {
    const topic = course?.topic || '';
    router.push(`/?topic=${encodeURIComponent(topic)}`);
  }

  if (loading) return <div className="text-center text-gray-400 mt-20">Loading course...</div>;
  if (error && !course) return <div className="text-center text-red-400 mt-20">{error}</div>;
  if (!course) return <div className="text-center text-gray-400 mt-20">Course not found</div>;

  // If already completed, redirect to learn
  if (course.status === 'completed') {
    router.push(`/courses/${courseId}/learn`);
    return null;
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-1">{course.topic}</h1>
      <p className="text-gray-400 mb-6">{course.sections.length} sections · Review your course outline</p>

      <div className="space-y-3 mb-8">
        {course.sections
          .sort((a, b) => a.position - b.position)
          .map((section) => (
            <div key={section.id} className="border-l-2 border-gray-700 pl-4 py-2">
              <div className="text-white font-medium">{section.position}. {section.title}</div>
              <div className="text-gray-400 text-sm">{section.summary}</div>
            </div>
          ))}
      </div>

      {error && <p className="text-red-400 text-sm mb-4">{error}</p>}

      <div className="flex gap-3">
        <button
          onClick={handleApprove}
          disabled={generating}
          className="flex-1 py-3 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg font-medium transition-colors"
        >
          {generating ? 'Generating lessons...' : 'Approve & Generate'}
        </button>
        <button
          onClick={handleRegenerate}
          disabled={generating}
          className="px-6 py-3 bg-gray-800 hover:bg-gray-700 border border-gray-600 rounded-lg text-gray-300 transition-colors"
        >
          Regenerate
        </button>
      </div>
    </div>
  );
}
