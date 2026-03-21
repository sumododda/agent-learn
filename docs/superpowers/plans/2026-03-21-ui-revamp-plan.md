# UI Revamp Implementation Plan — Modern Minimal Redesign

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the agent-learn frontend from a dark-only, custom-Tailwind UI to a modern minimal design using shadcn/ui, next-themes (dark default + light toggle), and Framer Motion — matching the approved Stitch mockups in project `7292737560165135148`.

**Architecture:** Big-bang rewrite of all 8 pages and 6 components. shadcn/ui provides the component primitives. next-themes handles dark/light switching. Framer Motion handles page transitions. The existing API layer (`src/lib/api.ts`), auth context (`src/context/AuthContext.tsx`), and type definitions (`src/lib/types.ts`) remain unchanged.

**Tech Stack:** Next.js 16 + React 19 + Tailwind v4 + shadcn/ui (New York) + next-themes + Framer Motion + Lucide React

**Design Reference:** Stitch mockups at `docs/stitch-designs/*.html` and spec at `docs/superpowers/specs/2026-03-21-ui-revamp-design.md`

**Color Tokens:**
- Dark: bg `#09090b`, surface `#18181b`, border `#27272a`, text `#fafafa`, muted `#a1a1aa`
- Light: bg `#ffffff`, surface `#f8fafc`, border `#e2e8f0`, text `#0f172a`, muted `#64748b`
- Accent: `#6366f1` (indigo-500) — buttons and active states only
- Success: `#22c55e`, Warning: `#f59e0b`, Error: `#ef4444`

---

## File Structure

### New files to create:
- `frontend/components.json` — shadcn/ui config
- `frontend/src/lib/utils.ts` — `cn()` helper (shadcn standard)
- `frontend/src/components/ui/` — shadcn/ui primitives (button, input, card, tabs, command, dialog, sheet, progress, tooltip, alert-dialog)
- `frontend/src/components/Navbar.tsx` — new app navbar (replaces AuthHeader)
- `frontend/src/components/ThemeProvider.tsx` — next-themes wrapper
- `frontend/src/components/PageTransition.tsx` — Framer Motion wrapper

### Files to rewrite:
- `frontend/src/app/layout.tsx` — add ThemeProvider, swap AuthHeader → Navbar, remove hardcoded dark classes
- `frontend/src/app/globals.css` — add CSS variables for theme, update .learn-content for light/dark
- `frontend/src/app/page.tsx` — single form → 3-step wizard
- `frontend/src/app/login/page.tsx` — centered card redesign
- `frontend/src/app/register/page.tsx` — centered card + Turnstile
- `frontend/src/app/verify/page.tsx` — centered card + OTP
- `frontend/src/app/library/page.tsx` — 2-col grid + tabs + progress bars
- `frontend/src/app/settings/page.tsx` — horizontal tabbed interface
- `frontend/src/app/courses/[id]/learn/page.tsx` — 3-panel layout
- `frontend/src/components/AuthHeader.tsx` — DELETE (replaced by Navbar)
- `frontend/src/components/ChatDrawer.tsx` — move into right panel of learning view
- `frontend/src/components/EvidencePanel.tsx` — restyle for right panel
- `frontend/src/components/PipelineProgress.tsx` — horizontal node pipeline + terminal log

### Files unchanged:
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`
- `frontend/src/context/AuthContext.tsx`
- `frontend/src/components/CitationRenderer.tsx` (minor restyle only)
- `frontend/src/components/MermaidBlock.tsx` (minor restyle only)

---

## Task 1: Install Dependencies & Initialize shadcn/ui

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/components.json`
- Create: `frontend/src/lib/utils.ts`

- [ ] **Step 1: Install shadcn/ui dependencies**

```bash
cd frontend
npx shadcn@latest init
```

When prompted:
- Style: New York
- Base color: Zinc
- CSS variables: Yes
- Tailwind CSS: (auto-detected v4)
- Components alias: `@/components`
- Utils alias: `@/lib`

