'use client';

import { useState, useEffect, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '@/context/AuthContext';
import { createCourse, getProviders } from '@/lib/api';
import { Navbar } from '@/components/Navbar';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent } from '@/components/ui/card';
import { Label } from '@/components/ui/label';

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

const SUGGESTION_CHIPS = ['Machine Learning', 'React Hooks', 'System Design', 'Data Structures', 'Spanish'];

function StepIndicator({ current }: { current: number }) {
  return (
    <div className="flex items-center justify-center gap-2 mt-8">
      {[1, 2, 3].map((s) => (
        <div
          key={s}
          className={`h-2 w-2 rounded-full transition-colors ${
            s === current
              ? 'bg-primary'
              : s < current
                ? 'bg-green-500'
                : 'border border-border bg-transparent'
          }`}
        />
      ))}
    </div>
  );
}

function HomePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { getToken, isSignedIn, isLoaded } = useAuth();
  const initialTopic = searchParams.get('topic') || '';

  const [step, setStep] = useState(1);
  const [topic, setTopic] = useState(initialTopic);
  const [selectedStyles, setSelectedStyles] = useState<Set<string>>(new Set());
  const [englishLevel, setEnglishLevel] = useState<string | null>(null);
  const [extraInstructions, setExtraInstructions] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasProvider, setHasProvider] = useState<boolean | null>(null);
  const [useResearchPapers, setUseResearchPapers] = useState(false);
  const [yearRange, setYearRange] = useState('5y');
  const [minCitations, setMinCitations] = useState(0);
  const [openAccessOnly, setOpenAccessOnly] = useState(false);
  useEffect(() => {
    if (!isLoaded || !isSignedIn) {
      setHasProvider(null);
      return;
    }
    getToken().then((token) =>
      getProviders(token).then((providers) => setHasProvider(providers.length > 0))
    );
  }, [isLoaded, isSignedIn, getToken]);

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

  async function handleGenerate() {
    if (!topic.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const token = await getToken();
      const academicSearch = useResearchPapers
        ? { enabled: true, year_range: yearRange, min_citations: minCitations, open_access_only: openAccessOnly }
        : null;
      const course = await createCourse(topic.trim(), buildInstructions(), token, academicSearch);
      router.push(`/courses/${course.id}/discover`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setLoading(false);
    }
  }

  // Not signed in — show landing
  if (isLoaded && !isSignedIn) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[70vh] px-4">
        <div className="w-full max-w-[520px] text-center">
          <h1 className="text-4xl font-bold mb-3">Learn anything, your way</h1>
          <p className="text-muted-foreground text-lg mb-8">
            AI-powered courses built around your topic, level, and goals.
            Grounded in real sources, delivered section by section.
          </p>
          <div className="flex flex-col gap-3 sm:flex-row sm:justify-center">
            <Button size="lg" nativeButton={false} render={<Link href="/register" />}>
              Get Started
            </Button>
            <Button size="lg" variant="outline" nativeButton={false} render={<Link href="/login" />}>
              Sign In
            </Button>
          </div>
          <p className="text-sm text-muted-foreground mt-4">
            New here?{' '}
            <a
              href="https://github.com/sumododda/agent-learn/wiki"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              Read the setup guide
            </a>
          </p>
          <div className="mt-12 grid grid-cols-1 sm:grid-cols-3 gap-4 text-left">
            <div className="p-4 rounded-xl border border-border">
              <div className="text-sm font-medium mb-1">Source-grounded</div>
              <div className="text-xs text-muted-foreground">Every lesson backed by real research and citations</div>
            </div>
            <div className="p-4 rounded-xl border border-border">
              <div className="text-sm font-medium mb-1">Personalized</div>
              <div className="text-xs text-muted-foreground">Tailored to your level, style, and language preferences</div>
            </div>
            <div className="p-4 rounded-xl border border-border">
              <div className="text-sm font-medium mb-1">Progressive</div>
              <div className="text-xs text-muted-foreground">Start reading as sections complete, track your progress</div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // No-provider state
  if (isLoaded && isSignedIn && hasProvider === false) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh]">
        <h1 className="text-3xl font-bold mb-2">Welcome to agent-learn</h1>
        <p className="text-muted-foreground mb-6">
          Before creating courses, you need to configure at least one AI provider.
        </p>
        <Button nativeButton={false} render={<Link href="/settings" />}>
          Configure Providers
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] px-4">
      <div className="w-full max-w-[640px]">
        {/* Step 1: Topic */}
        {step === 1 && (
          <div className="flex flex-col items-center">
            <h1 className="text-3xl font-bold mb-2 text-center">What do you want to learn?</h1>
            <p className="text-muted-foreground mb-8 text-center">
              Enter a topic and we&apos;ll create a course for you
            </p>

            <Input
              type="text"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g. Kubernetes networking"
              className="w-full h-10 px-3"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && topic.trim()) {
                  setStep(2);
                }
              }}
            />

            <div className="flex flex-wrap gap-2 mt-4 justify-center">
              {SUGGESTION_CHIPS.map((chip) => (
                <button
                  key={chip}
                  type="button"
                  onClick={() => setTopic(chip)}
                  className="px-3 py-1 rounded-full text-sm border border-border text-muted-foreground hover:text-foreground hover:border-foreground/20 transition-colors"
                >
                  {chip}
                </button>
              ))}
            </div>

            <div className="mt-8 w-full">
              <Button
                className="w-full"
                size="lg"
                disabled={!topic.trim()}
                onClick={() => setStep(2)}
              >
                Continue
              </Button>
            </div>

            <StepIndicator current={1} />
          </div>
        )}

        {/* Step 2: Customize */}
        {step === 2 && (
          <div className="flex flex-col">
            <h1 className="text-3xl font-bold mb-6 text-center">Customize your course</h1>

            {/* Course style */}
            <div className="mb-6">
              <div className="text-muted-foreground text-xs uppercase tracking-wider mb-3">Course style</div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {STYLE_OPTIONS.map((opt) => {
                  const active = selectedStyles.has(opt.id);
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => toggleStyle(opt.id)}
                      className={`rounded-xl px-4 py-3 text-sm text-left transition-colors ring-1 ${
                        active
                          ? 'ring-primary bg-primary/10 text-foreground'
                          : 'ring-foreground/10 bg-card text-card-foreground hover:ring-foreground/20'
                      }`}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* English level */}
            <div className="mb-6">
              <div className="text-muted-foreground text-xs uppercase tracking-wider mb-3">English level</div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {ENGLISH_LEVELS.map((lvl) => {
                  const active = englishLevel === lvl.id;
                  return (
                    <button
                      key={lvl.id}
                      type="button"
                      onClick={() => setEnglishLevel(active ? null : lvl.id)}
                      className={`rounded-xl px-4 py-3 text-sm text-left transition-colors ring-1 ${
                        active
                          ? 'ring-primary bg-primary/10 text-foreground'
                          : 'ring-foreground/10 bg-card text-card-foreground hover:ring-foreground/20'
                      }`}
                    >
                      {lvl.label}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Extra instructions */}
            <div className="mb-6">
              <div className="text-muted-foreground text-xs uppercase tracking-wider mb-3">Extra instructions (optional)</div>
              <Textarea
                value={extraInstructions}
                onChange={(e) => setExtraInstructions(e.target.value)}
                placeholder="Any other preferences? e.g. &quot;Assume I know Python&quot;, &quot;Include code examples&quot;..."
                rows={2}
                className="resize-none"
              />
            </div>

            {/* Research Papers */}
            <div className="mb-6 space-y-3">
              <div className="flex items-center justify-between">
                <Label htmlFor="research-toggle">Use Research Papers</Label>
                <button
                  id="research-toggle"
                  role="switch"
                  aria-checked={useResearchPapers}
                  onClick={() => setUseResearchPapers(!useResearchPapers)}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    useResearchPapers ? 'bg-primary' : 'bg-muted'
                  } cursor-pointer`}
                >
                  <span className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${
                    useResearchPapers ? 'translate-x-6' : 'translate-x-1'
                  }`} />
                </button>
              </div>

              {useResearchPapers && (
                <div className="space-y-3 pl-1 border-l-2 border-primary/20 ml-1">
                  <div className="space-y-1">
                    <Label className="text-sm">Year Range</Label>
                    <select
                      value={yearRange}
                      onChange={(e) => setYearRange(e.target.value)}
                      className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                    >
                      <option value="5y">Last 5 years</option>
                      <option value="10y">Last 10 years</option>
                      <option value="20y">Last 20 years</option>
                      <option value="all">All time</option>
                    </select>
                  </div>

                  <div className="space-y-1">
                    <Label className="text-sm">Minimum Citations</Label>
                    <select
                      value={minCitations}
                      onChange={(e) => setMinCitations(Number(e.target.value))}
                      className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                    >
                      <option value={0}>Any</option>
                      <option value={10}>10+</option>
                      <option value={50}>50+</option>
                      <option value={100}>100+</option>
                    </select>
                  </div>

                  <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <Label className="text-sm" htmlFor="oa-toggle">Open Access Only</Label>
                    <button
                      id="oa-toggle"
                      role="switch"
                      aria-checked={openAccessOnly}
                      onClick={() => setOpenAccessOnly(!openAccessOnly)}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                        openAccessOnly ? 'bg-primary' : 'bg-muted'
                      } cursor-pointer`}
                    >
                      <span className={`inline-block h-3 w-3 rounded-full bg-white transition-transform ${
                        openAccessOnly ? 'translate-x-5' : 'translate-x-1'
                      }`} />
                    </button>
                  </div>
                  <p className="text-xs text-muted-foreground">Only include papers with free full-text PDFs</p>
                  </div>
                </div>
              )}
            </div>

            <div className="flex gap-3">
              <Button variant="outline" className="flex-1" size="lg" onClick={() => setStep(1)}>
                Back
              </Button>
              <Button className="flex-1" size="lg" onClick={() => setStep(3)}>
                Continue
              </Button>
            </div>

            <StepIndicator current={2} />
          </div>
        )}

        {/* Step 3: Generate */}
        {step === 3 && (
          <div className="flex flex-col items-center">
            <h1 className="text-3xl font-bold mb-6 text-center">Ready to generate</h1>

            <Card className="w-full mb-6">
              <CardContent className="space-y-3">
                <div>
                  <div className="text-muted-foreground text-xs uppercase tracking-wider mb-1">Topic</div>
                  <div className="text-foreground font-medium">{topic}</div>
                </div>
                {selectedStyles.size > 0 && (
                  <div>
                    <div className="text-muted-foreground text-xs uppercase tracking-wider mb-1">Style</div>
                    <div className="text-foreground">
                      {STYLE_OPTIONS.filter(o => selectedStyles.has(o.id)).map(o => o.label).join(', ')}
                    </div>
                  </div>
                )}
                {englishLevel && (
                  <div>
                    <div className="text-muted-foreground text-xs uppercase tracking-wider mb-1">English level</div>
                    <div className="text-foreground">
                      {ENGLISH_LEVELS.find(l => l.id === englishLevel)?.label}
                    </div>
                  </div>
                )}
                {extraInstructions.trim() && (
                  <div>
                    <div className="text-muted-foreground text-xs uppercase tracking-wider mb-1">Extra instructions</div>
                    <div className="text-foreground text-sm">{extraInstructions.trim()}</div>
                  </div>
                )}
                {useResearchPapers && (
                  <div>
                    <div className="text-muted-foreground text-xs uppercase tracking-wider mb-1">Research papers</div>
                    <div className="text-foreground text-sm">
                      {yearRange === 'all' ? 'All time' : `Last ${yearRange.replace('y', ' years')}`}
                      {minCitations > 0 ? `, ${minCitations}+ citations` : ''}
                      {openAccessOnly ? ', open access only' : ''}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {error && <p className="text-destructive text-sm text-center mb-4">{error}</p>}

            <div className="flex gap-3 w-full">
              <Button variant="outline" className="flex-1" size="lg" onClick={() => setStep(2)} disabled={loading}>
                Back
              </Button>
              <Button className="flex-1" size="lg" onClick={handleGenerate} disabled={loading}>
                {loading ? 'Generating...' : 'Generate Course'}
              </Button>
            </div>

            <StepIndicator current={3} />
          </div>
        )}
      </div>
    </div>
  );
}

export default function HomePage() {
  return (
    <>
      <Navbar />
      <Suspense fallback={<div className="text-center text-muted-foreground mt-20">Loading...</div>}>
        <HomePageInner />
      </Suspense>
    </>
  );
}
