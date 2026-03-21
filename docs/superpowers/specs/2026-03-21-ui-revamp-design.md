# agent-learn UI Revamp — "Immersive Learning Studio"

**Date:** 2026-03-21
**Status:** Approved
**Direction:** Rich & immersive (Vercel/Raycast energy), light default + dark toggle

---

## 1. Design System & Theme

### Color Palette
- **Primary:** Indigo-to-violet gradient (`indigo-600` → `violet-500`) — CTAs, active states, progress
- **Light mode:** White base, soft blue-gray surfaces (`slate-50`, `slate-100`), deep text (`slate-900`)
- **Dark mode:** Deep navy (`slate-950`), blue-tinted surfaces (`slate-900`, `slate-800`), light text (`slate-100`)
- **Accents:** Emerald (success/completion), Amber (warnings), Rose (errors/danger)
- **Depth:** Glassmorphism cards — `backdrop-blur-xl`, semi-transparent backgrounds (`white/70` light, `slate-800/60` dark)

### Typography
- **UI:** Inter — all interface elements
- **Content:** Georgia / Merriweather serif stack — lesson reading
- **Code:** JetBrains Mono — code blocks

### Component Library
shadcn/ui with "New York" style variant. Key components: Button, Input, Card, Dialog, Tabs, Command (combobox), Dropdown, Sheet, Progress, Tooltip, AlertDialog.

### Motion
Framer Motion for page transitions and micro-interactions. Subtle scale on card hover, smooth reveals on scroll, purposeful state-change animations. No gratuitous motion.

### Shadows & Depth
- **Light mode:** Layered soft shadows (`shadow-sm` → `shadow-xl`) for card elevation
- **Dark mode:** Subtle border glow effects (`ring-1 ring-white/10`) instead of shadows

---

## 2. Layout Shell & Navigation

### App Shell
- Sticky top navbar: logo left, navigation center ("Create", "Library"), user avatar/auth right
- No app-level sidebar — pages own their layouts
- Navbar: transparent with `backdrop-blur` on scroll, gains subtle border on scroll
- Mobile: hamburger → Sheet (shadcn) slide-in from right

### Navbar Details
- Logo + wordmark "agent-learn" (left)
- "Create" / "Library" links (center) — pill-shaped active indicator slides between them
- Settings gear icon + user avatar dropdown (right)
- Dark mode: faint gradient border-bottom glow

### Page Transitions
Framer Motion `AnimatePresence`: subtle fade + slight upward slide (150ms).

---

## 3. Home — Course Creation Wizard

Multi-step wizard replacing the single form.

### Step 1: "What do you want to learn?"
- Hero heading: "What will you master today?"
- Large prominent input, auto-focus, cycling placeholder examples
- Animated gradient border (subtle pulse)
- Trending/suggested topics as clickable pill badges below
- "Next" activates once topic is entered

### Step 2: "Customize your experience"
- Card-based selectors (not dropdowns):
  - Course style: 3 cards with icons (Hands-on, Beginner-friendly, Deep & Technical)
  - Language level: 3 cards (Simple, Intermediate, Advanced)
  - Selected state: indigo gradient border + checkmark
- Optional "Extra instructions" textarea behind collapsible "Advanced" toggle
- Model selection: shadcn Command combobox with search

### Step 3: "Review & Generate"
- Summary card with all selections
- "Generate Course" gradient button with ripple animation on click
- Transitions to pipeline progress inline (no page redirect)

### Progress Indicator
Horizontal stepper (Step 1 · 2 · 3) with indigo fill connecting completed steps.

---

## 4. Library / Dashboard

### Hero Strip
- "Welcome back, [name]" greeting + stats (courses completed, sections learned)
- "Continue learning" card — most recent in-progress course with progress ring + "Resume" CTA
- Glassmorphism card treatment

### Course Grid
- Responsive: 1 col mobile, 2 col tablet, 3 col desktop
- Card design:
  - Gradient top strip (unique color per course from curated set)
  - Title, topic, creation date
  - Circular progress ring (corner)
  - Style/level pills
  - Hover: lift + shadow increase + subtle scale(1.02)
- Empty state: illustration + "Create your first course" CTA

### Filtering
- Tabs: "All", "In Progress", "Completed"
- Sort dropdown: Recent, Alphabetical, Progress
- Delete: hover-reveal trash icon, AlertDialog confirmation

---

## 5. Learning View

Refined 3-panel layout.