This creates `components.json` and `src/lib/utils.ts` with the `cn()` helper.

- [ ] **Step 2: Install next-themes, framer-motion, lucide-react**

```bash
cd frontend && npm install next-themes framer-motion lucide-react
```

- [ ] **Step 3: Install shadcn/ui components we need**

```bash
cd frontend
npx shadcn@latest add button input card tabs command dialog sheet progress tooltip alert-dialog dropdown-menu label separator
```

- [ ] **Step 4: Verify installation**

```bash
cd frontend && npm run build
```

Expected: Build succeeds with no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/
git commit -m "feat: install shadcn/ui, next-themes, framer-motion, lucide-react"
```

---

## Task 2: Theme System & Global Styles

**Files:**
- Create: `frontend/src/components/ThemeProvider.tsx`
- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/src/app/layout.tsx`

- [ ] **Step 1: Create ThemeProvider component**

Create `frontend/src/components/ThemeProvider.tsx`:

```tsx
'use client';

import { ThemeProvider as NextThemesProvider } from 'next-themes';
import type { ReactNode } from 'react';

export function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider attribute="class" defaultTheme="dark" enableSystem={false}>
      {children}
    </NextThemesProvider>
  );
}
```

- [ ] **Step 2: Update globals.css with theme CSS variables**

Replace the entire `frontend/src/app/globals.css` with theme-aware styles. Key changes:
- Add `:root` and `.dark` CSS variable blocks for all color tokens
- Update `.learn-content` to use CSS variables instead of hardcoded dark colors
- Keep the `@keyframes shake` animation
- Remove `@plugin "@tailwindcss/typography"` (we'll use our own prose styles)

The CSS variables should follow the shadcn/ui convention:
```css
:root {
  --background: 0 0% 100%;
  --foreground: 222.2 84% 4.9%;
  --card: 210 40% 98%;
  --card-foreground: 222.2 84% 4.9%;
  --primary: 238.7 83.5% 66.7%;
  --primary-foreground: 0 0% 100%;
  --muted: 210 40% 96%;
  --muted-foreground: 215.4 16.3% 46.9%;
  --border: 214.3 31.8% 91.4%;
  --input: 214.3 31.8% 91.4%;
  --ring: 238.7 83.5% 66.7%;
  --radius: 0.5rem;
}

.dark {
  --background: 240 10% 3.9%;
  --foreground: 0 0% 98%;
  --card: 240 5.9% 10%;
  --card-foreground: 0 0% 98%;
  --primary: 238.7 83.5% 66.7%;
  --primary-foreground: 0 0% 100%;
  --muted: 240 3.7% 15.9%;
  --muted-foreground: 240 5% 64.9%;
  --border: 240 3.7% 15.9%;
  --input: 240 3.7% 15.9%;
  --ring: 238.7 83.5% 66.7%;
}
```

Update `.learn-content` to use themed colors:
```css
.learn-content {
  font-family: Georgia, 'Merriweather', 'Times New Roman', serif;
  font-size: 17px;
  line-height: 1.85;
  color: hsl(var(--foreground) / 0.8);
}
.learn-content h2, .learn-content h3, .learn-content h4 {
  font-family: var(--font-inter), -apple-system, sans-serif;
  color: hsl(var(--foreground));
}
.learn-content pre {
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
}
.learn-content code {
  background: hsl(var(--muted));
  border: 1px solid hsl(var(--border));
}
.learn-content blockquote {
  border-left: 3px solid hsl(var(--primary) / 0.3);
  color: hsl(var(--muted-foreground));
}
.learn-content a {
  color: hsl(var(--primary));
}
.learn-content hr {
  border-top: 1px solid hsl(var(--border));
}
```

- [ ] **Step 3: Update layout.tsx**

Rewrite `frontend/src/app/layout.tsx`:

```tsx
import './globals.css';
import type { Metadata } from 'next';
import { Inter, JetBrains_Mono } from 'next/font/google';
import { AuthProvider } from '@/context/AuthContext';
import { ThemeProvider } from '@/components/ThemeProvider';

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
});

export const metadata: Metadata = {
  title: 'agent-learn',
  description: 'AI-powered personalized course generation',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} ${jetbrainsMono.variable} font-sans bg-background text-foreground min-h-screen antialiased`}>
        <ThemeProvider>
          <AuthProvider>
            {children}
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
```

Note: AuthHeader is removed from layout — Navbar will be added per-page where needed (not on auth pages or pipeline progress).

- [ ] **Step 4: Verify build**

```bash
cd frontend && npm run build
```

Expected: Build succeeds. Pages may look broken (no navbar yet) — that's expected.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ThemeProvider.tsx frontend/src/app/globals.css frontend/src/app/layout.tsx
git commit -m "feat: add theme system with CSS variables, next-themes, font loading"
```

