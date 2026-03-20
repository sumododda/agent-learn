'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useAuth } from '@/context/AuthContext';
import { listMyCoursesWithProgress } from '@/lib/api';
import { CourseWithProgress } from '@/lib/types';

export default function LibraryPage() {
  const { getToken } = useAuth();
  const [courses, setCourses] = useState<CourseWithProgress[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const token = await getToken();
        const data = await listMyCoursesWithProgress(token);
        setCourses(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load courses');
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [getToken]);

  if (loading) return <div className="text-center text-gray-400 mt-20">Loading your courses...</div>;
  if (error) return <div className="text-center text-red-400 mt-20">{error}</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <h1 className="text-2xl font-bold">My Courses</h1>
        <Link
          href="/"
          className="px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg text-sm font-medium transition-colors"
        >
          New Course
        </Link>
      </div>

      {courses.length === 0 ? (
        <div className="text-center text-gray-500 mt-20">
          <p className="mb-4">No courses yet.</p>
          <Link href="/" className="text-purple-400 hover:text-purple-300">
            Create your first course
          </Link>
        </div>
      ) : (
        <div className="space-y-3">
          {courses.map((course) => {
            const href =
              course.status === 'completed'
                ? `/courses/${course.id}/learn`
                : `/courses/${course.id}`;

            const statusLabel: Record<string, { text: string; color: string }> = {
              outline_ready: { text: 'Outline Ready', color: 'text-yellow-400' },
              generating: { text: 'Generating...', color: 'text-blue-400' },
              completed: { text: 'Completed', color: 'text-green-400' },
              failed: { text: 'Failed', color: 'text-red-400' },
            };

            const status = statusLabel[course.status] || { text: course.status, color: 'text-gray-400' };

            const totalSections = course.sections.length;
            const completedCount = course.progress?.completed_sections?.length || 0;
            const hasProgress = course.progress !== null && course.progress !== undefined;
            const progressPct = totalSections > 0 ? Math.round((completedCount / totalSections) * 100) : 0;

            return (
              <Link
                key={course.id}
                href={href}
                className="block p-4 bg-gray-900 border border-gray-800 rounded-lg hover:border-gray-600 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="text-white font-medium">{course.topic}</div>
                    <div className="text-gray-500 text-sm">
                      {totalSections} sections
                      {hasProgress && (
                        <span className="ml-2 text-gray-400">
                          &middot; {completedCount}/{totalSections} completed
                        </span>
                      )}
                    </div>
                    {/* Progress bar */}
                    {hasProgress && totalSections > 0 && (
                      <div className="mt-2 w-full bg-gray-800 rounded-full h-1.5">
                        <div
                          className="bg-purple-500 h-1.5 rounded-full transition-all"
                          style={{ width: `${progressPct}%` }}
                        />
                      </div>
                    )}
                  </div>
                  <span className={`text-sm ml-4 flex-shrink-0 ${status.color}`}>{status.text}</span>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
