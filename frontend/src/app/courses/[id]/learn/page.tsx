'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import MermaidBlock from '@/components/MermaidBlock';
import ChatDrawer from '@/components/ChatDrawer';
import { useAuth } from '@/context/AuthContext';
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

        try {
          const progress = await getProgress(courseId, token);
          if (progress) {
            const sections = [...(data.sections || [])].sort(
              (a: Section, b: Section) => a.position - b.position
            );
            const resumeIndex = sections.findIndex(
              (s: Section) => s.position === progress.current_section
            );
            if (resumeIndex >= 0) {
              setCurrentIndex(resumeIndex);
            }
            setCompletedSections(progress.completed_sections || []);
          }
        } catch {
          // Progress fetch failed — start from section 0
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
        // Progress tracking is best-effort
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

  const sections = [...course.sections].sort((a: Section, b: Section) => a.position - b.position);
  const currentSection = sections[currentIndex];

  if (!currentSection) return <div className="text-center text-gray-400 mt-20">No sections available</div>;

  return (
    <div className="flex">
      {/* Sidebar nav — pinned left */}
      <nav className="fixed left-0 top-[73px] bottom-0 w-64 border-r border-gray-800/50 overflow-y-auto px-4 py-6">
        <div className="text-gray-600 text-[10px] uppercase tracking-widest font-semibold mb-4 px-2">Sections</div>
        <div className="flex flex-col gap-1">
          {sections.map((section: Section, index: number) => {
            const isActive = index === currentIndex;
            const isCompleted = completedSections.includes(section.position);
            return (
              <button
                key={section.id}
                onClick={() => handleSectionClick(index, section.position)}
                className={`flex items-center gap-3 px-2 py-2.5 rounded-lg text-left transition-colors ${
                  isActive
                    ? 'bg-purple-600/10 text-purple-300'
                    : 'text-gray-500'
                }`}
              >
                <span className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold flex-shrink-0 ${
                  isActive
                    ? 'bg-purple-600 text-white'
                    : isCompleted
                    ? 'bg-green-900/40 text-green-400 border border-green-700/50'
                    : 'bg-gray-800/60 text-gray-500 border border-gray-700/50'
                }`}>
                  {isCompleted && !isActive ? (
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    section.position
                  )}
                </span>
                <span className={`text-sm leading-snug ${
                  isActive ? 'text-purple-200 font-medium' : 'text-gray-400'
                }`}>
                  {section.title}
                </span>
              </button>
            );
          })}
        </div>
      </nav>

      {/* Content */}
      <article className="flex-1 min-w-0 max-w-[820px] mx-auto ml-72">
        <div className="mb-8">
          <p className="text-purple-400 text-sm font-medium tracking-wide mb-2">
            Section {currentSection.position} of {sections.length}
          </p>
          <h1 className="text-2xl font-bold text-white leading-tight">
            {currentSection.title}
          </h1>
        </div>

        <div className="learn-content">
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
            {(currentSection.content || 'Content not yet generated.').replace(/^##?\s+.+\n+/, '')}
          </ReactMarkdown>
        </div>

        {/* Prev/Next navigation */}
        <div className="flex justify-between mt-12 pt-6 border-t border-gray-800/60">
          <button
            onClick={() => handlePrev(currentIndex - 1, sections[currentIndex - 1].position)}
            disabled={currentIndex === 0}
            className="text-sm text-gray-500 hover:text-gray-300 disabled:text-gray-800 disabled:cursor-default transition-colors"
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
            className="text-sm text-purple-400 hover:text-purple-300 disabled:text-gray-800 disabled:cursor-default transition-colors"
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
