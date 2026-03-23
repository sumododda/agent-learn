'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { Trash2 } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { listMyCoursesWithProgress, deleteCourse } from '@/lib/api';
import { CourseWithProgress } from '@/lib/types';
import { Navbar } from '@/components/Navbar';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardHeader, CardTitle, CardDescription, CardAction, CardContent } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from '@/components/ui/alert-dialog';

const TAB_ALL = 0;
const TAB_IN_PROGRESS = 1;
const TAB_COMPLETED = 2;

export default function LibraryPage() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const [courses, setCourses] = useState<CourseWithProgress[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [tab, setTab] = useState<number>(TAB_ALL);

  const load = useCallback(async () => {
    try {
      const token = await getToken();
      const data = await listMyCoursesWithProgress(token);
      setCourses(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load courses');
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    if (isLoaded && isSignedIn) load();
  }, [isLoaded, isSignedIn, load]);

  async function handleDelete(courseId: string) {
    setDeleting(courseId);
    try {
      const token = await getToken();
      await deleteCourse(courseId, token);
      setCourses((prev) => prev.filter((c) => c.id !== courseId));
    } catch {
      setError('Failed to delete course');
    } finally {
      setDeleting(null);
    }
  }

  const filtered = courses.filter((c) => {
    if (tab === TAB_IN_PROGRESS)
      return c.progress && c.progress.completed_sections.length < c.sections.length;
    if (tab === TAB_COMPLETED)
      return c.progress && c.progress.completed_sections.length === c.sections.length;
    return true;
  });

  const statusBadge: Record<string, { text: string; className: string }> = {
    outline_ready: { text: 'Outline Ready', className: 'bg-warning/15 text-warning' },
    generating: { text: 'Generating...', className: 'bg-primary/15 text-primary' },
    researching: { text: 'Researching...', className: 'bg-primary/15 text-primary' },
    writing: { text: 'Writing...', className: 'bg-primary/15 text-primary' },
    completed: { text: 'Completed', className: 'bg-emerald-500/15 text-emerald-500' },
    completed_partial: { text: 'Partial', className: 'bg-warning/15 text-warning' },
    failed: { text: 'Failed', className: 'bg-destructive/15 text-destructive' },
  };

  return (
    <>
      <Navbar />
      <div className="max-w-[960px] mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-semibold">My Courses</h1>
        </div>

        {/* Tab filters */}
        <Tabs
          defaultValue={TAB_ALL}
          onValueChange={(val: number) => setTab(val)}
        >
          <TabsList className="mb-6">
            <TabsTrigger value={TAB_ALL}>All</TabsTrigger>
            <TabsTrigger value={TAB_IN_PROGRESS}>In Progress</TabsTrigger>
            <TabsTrigger value={TAB_COMPLETED}>Completed</TabsTrigger>
          </TabsList>
        </Tabs>

        {/* Loading state */}
        {loading && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="animate-pulse bg-muted rounded-lg h-32" />
            ))}
          </div>
        )}

        {/* Error state */}
        {!loading && error && (
          <div className="text-center text-destructive mt-20">{error}</div>
        )}

        {/* Empty state */}
        {!loading && !error && filtered.length === 0 && (
          <div className="text-center text-muted-foreground mt-20">
            <p className="mb-4">No courses yet</p>
            <Link href="/" className="text-primary hover:underline">
              Create your first course
            </Link>
          </div>
        )}

        {/* Course grid */}
        {!loading && !error && filtered.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filtered.map((course) => {
              const href =
                course.status === 'completed' || course.status === 'completed_partial'
                  ? `/courses/${course.id}/learn`
                  : `/courses/${course.id}`;

              const totalSections = course.sections.length;
              const completedCount = course.progress?.completed_sections?.length || 0;
              const progressPct = totalSections > 0 ? Math.round((completedCount / totalSections) * 100) : 0;
              const isComplete = totalSections > 0 && completedCount === totalSections;

              const badge = statusBadge[course.status] || {
                text: course.status,
                className: 'bg-muted text-muted-foreground',
              };

              return (
                <Card key={course.id} className="p-5">
                  <CardHeader>
                    <CardTitle className="text-base font-semibold">
                      <Link href={href} className="hover:underline">
                        {course.topic}
                      </Link>
                    </CardTitle>
                    <CardDescription>
                      {totalSections} sections
                    </CardDescription>
                    <CardAction>
                      <AlertDialog>
                        <AlertDialogTrigger
                          render={
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              className="h-7 w-7"
                              disabled={deleting === course.id}
                            />
                          }
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>Delete course?</AlertDialogTitle>
                            <AlertDialogDescription>
                              This will permanently delete &ldquo;{course.topic}&rdquo; and all associated data. Any active generation will be stopped.
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                            <AlertDialogAction
                              variant="destructive"
                              onClick={() => handleDelete(course.id)}
                            >
                              Delete
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    </CardAction>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    <Progress value={progressPct} className="[&_[data-slot=progress-track]]:h-1" />
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-muted-foreground">
                        {isComplete ? 'Completed' : `${progressPct}%`}
                      </span>
                      <span
                        className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}
                      >
                        {badge.text}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