### Left Panel — Section Navigator (280px, collapsible)
- Course title + overall progress bar at top
- Section list:
  - Circular number badge (indigo=active, emerald+check=completed, slate=pending)
  - Section title (truncated, tooltip on overflow)
  - Active section: indigo left-border accent + background highlight
- Collapses to 48px icon rail; auto-collapses on smaller screens
- Smooth slide transition

### Center Panel — Lesson Content (fluid, max-width 720px centered)
- Warm serif typography (Georgia/Merriweather)
- Heading hierarchy: subtle indigo accent on h2 left-borders
- Code blocks: dark rounded cards + syntax highlighting + copy button
- Mermaid diagrams: bordered cards with light background
- Blockquotes: indigo left-border + tinted background
- Bottom navigation: "Previous / Next" buttons with section titles

### Right Panel — Evidence & Chat (360px, collapsible)
- shadcn Tabs: "Evidence" | "Chat"
- Evidence tab: source cards with tier badges, confidence meters, expandable details
- Chat tab: full chat interface (replaces floating drawer), model picker in header, section-aware context
- Collapses to floating edge tab
- Mobile: both panels become Sheet drawers (slide up from bottom)

### Top Bar
- Breadcrumb: Library → Course Title → Current Section
- Reading progress bar: thin indigo line at viewport top, fills as you scroll

---

## 6. Auth Pages (Login, Register, Verify)

### Split-Screen Layout
- **Left (60%):** Branded panel — gradient background (indigo → violet, animated drift), wordmark + tagline, floating geometric shapes (CSS/SVG), rotating feature highlight
- **Right (40%):** Auth form — clean white/slate-900 background, shadcn Card

### Login
Email + password inputs, gradient "Sign in" button, "Create account" link.

### Register
Email + password + Turnstile widget (blended with card styling), "Create account" button.

### Verify
6-digit OTP boxes (keep auto-advance + paste UX), countdown timer for resend, animated checkmark on success.

### Mobile
Stacks vertically: branded strip (30vh) top, form below. Branded panel shrinks to logo + gradient.

### Success
Confetti micro-animation after verify, then redirect to dashboard.

---

## 7. Settings Page

### Layout
Page title "Settings" with description. Vertical shadcn Tabs on desktop, horizontal on mobile.

### Tabs

**AI Provider:**
- OpenRouter API key input (show/hide + "Verify" button)
- Model selection: shadcn Command combobox (search, shows model name + context + pricing)
- "Test connection" button (runs quick completion, shows latency)
- Green/red status dot

**Search:**
- Card-based radio group for provider (Tavily, Exa, Brave, Serper, DuckDuckGo) with icons
- API key input for selected provider + verify button
- Green/red status dot

**Account:**
- Email (read-only)
- Change password section
- Danger zone: "Delete account" in rose-bordered card + AlertDialog confirmation

### Validation
All inputs validate on blur. Emerald check = success, rose message = error.

---

## 8. Pipeline Progress (Course Generation)

### Full-Page Takeover
Replaces wizard inline after "Generate" click.

### Pipeline Visualization
- Horizontal connected nodes: Research → Verify → Write → Edit → Complete
- Active node: indigo glow pulse
- Completed node: emerald fill + checkmark
- Pending node: muted
- Animated connecting line fills between nodes

### Live Activity Feed
- Scrolling log below pipeline: "Researching quantum entanglement basics...", "Found 12 sources..."
- Each line fades in with slide-up animation
- Creates feeling of watching AI work in real-time

### Supporting Elements
- Estimated time indicator (based on section count)
- Animated gradient mesh background (slow shift)

### Completion
Pipeline fills to 100%, expanding-ring celebration animation, "View your course" button appears with outline preview.

---

## Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Component library | shadcn/ui (New York) | Tailwind-native, fully customizable, accessible, huge ecosystem |
| Animation | Framer Motion | Best React animation library, composable, performant |
| Theme toggle | next-themes | Standard for Next.js light/dark, SSR-safe |
| Font loading | next/font (Inter) | Zero layout shift, optimal loading |
| Icons | Lucide React | Already shadcn/ui default, consistent |

## Pages Summary

| Page | Key Changes |
|------|-------------|
| Home | Single form → 3-step wizard with card selectors |
| Library | Card list → dashboard with stats, progress rings, filtering |
| Learning | 2-panel + drawer → 3-panel with tabbed evidence/chat |
| Login | Centered form → split-screen with branded panel |
| Register | Centered form → split-screen + Turnstile integration |
| Verify | Centered OTP → split-screen + success animation |
| Settings | Scroll page → vertical tabbed interface |
| Pipeline | Step list → full-page animated visualization |