---

## Task 3: Navbar Component

**Files:**
- Create: `frontend/src/components/Navbar.tsx`
- Delete: `frontend/src/components/AuthHeader.tsx`

- [ ] **Step 1: Create Navbar component**

Create `frontend/src/components/Navbar.tsx`:

```tsx
'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { Settings, Moon, Sun, LogOut } from 'lucide-react';
import { useTheme } from 'next-themes';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

export function Navbar() {
  const pathname = usePathname();
  const { isSignedIn, logout } = useAuth();
  const { theme, setTheme } = useTheme();

  return (
    <nav className="sticky top-0 z-50 h-12 border-b border-border bg-background">
      <div className="flex h-full items-center justify-between px-4 max-w-7xl mx-auto">
        {/* Left: Logo */}
        <Link href="/" className="text-sm font-semibold text-foreground">
          agent-learn
        </Link>

        {/* Center: Nav links */}
        {isSignedIn && (
          <div className="flex items-center gap-6">
            <Link
              href="/"
              className={`text-sm ${pathname === '/' ? 'text-primary' : 'text-muted-foreground hover:text-foreground'} transition-colors`}
            >
              Create
            </Link>
            <Link
              href="/library"
              className={`text-sm ${pathname === '/library' ? 'text-primary' : 'text-muted-foreground hover:text-foreground'} transition-colors`}
            >
              Library
            </Link>
          </div>
        )}

        {/* Right: Actions */}
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          >
            <Sun className="h-4 w-4 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
            <Moon className="absolute h-4 w-4 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
          </Button>

          {isSignedIn && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="h-8 w-8">
                  <div className="h-6 w-6 rounded-full bg-muted" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem asChild>
                  <Link href="/settings">
                    <Settings className="mr-2 h-4 w-4" />
                    Settings
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem onClick={logout}>
                  <LogOut className="mr-2 h-4 w-4" />
                  Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          {!isSignedIn && (
            <Button variant="ghost" size="sm" asChild>
              <Link href="/login">Sign in</Link>
            </Button>
          )}
        </div>
      </div>
    </nav>
  );
}
```

- [ ] **Step 2: Delete old AuthHeader**

```bash
rm frontend/src/components/AuthHeader.tsx
```

- [ ] **Step 3: Verify build**

```bash
cd frontend && npm run build
```

Expected: Build may show warnings about missing AuthHeader imports (pages that import it will be fixed in subsequent tasks). That's OK for now — we're rewriting those pages.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Navbar.tsx
git rm frontend/src/components/AuthHeader.tsx
git commit -m "feat: add minimal Navbar with theme toggle, remove AuthHeader"
```

---

## Task 4: Auth Pages — Login

**Files:**
- Rewrite: `frontend/src/app/login/page.tsx`

- [ ] **Step 1: Rewrite login page**

Rewrite `frontend/src/app/login/page.tsx` to match the Stitch mockup `04-login.html`:

```tsx
'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '@/context/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Eye, EyeOff } from 'lucide-react';

