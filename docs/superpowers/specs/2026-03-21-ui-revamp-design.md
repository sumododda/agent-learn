# agent-learn UI Revamp — Modern Minimal Redesign

**Date:** 2026-03-21
**Status:** Approved
**Direction:** Modern minimal, dark-first, Linear/Vercel/Raycast energy. Light+dark with toggle, dark default.
**Stitch reference:** Project `7292737560165135148` — 6 screen mockups approved

---

## 1. Design System & Theme

### Color Palette
- **Primary accent:** `#6366f1` (indigo-500) — used ONLY for primary buttons and active states. Everything else is grayscale.
- **Dark mode (default):** Background `#09090b` (gray-950), surfaces `#18181b` (gray-900), borders `#27272a` (gray-800), text `#fafafa` (gray-50), muted `#a1a1aa` (gray-400)
- **Light mode:** Background `#ffffff`, surfaces `#f8fafc` (slate-50), borders `#e2e8f0` (slate-200), text `#0f172a` (slate-900), muted `#64748b` (slate-500)
- **Semantic:** Success `#22c55e` (green-500), Warning `#f59e0b` (amber-500), Error `#ef4444` (red-500)
- **No gradients on cards or backgrounds.** No glassmorphism. No blur effects. Solid surfaces only.

### Typography
- **UI:** Inter (loaded via `next/font/google`) — all interface elements
- **Content:** Georgia (system serif) — lesson reading. Merriweather as CSS fallback only.
- **Code:** JetBrains Mono (loaded via `next/font/google`) — code blocks
- **Scale:** 12.8 / 14 / 16 / 20 / 25 / 31px. Headings: font-weight 600, letter-spacing -0.02em. Body: 16px, line-height 1.5.

### Component Library
shadcn/ui with "New York" style variant. Key components: Button, Input, Card, Dialog, Tabs, Command (combobox), Dropdown, Sheet, Progress, Tooltip, AlertDialog.

### Borders & Depth
- **All borders:** 1px solid, `#27272a` (dark) / `#e2e8f0` (light). Consistent everywhere.
- **Border radius:** 8px (`rounded-lg`) consistently. No mixing of radius sizes.
- **Shadows:** Minimal. Small shadow for interactive elements in light mode. None in dark mode — use tonal surface differences for elevation.
- **No glassmorphism, no backdrop-blur, no glow effects.**

### Motion & Accessibility
- Framer Motion for page transitions (150ms fade+slide) and functional micro-interactions only.
- Only animate `transform` and `opacity` (GPU-accelerated).
- All animations respect `prefers-reduced-motion: reduce` via Framer Motion's `useReducedMotion` hook.
- No decorative animations (no pulsing, floating, confetti, mesh backgrounds).

### Loading States
Skeleton/shimmer states matching final layout shapes. Cards show gray bar placeholders, text areas show animated gray bars. Same dimensions as loaded content to prevent layout shift.

### Error States
API failures: inline error card (red-500 border, retry button) within content area. Never full-page error unless page can't render at all.

---

## 2. Layout Shell & Navigation

### Navbar
- Height: 48px. Solid background (gray-950 dark / white light). 1px bottom border.
- Left: "agent-learn" text wordmark (font-weight 600, no icon)
- Center: "Create" / "Library" text links. Active link in indigo-500, inactive in gray-400.
- Right: Settings gear icon + 32px circle avatar placeholder
- Mobile: hamburger → Sheet (shadcn) slide-in from right

### Page Transitions
Framer Motion `AnimatePresence`: 150ms fade + slight upward slide.

---

## 3. Home — Course Creation Wizard

Multi-step wizard replacing the single form. Max-width 640px, centered.

### Wizard State Management
- All wizard state in React `useState` — no URL params, no external store
- Browser back button does NOT navigate wizard steps (single page, not routes)
- Page refresh resets to Step 1 (acceptable)
- Suggested topics are a hardcoded curated list

### Wizard-to-Pipeline Transition
"Generate Course" in Step 3 fades out wizard, renders pipeline progress inline in same container. On completion, "View your course" navigates to course outline page.

