'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import { getCourse } from '@/lib/api';
import { Course, Section } from '@/lib/types';

export default function LearnPage() {
  const params = useParams();
  const courseId = params.id as string;

  const [course, setCourse] = useState<Course | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
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

  if (loading) return <div className="text-center text-gray-400 mt-20">Loading lessons...</div>;
  if (error) return <div className="text-center text-red-400 mt-20">{error}</div>;
  if (!course) return <div className="text-center text-gray-400 mt-20">Course not found</div>;

  const sections = course.sections.sort((a: Section, b: Section) => a.position - b.position);
  const currentSection = sections[currentIndex];

  if (!currentSection) return <div className="text-center text-gray-400 mt-20">No sections available</div>;

  return (
    <div className="flex gap-8">
      {/* Sidebar */}
      <nav className="w-56 flex-shrink-0">
        <div className="text-gray-500 text-xs uppercase tracking-wider mb-3">Sections</div>
        <div className="space-y-1">
          {sections.map((section: Section, index: number) => (
            <button
              key={section.id}
              onClick={() => setCurrentIndex(index)}
              className={`block w-full text-left px-3 py-2 text-sm rounded transition-colors ${
                index === currentIndex
                  ? 'text-purple-400 bg-gray-800 border-l-2 border-purple-400'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-900'
              }`}
            >
              {section.position}. {section.title}
            </button>
          ))}
        </div>
      </nav>

      {/* Content */}
      <article className="flex-1 min-w-0">
        <h1 className="text-2xl font-bold mb-1">{currentSection.title}</h1>
        <p className="text-gray-500 text-sm mb-6">Section {currentSection.position} of {sections.length}</p>

        <div className="prose prose-invert prose-purple max-w-none">
          <ReactMarkdown>{currentSection.content || 'Content not yet generated.'}</ReactMarkdown>
        </div>

        {/* Prev/Next navigation */}
        <div className="flex justify-between mt-8 pt-4 border-t border-gray-800">
          <button
            onClick={() => setCurrentIndex(currentIndex - 1)}
            disabled={currentIndex === 0}
            className="text-sm text-gray-400 hover:text-white disabled:text-gray-700 transition-colors"
          >
            &larr; {currentIndex > 0 ? sections[currentIndex - 1].title : 'Previous'}
          </button>
          <button
            onClick={() => setCurrentIndex(currentIndex + 1)}
            disabled={currentIndex === sections.length - 1}
            className="text-sm text-purple-400 hover:text-purple-300 disabled:text-gray-700 transition-colors"
          >
            {currentIndex < sections.length - 1 ? sections[currentIndex + 1].title : 'Next'} &rarr;
          </button>
        </div>
      </article>
    </div>
  );
}