export default function LoginPage() {
  const router = useRouter();
  const { login } = useAuth();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
      router.push('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4">
      <div className="text-sm font-semibold text-foreground mb-8">agent-learn</div>

      <Card className="w-full max-w-[380px]">
        <CardHeader>
          <CardTitle className="text-xl">Sign in</CardTitle>
          <CardDescription>Welcome back</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div className="text-sm text-red-500 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2">
                {error}
              </div>
            )}

            <div className="space-y-1">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="password">Password</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? 'Signing in...' : 'Sign in'}
            </Button>

            <p className="text-center text-sm text-muted-foreground">
              Don&apos;t have an account?{' '}
              <Link href="/register" className="text-primary hover:underline">
                Sign up
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: Verify build and test manually**

```bash
cd frontend && npm run build
```

Expected: Build succeeds. Login page renders as centered card on dark background.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/login/page.tsx
git commit -m "feat: redesign login page with shadcn Card, minimal centered layout"
```

---

## Task 5: Auth Pages — Register

**Files:**
- Rewrite: `frontend/src/app/register/page.tsx`

- [ ] **Step 1: Rewrite register page**

Same centered card layout as login, but with Turnstile widget. Preserve the existing `register()` call from AuthContext and Turnstile token handling.

Key differences from login:
- Heading: "Create account" / "Get started with agent-learn"
- Includes `<Turnstile>` component below password field
- Submit passes `turnstileToken` to `register()`
- Redirects to `/verify?email=...` on success
- Footer link: "Already have an account? Sign in"

The Turnstile widget should be styled to blend in: wrap in a `<div className="flex justify-center">`.

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/register/page.tsx
git commit -m "feat: redesign register page with shadcn Card + Turnstile"
```

---

## Task 6: Auth Pages — Verify OTP

**Files:**
- Rewrite: `frontend/src/app/verify/page.tsx`

- [ ] **Step 1: Rewrite verify page**

Same centered card layout. Preserve ALL existing OTP logic:
- 6-digit OTP input array with auto-advance
- Paste support
- Resend cooldown timer
- Shake animation on error
- Redirect to `/` on success

Key changes:
- Wrap in Card component
- Use shadcn Input for OTP boxes (styled as square boxes: `w-10 h-10 text-center text-lg`)
- Use shadcn Button for "Resend code"
- Keep the `animate-shake` class from globals.css

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/verify/page.tsx
git commit -m "feat: redesign OTP verify page with shadcn Card"
```

---

## Task 7: Settings Page

**Files:**
- Rewrite: `frontend/src/app/settings/page.tsx`

- [ ] **Step 1: Rewrite settings page with horizontal tabs**

Use shadcn Tabs component with three tabs: "AI Provider", "Search", "Account".

Preserve ALL existing API integration:
- `getProviders`, `saveProvider`, `updateProvider`, `deleteProvider`, `testProvider`
- `getSearchProviderRegistry`, `getSearchProviders`, `saveSearchProvider`, `updateSearchProvider`, `deleteSearchProvider`, `testSearchProvider`, `setDefaultSearchProvider`

Layout: max-width 720px, centered. `<Navbar />` at top.

**AI Provider tab:**
- OpenRouter subheading
- API key: `<Input type="password">` with eye toggle + "Verify" ghost button
- Status indicator: small green/red circle + "Connected"/"Not connected" text
- Model: shadcn Command combobox (or simple Input with current model value)
- "Test Connection" ghost button + latency result

**Search tab:**
- Card-based radio group: map over search provider registry, render each as a selectable card
- Selected card gets `border-primary` class
- API key input + verify button for selected provider

**Account tab:**
- Email display (read-only Input with `disabled`)
- "Coming soon" text for password change and account deletion

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/settings/page.tsx
git commit -m "feat: redesign settings with horizontal tabs, shadcn form components"
```

---

## Task 8: Home Page — 3-Step Wizard

**Files:**
- Rewrite: `frontend/src/app/page.tsx`

- [ ] **Step 1: Rewrite home page as wizard**

Replace the single form with a 3-step wizard. Max-width 640px, centered. `<Navbar />` at top.

Preserve existing data: `STYLE_OPTIONS`, `ENGLISH_LEVELS`, API calls to `createCourse`, `generateCourse`, `getProviders`.

