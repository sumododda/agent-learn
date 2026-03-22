'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import { Check } from 'lucide-react';
import MermaidBlock from '@/components/MermaidBlock';
import CitationRenderer from '@/components/CitationRenderer';
import EvidencePanel from '@/components/EvidencePanel';
import { ChatPanel } from '@/components/ChatDrawer';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
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

  // Scroll progress tracking
  const contentRef = useRef<HTMLDivElement>(null);
  const [scrollProgress, setScrollProgress] = useState(0);

  useEffect(() => {
    const el = contentRef.current;
    if (!el) return;
    function handleScroll() {
      const { scrollTop, scrollHeight, clientHeight } = el!;
      const progress = scrollHeight > clientHeight ? (scrollTop / (scrollHeight - clientHeight)) * 100 : 0;
      setScrollProgress(progress);
    }
    el.addEventListener('scroll', handleScroll);
    return () => el.removeEventListener('scroll', handleScroll);
  }, [currentIndex]);

  useEffect(() => {
    async function loadCourse() {
      try {
        const token = await getToken();
        const [data, progress] = await Promise.all([
          getCourse(courseId, token),
          getProgress(courseId, token).catch(() => null),
        ]);
        setCourse(data);

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
      setScrollProgress(0);
      if (initialLoadDone.current) {
        trackProgress({ current_section: sectionPosition });
      }
    },
    [trackProgress]
  );

  const handleNext = useCallback(
    (currentPosition: number, nextIndex: number, nextPosition: number) => {
      setCurrentIndex(nextIndex);
      setScrollProgress(0);
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
      setScrollProgress(0);
      trackProgress({ current_section: prevPosition });
    },
    [trackProgress]
  );

  if (loading) return <div className="text-center text-muted-foreground mt-20">Loading lessons...</div>;
  if (error) return <div className="text-center text-destructive mt-20">{error}</div>;
  if (!course) return <div className="text-center text-muted-foreground mt-20">Course not found</div>;

  const sections = [...course.sections].sort((a: Section, b: Section) => a.position - b.position);
  const currentSection = sections[currentIndex];

  if (!currentSection) return <div className="text-center text-muted-foreground mt-20">No sections available</div>;

  const markdownComponents = {
    code({ className, children }: { className?: string; children?: React.ReactNode }) {
      if (/language-mermaid/.test(className || '')) {
        return <MermaidBlock definition={String(children).replace(/\n$/, '')} />;
      }
      return <code className={className}>{children}</code>;
    },
  };

  return (
    <div className="h-screen flex flex-col">
      {/* Top bar: breadcrumb + progress */}
      <div className="h-10 border-b border-border flex items-center px-4 shrink-0">
        <span className="text-xs text-muted-foreground">
          <Link href="/library" className="hover:text-foreground transition-colors">Library</Link>
          {' / '}
          <span className="text-foreground">{course.topic}</span>
          {' / '}
          <span>{currentSection.title}</span>
        </span>
      </div>

      {/* Reading progress bar */}
      <div className="h-0.5 bg-muted shrink-0">
        <div className="h-full bg-primary transition-all" style={{ width: `${scrollProgress}%` }} />
      </div>

      {/* 3-panel body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Section nav */}
        <aside className="w-60 border-r border-border bg-card overflow-y-auto shrink-0">
          <div className="p-4">
            <div className="text-xs text-muted-foreground uppercase tracking-wider font-semibold mb-3">Sections</div>
            {sections.map((section, index) => {
              const isActive = index === currentIndex;
              const isCompleted = completedSections.includes(section.position);
              return (
                <button
                  key={section.id}
                  onClick={() => handleSectionClick(index, section.position)}
                  className={`w-full text-left flex items-center gap-2 px-2 py-2 rounded-md text-sm transition-colors mb-0.5 ${
                    isActive
                      ? 'bg-muted border-l-2 border-primary text-foreground font-medium'
                      : isCompleted
                      ? 'text-muted-foreground'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {isCompleted && !isActive && <Check className="h-3 w-3 text-green-500 shrink-0" />}
                  <span className="truncate">{section.title}</span>
                </button>
              );
            })}
          </div>
        </aside>

        {/* Center: Content */}
        <main className="flex-1 overflow-y-auto" ref={contentRef}>
          <div className="max-w-[680px] mx-auto px-10 py-12">
            <div className="mb-6">
              <p className="text-xs text-muted-foreground mb-1">
                Section {currentSection.position} of {sections.length}
              </p>
              <h1 className="text-2xl font-semibold text-foreground">
                {currentSection.title}
              </h1>
            </div>

            <div className="learn-content">
              <CitationRenderer
                content={(currentSection.content || 'Content not yet generated.').replace(/^##?\s+.+\n+/, '')}
                citations={currentSection.citations || []}
                markdownComponents={markdownComponents}
              />
            </div>

            {/* Prev/Next navigation */}
            <div className="flex justify-between mt-12 pt-6 border-t border-border">
              <Button
                variant="ghost"
                onClick={() => handlePrev(currentIndex - 1, sections[currentIndex - 1].position)}
                disabled={currentIndex === 0}
              >
                &larr; Previous
              </Button>
              <Button
                variant="ghost"
                onClick={() =>
                  handleNext(
                    currentSection.position,
                    currentIndex + 1,
                    sections[currentIndex + 1].position
                  )
                }
                disabled={currentIndex === sections.length - 1}
              >
                Next &rarr;
              </Button>
            </div>
          </div>
        </main>

        {/* Right: Evidence + Chat tabs */}
        <aside className="w-[300px] border-l border-border shrink-0 flex flex-col">
          <Tabs defaultValue={0} className="flex flex-col h-full">
            <TabsList className="w-full shrink-0">
              <TabsTrigger value={0} className="flex-1">Sources</TabsTrigger>
              <TabsTrigger value={1} className="flex-1">Chat</TabsTrigger>
            </TabsList>
            <TabsContent value={0} className="overflow-y-auto flex-1">
              <EvidencePanel courseId={courseId} sectionPosition={currentSection.position} />
            </TabsContent>
            <TabsContent value={1} className="flex-1 flex flex-col overflow-hidden">
              <ChatPanel
                courseId={courseId}
                currentSectionPosition={currentSection.position}
                currentSectionTitle={currentSection.title}
              />
            </TabsContent>
          </Tabs>
        </aside>
      </div>
    </div>
  );
}
