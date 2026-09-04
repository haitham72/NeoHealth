# ReguLense Onboarding Redesign Handoff

> **Superseded 2026-09-04 (same day, later in the session).** The vertical-scroll
> page this doc specifies *was* built (`OnboardingWelcome.tsx`, `tokens.css`'s
> `.onboarding-*` block) and then deliberately replaced again, per direct user
> instruction, with a **compact blocking modal wizard**: a dimmed backdrop, a
> centered two-panel dialog (static dark brand panel on the left with the headline
> + tagline + authority marks; a stepped, horizontally-sliding content deck on the
> right — 3 steps, each with an eyebrow, a step counter like `01 / 03`, a title,
> body copy, and one of the case-file visuals below), Skip always visible, and
> click-backdrop/Escape/`X` to dismiss. Session-persisted the same way (see
> below). The "case file" visual language (ink stamps for jurisdiction, an
> animated SVG confidence gauge, a highlighted doc-leaf citation mockup — all in
> the app's real `--fhir-blue`, on the OS system-font stack so Mac/iOS visitors
> render real SF Pro) carried over from the vertical version; only the shell
> changed from a full-page takeover to a modal. The reason: the user pointed to an
> existing (uncommitted, Vercel-deployed-only) two-panel modal design as the
> intended interaction and asked for something better in the same spirit — see
> `frontend/src/components/OnboardingWelcome.tsx` for the current implementation.
> Everything below about content/copy/product-truths is still accurate; only the
> "vertical scroll page" interaction model is stale.

## Objective

Redesign the onboarding experience as a premium SaaS-style, scroll-driven product
introduction. The current implementation is a horizontal swipe/slide deck. That is
not the intended interaction.

The desired interaction is:

- A vertical page that scrolls from top to bottom.
- Each section enters smoothly as the user scrolls down.
- The first section is a strong SaaS-style product welcome/hero section.
- Later sections explain the real ReguLense product capabilities.
- The experience should feel polished and editorial, inspired by the pacing and
  restraint of Apple product pages, but not copied from Apple and not a product
  clone.
- It must work on desktop and mobile.
- Respect `prefers-reduced-motion`.
- No deployment work yet. Iterate locally first.

## Current Branch And Deployment State

- Current branch: `feat/vercel-phase-1`
- Do not deploy to Vercel during redesign iterations.
- The Vercel project exists, but its deployment is not the development target.
- The public backend is still running an older Render build where `/ready` returns
  `404`; local backend has the new route and is the correct environment for testing.

## Local Development

Frontend:

```text
http://localhost:5173
```

Backend:

```text
http://localhost:8000
```

Local frontend config must keep this value blank:

```text
VITE_API_URL=""
```

That makes Vite proxy API calls to the local backend. Do not change it to the
Render URL during local visual work.

The local Postgres container is normally named `temporal_note-db` and exposes
port `5433`. The backend readiness check is:

```text
GET http://localhost:8000/ready
```

If local servers are not running:

```powershell
# Terminal 1, from backend/
python -m app.main

# Terminal 2, from frontend/
npm run dev
```

If a previous server process was started by another tool, check ports `5173` and
`8000` before starting another copy.

## Relevant Files

### Primary redesign file

`frontend/src/components/OnboardingWelcome.tsx`

This currently contains:

- Four content items, markers `00` through `03`.
- A first welcome/hero item.
- Product story copy about jurisdiction, confidence, and citations.
- Small CSS preview components for search, authority mapping, confidence, and PDF
  citation highlighting.
- Session storage persistence through `ONBOARDING_STORAGE_KEY`.
- Skip, Back, Continue, keyboard arrows, Escape, and touch swipe handlers.
- A horizontal track using `translateX(...)`.

The horizontal track and swipe behavior are the parts to replace. Do not preserve
the swipe interaction just because it already exists.

### Styling

`frontend/src/tokens.css`

The onboarding-specific CSS is currently near the bottom of this file. It uses
classes beginning with `onboarding-`, including:

- `.onboarding-screen`
- `.onboarding-header`
- `.onboarding-viewport`
- `.onboarding-track`
- `.onboarding-slide`
- `.onboarding-preview*`
- `.onboarding-progress`
- `.onboarding-orb*`

Replace or simplify these styles as needed. Keep the existing global design tokens
and preserve the rest of the application styles.

### App integration

`frontend/src/App.tsx`

The app renders onboarding over the chat shell:

```tsx
{showOnboarding && <OnboardingWelcome onComplete={() => setShowOnboarding(false)} />}
```

`showOnboarding` is based on `ONBOARDING_STORAGE_KEY`. Completing or skipping the
onboarding writes `complete` to `sessionStorage`.

The onboarding must remain an overlay/full-screen experience. Do not gate or remove
the chat itself based on backend readiness.