**Wizard state:**
```tsx
const [step, setStep] = useState(1);
const [topic, setTopic] = useState('');
const [selectedStyle, setSelectedStyle] = useState<string | null>(null);
const [englishLevel, setEnglishLevel] = useState<string | null>(null);
const [extraInstructions, setExtraInstructions] = useState('');
const [selectedModel, setSelectedModel] = useState('');
const [generating, setGenerating] = useState(false);
```

**Step 1:** Topic input + suggestion chips + step indicator + "Continue" button.

Suggestion chips (hardcoded):
```tsx
const SUGGESTIONS = ['Machine Learning', 'React Hooks', 'System Design', 'Data Structures', 'Spanish'];
```

**Step 2:** Card selectors for style and language level. Each option is a Card with `onClick`, `border-primary` when selected. Back + Continue buttons.

**Step 3:** Summary Card showing selections. "Generate Course" button triggers `createCourse()` then `generateCourse()`, then sets `generating = true`.

**When generating:** Render `<PipelineProgress>` inline (the component will be restyled in Task 11).

**Step indicator:** Three small circles (8px). Current = `bg-primary`, completed = `bg-green-500`, pending = `border border-border`.

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/page.tsx
git commit -m "feat: redesign home page as 3-step course creation wizard"
```

---

## Task 9: Library / Dashboard

**Files:**
- Rewrite: `frontend/src/app/library/page.tsx`

- [ ] **Step 1: Rewrite library page**

Max-width 960px, centered. `<Navbar />` at top.

Preserve existing logic: `listMyCoursesWithProgress`, `deleteCourse`, auth guards (`isLoaded && isSignedIn`).

**Header row:** "My Courses" heading (text-2xl font-semibold) left. Tab filters right using shadcn Tabs:
```tsx
<Tabs defaultValue="all">
  <TabsList>
    <TabsTrigger value="all">All</TabsTrigger>
    <TabsTrigger value="in-progress">In Progress</TabsTrigger>
    <TabsTrigger value="completed">Completed</TabsTrigger>
  </TabsList>
</Tabs>
```

Filter logic:
```tsx
const filtered = courses.filter(c => {
  if (tab === 'in-progress') return c.progress && c.progress.completed_sections.length < c.sections.length;
  if (tab === 'completed') return c.progress && c.progress.completed_sections.length === c.sections.length;
  return true;
});
```

**Course cards:** 2-col grid (`grid grid-cols-1 md:grid-cols-2 gap-4`). Each card:
- shadcn Card with padding-5
- Title: text-base font-semibold
- Subtitle: `${sections.length} sections · ${timeAgo}` in text-sm text-muted-foreground
- Progress bar: shadcn Progress component (4px height via className)
- Percentage or "Completed" text

Delete: small trash icon button in card corner, AlertDialog confirmation.

Empty state: centered text "No courses yet" + Link to create.

Loading state: skeleton cards (div with `animate-pulse bg-muted rounded-lg h-32`).

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/library/page.tsx
git commit -m "feat: redesign library as 2-col grid with tabs and progress bars"
```

---

## Task 10: Learning View — 3-Panel Layout

**Files:**
- Rewrite: `frontend/src/app/courses/[id]/learn/page.tsx`
- Modify: `frontend/src/components/EvidencePanel.tsx`
- Modify: `frontend/src/components/ChatDrawer.tsx`

This is the most complex task. The learning view goes from a sidebar + content + floating chat drawer to a 3-panel layout with tabbed right panel.

- [ ] **Step 1: Restyle EvidencePanel for right panel**

Update `frontend/src/components/EvidencePanel.tsx`:
- Remove the collapsible toggle button (panel visibility is controlled by parent)
- Each source card: simple div with `border-b border-border p-3`
- Source title: text-sm font-medium
- URL: text-xs text-muted-foreground truncate
- Tier badge: small span with `text-xs bg-muted px-1.5 py-0.5 rounded` and colored text (green for Tier 1, blue for Tier 2, yellow for Tier 3)
- Keep existing API calls to `getEvidence`

