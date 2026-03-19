'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import MermaidBlock from '@/components/MermaidBlock';
import ChatDrawer from '@/components/ChatDrawer';
import { useAuth } from '@clerk/nextjs';
import { getCourse, getProgress, updateProgress } from '@/lib/api';
import { Course, Section } from '@/lib/types';

export default function LearnPage() {
  const params = useParams();
  const { getToken } = useAuth();
  const courseId = params.id as string;

  const [course, setCourse] = useState<Course | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [completedSections, setCompletedSections] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const initialLoadDone = useRef(false);

  useEffect(() => {
    async function loadCourse() {
      try {
        const token = await getToken();
        const data = await getCourse(courseId, token);
        setCourse(data);

        // Fetch current progress to resume from last position
        try {
          const progress = await getProgress(courseId, token);
          if (progress) {
            const sections = (data.sections || []).sort(
              (a: Section, b: Section) => a.position - b.position
            );
            // Find the index that matches the saved current_section position
            const resumeIndex = sections.findIndex(
              (s: Section) => s.position === progress.current_section
            );
            if (resumeIndex >= 0) {
              setCurrentIndex(resumeIndex);
            }
            setCompletedSections(progress.completed_sections || []);
          }
        } catch {
          // Progress fetch failed — start from section 0, not critical
        }
        initialLoadDone.current = true;
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load course');
      } finally {
        setLoading(false);
      }
    }
    loadCourse();
  }, [courseId, getToken]);

  const trackProgress = useCallback(
    async (data: { current_section?: number; completed_section?: number }) => {
      try {
        const token = await getToken();
        const result = await updateProgress(courseId, data, token);
        setCompletedSections(result.completed_sections || []);
      } catch {
        // Progress tracking is best-effort — don't block navigation
      }
    },
    [courseId, getToken]
  );

  const handleSectionClick = useCallback(
    (index: number, sectionPosition: number) => {
      setCurrentIndex(index);
      if (initialLoadDone.current) {
        trackProgress({ current_section: sectionPosition });
      }
    },
    [trackProgress]
  );

  const handleNext = useCallback(
    (currentPosition: number, nextIndex: number, nextPosition: number) => {
      setCurrentIndex(nextIndex);
      trackProgress({
        current_section: nextPosition,
        completed_section: currentPosition,
      });
    },
    [trackProgress]
  );

  const handlePrev = useCallback(
    (prevIndex: number, prevPosition: number) => {
      setCurrentIndex(prevIndex);
      trackProgress({ current_section: prevPosition });
    },
    [trackProgress]
  );

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
          {sections.map((section: Section, index: number) => {
            const isCompleted = completedSections.includes(section.position);
            return (
              <button
                key={section.id}
                onClick={() => handleSectionClick(index, section.position)}
                className={`block w-full text-left px-3 py-2 text-sm rounded transition-colors ${
                  index === currentIndex
                    ? 'text-purple-400 bg-gray-800 border-l-2 border-purple-400'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-900'
                }`}
              >
                <span className="flex items-center gap-2">
                  {isCompleted && (
                    <svg className="w-3.5 h-3.5 text-green-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                  <span>{section.position}. {section.title}</span>
                </span>
              </button>
            );
          })}
        </div>
      </nav>

      {/* Content */}
      <article className="flex-1 min-w-0">
        <h1 className="text-2xl font-bold mb-1">{currentSection.title}</h1>
        <p className="text-gray-500 text-sm mb-6">Section {currentSection.position} of {sections.length}</p>

        <div className="prose prose-invert prose-purple max-w-none">
          <ReactMarkdown
            components={{
              code({ className, children }) {
                if (/language-mermaid/.test(className || '')) {
                  return <MermaidBlock definition={String(children).replace(/\n$/, '')} />;
                }
                return <code className={className}>{children}</code>;
              },
            }}
          >
            {currentSection.content || 'Content not yet generated.'}
          </ReactMarkdown>
        </div>

        {/* Prev/Next navigation */}
        <div className="flex justify-between mt-8 pt-4 border-t border-gray-800">
          <button
            onClick={() => handlePrev(currentIndex - 1, sections[currentIndex - 1].position)}
            disabled={currentIndex === 0}
            className="text-sm text-gray-400 hover:text-white disabled:text-gray-700 transition-colors"
          >
            &larr; {currentIndex > 0 ? sections[currentIndex - 1].title : 'Previous'}
          </button>
          <button
            onClick={() =>
              handleNext(
                currentSection.position,
                currentIndex + 1,
                sections[currentIndex + 1].position
              )
            }
            disabled={currentIndex === sections.length - 1}
            className="text-sm text-purple-400 hover:text-purple-300 disabled:text-gray-700 transition-colors"
          >
            {currentIndex < sections.length - 1 ? sections[currentIndex + 1].title : 'Next'} &rarr;
          </button>
        </div>
      </article>

      <ChatDrawer
        courseId={courseId}
        currentSectionPosition={sections[currentIndex].position}
        currentSectionTitle={sections[currentIndex].title}
      />
    </div>
  );
}