### Readiness and queue behavior

These files are already implemented and should not be redesigned as part of the
visual handoff:

- `frontend/src/hooks/useBackendReady.ts`
- `frontend/src/api/url.ts`
- `frontend/src/api/client.ts`
- `frontend/src/components/ThinkingSteps.tsx`
- `frontend/src/components/AssistantMessage.tsx`

The first user message is queued while readiness is false. It appears immediately,
then shows:

```text
Starting the server — Ns
```

The request is sent only after `/ready` returns HTTP 200. This behavior is separate
from the onboarding animation and must continue working.

## Product Truths For Copy

Only describe behavior that exists in the codebase:

- Hybrid retrieval over UAE health regulation.
- DHA, DoH, and MOHAP authorities.
- Jurisdiction filtering.
- Version awareness and superseded regulation handling.
- Tiered confidence.
- Citation-or-abstain behavior.
- Exact cited passages with PDF highlights.
- On-demand version comparison exists, but it is not part of this redesign unless
  the new onboarding explicitly explains an already-existing feature.

Do not imply that the product is an autonomous compliance decision-maker, legal
advisor, or official government service.

## Desired Page Structure

Use this as a starting direction, not a rigid wireframe.

### Section 00: Product hero

- Strong statement such as “The rule, without the noise.” or a better original
  alternative.
- Clear supporting statement for healthcare/compliance teams.
- A real-looking but clearly illustrative search workspace preview.
- Small authority markers for DHA, DoH, and MOHAP.
- Primary action: `Start exploring` or equivalent.
- Secondary `Skip introduction` action.

### Section 01: Context and jurisdiction

- Show why authority context matters.
- Animate DHA, DoH, and MOHAP source cards or a restrained authority map into view.
- Avoid generic dashboard decoration. Every visual should explain the product.

### Section 02: Confidence and abstention

- Explain that the system does not answer every question with equal certainty.
- Animate an evidence signal or confidence meter into view.
- Make abstention feel like a safety property, not a failure.

### Section 03: Source trail

- Show a regulation passage and precise citation highlight.
- Explain that the user can open the cited passage in the PDF.
- End with a clear `Start asking` action.

## Interaction Requirements

- Vertical scroll is the primary interaction.
- Do not require horizontal swipe.
- Avoid trapping the user in a modal carousel.
- The page should be naturally scrollable with a mouse wheel, trackpad, touch
  scrolling, and keyboard.
- `Skip introduction` should work from the top and remain accessible.
- The final CTA should call `onComplete` and write the completion state.
- Keep session persistence. A returning visitor in the same session should not see
  the onboarding again.
- The simplest robust approach is a normal vertical document with sections and
  `IntersectionObserver`-driven `is-visible` classes. CSS transitions are preferred
  over a heavy animation dependency.
- A fixed progress indicator is acceptable if it does not obscure content or make
  the page feel like a carousel.

## Animation Direction

Aim for a calm product-film rhythm:

- Section content fades in while moving upward a short distance.
- Visual previews can use staggered child transitions.
- Large type should remain legible and stable, not bounce or rapidly scale.
- Use restrained duration/easing, approximately `500ms` to `900ms` for section
  entrances.
- Add no autoplay audio, video, or external assets.
- Under `prefers-reduced-motion: reduce`, content should appear immediately and
  scrolling should remain natural.

## Known Current Problems

The current implementation should be treated as a prototype, not as the target:

- It is a horizontal `translateX` deck.
- It exposes swipe handlers even though the desired experience is vertical scroll.
- It uses a full-screen overlay but visually behaves like a compact carousel.
- The hero and feature visuals need stronger hierarchy and more deliberate pacing.
- The progress bars currently communicate slide position, which reinforces the
  carousel interaction.

## Verification

Run from `frontend/` after changes:

```text
npm run build
npm run lint
npm run test
```

For visual review:

1. Open `http://localhost:5173`.
2. Clear the onboarding key in DevTools Console:

```js
sessionStorage.removeItem("regulense-onboarding-v2")
location.reload()
```

3. Test desktop wheel/trackpad scrolling.
4. Test mobile emulation and touch scrolling.
5. Test `Skip introduction`.
6. Complete the final CTA and reload to confirm onboarding stays dismissed.
7. Test keyboard navigation and `prefers-reduced-motion`.
8. Test that asking the existing sample question still displays the queued
   `Starting the server — Ns` state when the backend is not yet ready.

Do not run `vercel deploy`, create aliases, or modify Vercel project settings during
this redesign phase.

## Out Of Scope

- No new backend endpoints.
- No changes to retrieval, citations, or answer generation.
- No Phase 3 in-chat feature popup.
- No new queue architecture beyond preserving the current first-message queue.
- No Vercel deployment or production configuration changes.
- No dependency additions unless clearly justified and approved.