- [ ] **Step 2: Restyle ChatDrawer as inline chat panel**

Update `frontend/src/components/ChatDrawer.tsx`:
- Remove the floating pill button and bottom-drawer animation
- Instead, export a `ChatPanel` component that renders inline (takes full height of its container)
- Keep ALL existing chat logic: `sendChatMessage`, `getChatHistory`, streaming SSE, model picker, markdown rendering
- Model picker moves to panel header as a small dropdown
- Messages: user = right-aligned, assistant = left-aligned with markdown

- [ ] **Step 3: Rewrite the learning view page**

Rewrite `frontend/src/app/courses/[id]/learn/page.tsx`:

Layout structure (no Navbar — full-height 3-panel):
```tsx
<div className="h-screen flex flex-col">
  {/* Top bar: breadcrumb + progress */}
  <div className="h-10 border-b border-border flex items-center px-4 shrink-0">
    <span className="text-xs text-muted-foreground">
      <Link href="/library">Library</Link> / {course.topic} / {currentSection.title}
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
      ...
    </aside>

    {/* Center: Content */}
    <main className="flex-1 overflow-y-auto">
      <div className="max-w-[680px] mx-auto px-10 py-12">
        ...
      </div>
    </main>

    {/* Right: Evidence + Chat */}
    <aside className="w-[300px] border-l border-border shrink-0 flex flex-col">
      <Tabs defaultValue="sources">
        <TabsList className="w-full">
          <TabsTrigger value="sources" className="flex-1">Sources</TabsTrigger>
          <TabsTrigger value="chat" className="flex-1">Chat</TabsTrigger>
        </TabsList>
        <TabsContent value="sources" className="overflow-y-auto flex-1">
          <EvidencePanel ... />
        </TabsContent>
        <TabsContent value="chat" className="overflow-y-auto flex-1">
          <ChatPanel ... />
        </TabsContent>
      </Tabs>
    </aside>
  </div>
</div>
```

Preserve ALL existing logic:
- Course loading from API
- Section navigation (currentIndex, completedSections)
- Progress tracking (getProgress, updateProgress)
- ReactMarkdown rendering with MermaidBlock
- CitationRenderer integration

**Left sidebar sections:**
- Completed: `<Check className="h-3 w-3 text-green-500" />` + gray-400 text
- Active: `bg-muted border-l-2 border-primary` + foreground text
- Pending: muted-foreground text

**Center content:** Keep `.learn-content` class on the markdown container. Add "Previous" / "Next" buttons at bottom.

**Responsive:** For this task, implement desktop only. Mobile responsiveness (Sheet drawers) can be a follow-up.

- [ ] **Step 4: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/courses/[id]/learn/page.tsx frontend/src/components/EvidencePanel.tsx frontend/src/components/ChatDrawer.tsx
git commit -m "feat: redesign learning view as 3-panel layout with tabbed evidence/chat"
```

---

## Task 11: Pipeline Progress — Horizontal Nodes + Terminal Log

**Files:**
- Rewrite: `frontend/src/components/PipelineProgress.tsx`

- [ ] **Step 1: Rewrite PipelineProgress component**

Replace the vertical step list with a horizontal node pipeline + terminal-style activity log.

Props remain the same (receives course status via polling or SSE).

**Pipeline nodes:** 5 horizontal circles connected by lines.
```tsx
const STAGES = ['research', 'verify', 'write', 'edit', 'complete'];

function getStageStatus(currentStage: string, stage: string): 'completed' | 'active' | 'pending' {
  const currentIdx = STAGES.indexOf(currentStage);
  const stageIdx = STAGES.indexOf(stage);
  if (stageIdx < currentIdx) return 'completed';
  if (stageIdx === currentIdx) return 'active';
  return 'pending';
}
```

Each node:
- Completed: `bg-green-500` circle with `<Check>` icon, green label
- Active: `bg-primary` circle with small dot, primary label
- Pending: `border border-border` circle, muted label
- Connecting lines: `bg-green-500` (completed), `bg-primary` (to active), `border-t border-dashed border-border` (pending)

**Activity log:** Card with monospace font, scrolling log lines:
```tsx
<div className="bg-card border border-border rounded-lg p-4 font-mono text-sm max-h-60 overflow-y-auto">
  {logs.map((log, i) => (
    <div key={i} className={i === logs.length - 1 ? 'text-foreground' : 'text-muted-foreground'}>
      {log.timestamp}  {log.message}
    </div>
  ))}