### Step 1: "What will you learn?"
- Heading: "What will you learn?" — 25px, font-weight 600, left-aligned
- Large input: height 48px, bg gray-900, border gray-800, placeholder cycles examples
- Suggestion chips below: bg gray-900, border gray-800, text gray-400, text-sm
- Step indicator: three 8px circles (filled indigo = current, gray-800 outline = pending)
- "Continue" button: bg indigo-500, text white, right-aligned

### Step 2: "Customize your experience"
- Card-based selectors (3 cards per row, 12px gap):
  - Course style: Hands-on / Beginner / Technical
  - Language level: Simple / Intermediate / Advanced
  - Selected state: 1px indigo-500 border + small checkmark
- Optional "Advanced" collapsible for extra instructions + model selection (Command combobox)
- Back (ghost) + Continue (indigo) buttons

### Step 3: "Review & Generate"
- Summary card with all selections
- "Generate Course" button (bg indigo-500)

---

## 4. Library / Dashboard

Max-width 960px, centered.

### Header
- "My Courses" heading (25px, font-weight 600) left
- Tab filters right: "All" (active, indigo-500 underline), "In Progress", "Completed" — text-sm

### Course Grid
- 2-column responsive grid, 16px gap. 1-col on mobile.
- Each card: bg gray-900, border gray-800, rounded-lg, padding 20px
  - Title: 16px font-weight 600
  - Subtitle: "12 sections · Started 3 days ago" text-sm gray-400
  - Progress bar: 4px height, gray-800 bg, indigo-500 fill
  - Percentage: text-xs gray-500, right-aligned. Completed courses show green "Completed" text.
- Delete: hover-reveal trash icon, AlertDialog confirmation
- Empty state: text only "No courses yet" + "Create your first course" link. No illustrations.

---

## 5. Learning View

3-panel layout, full-height.

### Responsive Breakpoints
| Viewport | Left Panel | Center | Right Panel |
|----------|-----------|--------|-------------|
| >= 1440px | 240px sidebar | fluid (max 680px) | 300px tabbed panel |
| 1024–1439px | 240px sidebar | fluid | right panel collapsed to edge tab |
| 768–1023px | 48px icon rail | fluid | collapsed to edge tab |
| < 768px | Sheet drawer (swipe) | full width | Sheet drawer (swipe up) |

### Left Panel — Section Navigator (240px)
- bg gray-900, border-right 1px gray-800
- Course title (text-sm, font-weight 600) + thin progress bar (3px) at top
- Section list: completed = green checkmark + gray-400 text, active = gray-800 bg + indigo-500 left-border + gray-50 text, pending = gray-500 text
- Collapses to 48px icon rail

### Center Panel — Lesson Content (fluid, max 680px)
- Breadcrumb: text-xs gray-500
- Heading: 25px font-weight 600
- Body: 16px Georgia, line-height 1.75, gray-300 text
- Code blocks: bg `#111113`, border gray-800, rounded-lg, monospace 14px, syntax highlighted, copy button
- Bottom nav: "← Previous" / "Next →" in text-sm gray-400

### Right Panel — Evidence & Chat (300px)
- border-left 1px gray-800
- Tabs: "Sources" / "Chat" with indigo-500 underline on active
- Sources: cards with title, URL (truncated), tier badge (text-xs, gray-800 bg)
- Chat: full interface replacing floating drawer. Model picker in header. Section-aware context.
- Chat maintains state across tab switches. Notification dot on Chat tab when messages arrive while viewing Sources.
- Collapses to floating edge tab

### Top Bar
- Breadcrumb: Library → Course Title → Current Section
- Reading progress: thin indigo line at viewport top

---

## 6. Auth Pages (Login, Register, Verify)

### Layout — Centered form, no split-screen
- Full page bg gray-950. Centered vertically and horizontally.
- "agent-learn" wordmark centered above form
- Form card: bg gray-900, border gray-800, rounded-lg, padding 32px, width 380px

