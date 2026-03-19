'use client';

import { useEffect, useState, useCallback } from 'react';
import { useParams } from 'next/navigation';
import { getCourse, getBlackboard } from '@/lib/api';
import { Course, Section, BlackboardState } from '@/lib/types';
import CitationRenderer from '@/components/CitationRenderer';
import EvidencePanel from '@/components/EvidencePanel';

const POLL_INTERVAL_MS = 5000;

export default function LearnPage() {
  const params = useParams();
  const courseId = params.id as string;

  const [course, setCourse] = useState<Course | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [blackboard, setBlackboard] = useState<BlackboardState | null>(null);
  const [showGlossary, setShowGlossary] = useState(false);

  const isGenerating = course
    ? ['generating', 'researching', 'verifying', 'writing', 'editing'].includes(course.status)
    : false;

  const loadCourse = useCallback(async () => {
    try {
      const data = await getCourse(courseId);
      setCourse(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load course');
      return null;
    }
  }, [courseId]);

  useEffect(() => {
    async function init() {
      await loadCourse();
      setLoading(false);
    }
    init();
  }, [loadCourse]);

  // Poll for progressive loading while course is generating
  useEffect(() => {
    if (!isGenerating) return;

    const interval = setInterval(async () => {
      const data = await loadCourse();
      if (data && !['generating', 'researching', 'verifying', 'writing', 'editing'].includes(data.status)) {
        clearInterval(interval);
      }
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [isGenerating, loadCourse]);

  // Load blackboard on demand
  async function handleShowGlossary() {
    if (!showGlossary && !blackboard) {
      try {
        const data = await getBlackboard(courseId);
        setBlackboard(data);
      } catch {
        // Blackboard may not exist yet
      }
    }
    setShowGlossary(!showGlossary);
  }

  if (loading) return <div className="text-center text-gray-400 mt-20">Loading lessons...</div>;
  if (error) return <div className="text-center text-red-400 mt-20">{error}</div>;
  if (!course) return <div className="text-center text-gray-400 mt-20">Course not found</div>;

  const sections = (course.sections || []).sort((a: Section, b: Section) => a.position - b.position);
  const sectionsWithContent = sections.filter((s) => s.content);
  const currentSection = sections[currentIndex];

  if (!currentSection) return <div className="text-center text-gray-400 mt-20">No sections available</div>;

  const hasContent = !!currentSection.content;
  const citations = currentSection.citations || [];

  return (
    <div className="flex gap-8">
      {/* Sidebar */}
      <nav className="w-56 flex-shrink-0">
        <div className="text-gray-500 text-xs uppercase tracking-wider mb-3">Sections</div>
        <div className="space-y-1">
          {sections.map((section: Section, index: number) => {
            const sectionHasContent = !!section.content;
            return (
              <button
                key={section.id}
                onClick={() => setCurrentIndex(index)}
                className={`block w-full text-left px-3 py-2 text-sm rounded transition-colors ${
                  index === currentIndex
                    ? 'text-purple-400 bg-gray-800 border-l-2 border-purple-400'
                    : sectionHasContent
                    ? 'text-gray-400 hover:text-gray-200 hover:bg-gray-900'
                    : 'text-gray-600 cursor-default'
                }`}
                disabled={!sectionHasContent && index !== currentIndex}
              >
                <span className="flex items-center gap-2">
                  {sectionHasContent && (
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-500 flex-shrink-0" />
                  )}
                  {!sectionHasContent && isGenerating && (
                    <span className="inline-block w-3 h-3 rounded-full border-2 border-gray-600 border-t-transparent animate-spin flex-shrink-0" />
                  )}
                  {section.position}. {section.title}
                </span>
              </button>
            );
          })}
        </div>

        {/* Course Knowledge toggle */}
        <div className="mt-6 pt-4 border-t border-gray-800">
          <button
            onClick={handleShowGlossary}
            className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            {showGlossary ? 'Hide' : 'Show'} Course Knowledge
          </button>
        </div>

        {/* Ungrounded badge */}
        {course.ungrounded && (
          <div className="mt-3">
            <span className="inline-block px-2 py-1 text-xs font-medium bg-yellow-900 text-yellow-300 border border-yellow-700 rounded">
              Ungrounded
            </span>
          </div>
        )}

        {/* Progressive loading indicator */}
        {isGenerating && (
          <div className="mt-3 text-xs text-gray-600">
            {sectionsWithContent.length} / {sections.length} sections ready
          </div>
        )}
      </nav>

      {/* Content */}
      <article className="flex-1 min-w-0">
        <h1 className="text-2xl font-bold mb-1">{currentSection.title}</h1>
        <p className="text-gray-500 text-sm mb-6">Section {currentSection.position} of {sections.length}</p>

        {hasContent ? (
          <>
            <CitationRenderer
              content={currentSection.content!}
              citations={citations}
            />
            <EvidencePanel
              courseId={courseId}
              sectionPosition={currentSection.position}
            />
          </>
        ) : isGenerating ? (
          <div className="text-center text-gray-500 mt-12">
            <div className="inline-block w-6 h-6 rounded-full border-2 border-gray-600 border-t-purple-400 animate-spin mb-3" />
            <p>This section is still being generated...</p>
          </div>
        ) : (
          <div className="prose prose-invert prose-purple max-w-none">
            <p className="text-gray-500">Content not yet generated.</p>
          </div>
        )}

        {/* Glossary panel */}
        {showGlossary && blackboard && Object.keys(blackboard.glossary).length > 0 && (
          <div className="mt-8 p-4 bg-gray-900 border border-gray-800 rounded-lg">
            <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Glossary</h3>
            <dl className="space-y-2">
              {Object.entries(blackboard.glossary).map(([term, info]) => (
                <div key={term}>
                  <dt className="text-sm text-purple-400 font-medium">{term}</dt>
                  <dd className="text-xs text-gray-400 ml-3">
                    {info.definition}
                    <span className="text-gray-600 ml-2">(Section {info.defined_in_section})</span>
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}

        {showGlossary && blackboard && Object.keys(blackboard.glossary).length === 0 && (
          <div className="mt-8 p-4 bg-gray-900 border border-gray-800 rounded-lg text-gray-600 text-sm">
            No glossary terms available yet.
          </div>
        )}

        {showGlossary && !blackboard && (
          <div className="mt-8 p-4 bg-gray-900 border border-gray-800 rounded-lg text-gray-600 text-sm">
            Course knowledge not available for this course.
          </div>
        )}

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