</div>
```

**Footer:** "Section X of Y · ~Z min remaining" in text-xs text-muted-foreground.

Preserve existing: course status polling, stage detection from `pipeline_status`, section progress tracking.

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/PipelineProgress.tsx
git commit -m "feat: redesign pipeline progress as horizontal nodes + terminal log"
```

---

## Task 12: Course Outline Page

**Files:**
- Rewrite: `frontend/src/app/courses/[id]/page.tsx`

- [ ] **Step 1: Restyle outline review page**

This page shows the course outline after creation, before full generation. Add `<Navbar />`. Max-width 720px, centered.

Preserve ALL existing logic:
- Course loading, generation, regeneration
- Section comment inputs
- Overall comment
- PipelineProgress integration

Restyle using shadcn components:
- Course title: text-2xl font-semibold
- Section list: Card for each section with title + summary
- Comment inputs: shadcn Input
- Action buttons: shadcn Button (primary for generate, ghost for regenerate)

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/courses/[id]/page.tsx
git commit -m "feat: restyle course outline page with shadcn components"
```

---

## Task 13: Loading States & Page Transitions

**Files:**
- Create: `frontend/src/components/PageTransition.tsx`
- Modify: `frontend/src/app/courses/[id]/loading.tsx`
- Modify: `frontend/src/app/courses/[id]/learn/loading.tsx`

- [ ] **Step 1: Create PageTransition wrapper**

Create `frontend/src/components/PageTransition.tsx`:

```tsx
'use client';

import { motion } from 'framer-motion';
import type { ReactNode } from 'react';

export function PageTransition({ children }: { children: ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.15, ease: 'easeOut' }}
    >
      {children}
    </motion.div>
  );
}
```

- [ ] **Step 2: Update loading states**

Update loading.tsx files to use skeleton styles:

```tsx
export default function Loading() {
  return (
    <div className="space-y-4 p-8 max-w-3xl mx-auto">
      <div className="h-8 w-48 bg-muted animate-pulse rounded" />
      <div className="h-4 w-full bg-muted animate-pulse rounded" />
      <div className="h-4 w-3/4 bg-muted animate-pulse rounded" />
      <div className="h-32 w-full bg-muted animate-pulse rounded-lg" />
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/PageTransition.tsx frontend/src/app/courses/
git commit -m "feat: add page transition animation and skeleton loading states"
```

---

## Task 14: Final Cleanup & Verification

**Files:**
- Multiple minor cleanups

- [ ] **Step 1: Remove unused imports across all files**

Search for any remaining imports of `AuthHeader` and remove them. Check for unused imports.

```bash
cd frontend && grep -r "AuthHeader" src/
```

- [ ] **Step 2: Full build verification**

```bash
cd frontend && npm run build
```

Expected: Clean build with no errors.

- [ ] **Step 3: Run linter**

```bash
cd frontend && npm run lint
```

Fix any lint errors.

- [ ] **Step 4: Manual testing checklist**

Test each page in browser (dark mode default):
- [ ] Login page: centered card, form submits
- [ ] Register page: centered card with Turnstile, form submits
- [ ] Verify page: OTP input works, paste works, resend works
- [ ] Home page: 3-step wizard flows correctly, generates course
- [ ] Library page: shows courses, tabs filter, delete works
- [ ] Settings page: provider config works, test connection works
- [ ] Learning view: 3-panel layout, section nav works, evidence loads, chat works
- [ ] Theme toggle: light/dark switch works on all pages

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: cleanup unused imports and fix lint issues after UI revamp"
```