### Login
- "Sign in" heading + "Welcome back" subtitle
- Email + Password inputs (bg gray-950, border gray-800, height 40px)
- Password has "Show" toggle
- Full-width "Sign in" button (bg indigo-500)
- "Don't have an account? Sign up" link (indigo-400)

### Register
- Same centered layout + Turnstile widget below password

### Verify
- 6-digit OTP boxes (keep auto-advance + paste UX)
- Countdown timer for resend
- Simple checkmark on success, then redirect

### Mobile
- Same centered layout, card goes full-width with margin

---

## 7. Settings Page

Max-width 720px, centered.

### Layout
- "Settings" heading + "Manage your providers and preferences" subtitle
- Horizontal tabs: "AI Provider" (active), "Search", "Account". 1px gray-800 border below.

### AI Provider Tab
- "OpenRouter" subheading
- API Key input (masked) + "Verify" button. Green dot + "Connected" below on success.
- "Model" label + Command combobox showing current model
- "Test Connection" button + latency display

### Search Tab
- Card-based radio group for provider (Tavily, Exa, Brave, Serper, DuckDuckGo) with brand-color accent squares
- API key input + verify button per provider

### Account Tab *(Phase 2 — requires backend endpoints)*
- Email display (read-only)
- "Coming soon" placeholders for password change and account deletion

### Validation
All inputs validate on blur. Green check = success, red message = error.

---

## 8. Pipeline Progress (Course Generation)

### Layout
Replaces wizard inline. Max-width 560px, centered. No navbar.

### Pipeline Visualization
- "Generating course" heading (20px) + topic subtitle (text-sm gray-400)
- 5 horizontal nodes (32px circles) connected by lines:
  - Completed: green-500 fill, white checkmark
  - Active: indigo-500 fill, spinner/dot
  - Pending: gray-800 border, empty
  - Lines: solid green between completed, solid indigo to active, dashed gray-800 between pending
- Labels below each node in text-xs

### Activity Log
- bg gray-900, border gray-800, rounded-lg
- Monospace font, 13px, timestamp-prefixed lines
- Current line in gray-50, past lines in gray-400
- Looks like a CI/CD log (GitHub Actions / Vercel deploy style)

### Footer
- "Section 3 of 8 · ~2 min remaining" centered, text-xs gray-500

---

## Migration Strategy

**Big-bang rewrite** of frontend styles. The app has 8 pages and 6 components — incremental adds complexity without meaningful risk reduction.

### Approach
1. Install shadcn/ui, next-themes, framer-motion
2. Set up theme provider (next-themes) with CSS custom properties
3. Replace globals.css hardcoded dark colors with Tailwind `dark:` classes and CSS variables
4. Rewrite each page using shadcn/ui + new theme
5. All pages converted before merging

### Order of Conversion
1. Layout shell + navbar
2. Auth pages (login, register, verify)
3. Settings page
4. Home / wizard
5. Library / dashboard
6. Learning view
7. Pipeline progress (inline within home page)

---

## Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Component library | shadcn/ui (New York) | Tailwind-native, accessible, customizable |
| Animation | Framer Motion | Composable, performant, `useReducedMotion` built-in |
| Theme toggle | next-themes | Standard for Next.js, SSR-safe, dark default |
| Font loading | next/font (Inter, JetBrains Mono) | Zero layout shift. Georgia is system font. |
| Icons | Lucide React | shadcn/ui default, consistent |
| Styling approach | Tailwind `dark:` classes | Native dark mode, no runtime JS for theming |

## Pages Summary

| Page | Key Changes |
|------|-------------|
| Home | Single form → 3-step wizard with card selectors |
| Library | Card list → 2-col grid with progress bars, tab filters |
| Learning | 2-panel + drawer → 3-panel with tabbed sources/chat |
| Login | Dark centered form → minimal centered card |
| Register | Same as login + Turnstile |
| Verify | Centered OTP boxes |
| Settings | Scroll page → horizontal tabbed interface |
| Pipeline | Step list → horizontal node pipeline + terminal log |
