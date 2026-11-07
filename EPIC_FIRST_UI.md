# EPIC — First-class modular mobile-first UI with phone-matrix E2E

Parent issue: #1 — `[EPIC] First-class modular mobile-first UI with phone-matrix E2E`

## Indexed child issues

| ID | Issue | Scope |
|----|-------|-------|
| UIP-01 | #2 | Skeleton: design tokens, UI primitives, AppShell split |
| UIP-02 | #3 | Features split: jobs, player, artifacts, polling hook |
| UIP-03 | #4 | Visualization composition: Transport, MeasureSeek, state hook |
| UIP-04 | #5 | Polish and accessibility: safe-area, sticky transport, skeletons, focus |
| UIP-05 | #6 | E2E phone matrix: `devices.ts` plus journey spec |

## 1. Goal & constraints

Build a high-quality, maintainable, modular UI without touching backend behavior:
- Mobile-first responsive shell (320px → desktop), 44px targets, no horizontal page
  overflow, visible focus, preserved visualization layer separation
  (`types`/`midi` → `fretting` → `alphaTex` → `AlphaTabView` / `PianoRoll` →
  workspace + shared playback engine).
- Keep existing guarantees: user-gesture audio start, synth disposal on song/job
  change, sustained-note restore on seek, cancellable MIDI load, docs
  (`AGENTS.md`/`README.md`) updated with any behavior change.
- E2E must prove real user flows on representative phones, not just desktop.

## 2. Target modular architecture

```
ui/src/
  app/            # shell: AppShell, tabs, polling provider, toasts
  features/jobs/  # queue form, job list, JobCard, filters, actions
  features/player/# MidiPlayer (+ shared Tone engine wrapper)
  features/artifacts/
  visualization/  # keep existing files; add Transport, MeasureSeek,
                  # PageNavigator, useVisualizationState; playback.ts stays pure
  components/ui/  # Button, Select, Slider, Badge, EmptyState, Notice
  lib/            # api client, format, polling hook
  styles/         # tokens.css, base.css, components.css (replace monolith)
```

Rules:
- One component per file, props-typed, no cross-feature imports except via
  `components/ui` and `lib`.
- Visualization layers stay independent: renderer (`AlphaTabView`) never imports
  fretting/playback engine; `PianoRoll` never imports alphaTab.
- New shared primitives only: `Transport` (play/pause/restart/slider/time)
  reused by Jobs preview and Visualization; `FollowToggle`; `PageNavigator`.

## 3. Mobile-first design system

- Tokens: spacing scale (4/8/12/16/24), type scale (12/14/16/20/28), radius,
  `--accent`, safe-area insets (`env(safe-area-inset-*)`), sticky bottom
  transport on small screens.
- Layout: single-column <640px; bottom-sheet style job actions; tabs stay a
  `role=tablist` with arrow-key nav; notation/roll scroll inside their own
  containers.
- Patterns: skeleton loaders for MIDI fetch, inline `role=status/alert` errors
  with dismiss, offline/reconnect banner reuse, reduced-motion respect.
- Accessibility: every slider/select labelled, 44px targets verified by test,
  focus-visible rings, `aria-pressed/selected`, time text in `role=status`.

## 4. E2E test plan (Playwright, mocked API + generated MIDI)

Keep unit-style specs (fretting, alphaTex import) as-is; add
`tests/e2e-journey.spec.ts` + `tests/devices.ts` using Playwright device
descriptors.

Representative phones (minimum set):

| Profile | Viewport | Why |
|---------|----------|-----|
| Small iPhone (SE 2022) | 375×667, DPR 2 | smallest modern iOS, tightest layout |
| Baseline iPhone (14) | 390×844, DPR 3 | most common iPhone width |
| Large iPhone (14 Pro Max) | 430×932, DPR 3 | large iOS, safe-area + notch |
| Google Pixel 7 | 412×915, DPR 2.6 | canonical Android |
| Samsung Galaxy S22 / A54 class | 360×780 | narrow Android, catches overflow |
| Desktop control | 1280×800 | regression anchor |

Each device runs the journey; assertions per device include
`scrollWidth <= clientWidth` and touch-target sampling (buttons ≥44px).

Journey steps (same script, parameterized by device):
1. **Queue flow**: empty state → fill URL → submit → mocked `POST /jobs` →
   job appears; error toast on 4xx with dismiss.
2. **Polling/progress**: mocked transitions
   `queued → downloading → transcribing → done`; progress updates; filters work.
3. **Artifacts**: audio/MIDI/event links present; `onPlay` scrolls to preview;
   retry/cancel/delete targets work.
4. **Preview playback**: Play → slider advances; Pause holds; seek; Restart.
5. **Visualization sync**: open Visualization tab → transport visible →
   Measure 2 seeks → Next measures pages + seeks → Piano-roll tab shows
   playhead → Play advances shared slider → Pause holds → no overflow.
6. **Robustness**: API failure → reconnect notice; MIDI 404 → error notice, no
   stale green health; long titles/URLs/diagnostics never overflow.

## 5. Phased execution

1. **UIP-01 (P1) — Skeleton**: tokens + `components/ui` + `AppShell` split, no
   visual change; device-parameterized widths.
2. **UIP-02 (P2) — Features split**: `features/jobs`, `features/player`,
   `features/artifacts` extracted from `App.tsx`; polling hook with
   abort/dispose.
3. **UIP-03 (P3) — Visualization composition**: extract `Transport`,
   `MeasureSeek`, `useVisualizationState`; wire shared engine; keep layer rules.
4. **UIP-04 (P4) — Polish + a11y**: safe-area, sticky transport, skeletons,
   focus management, reduced-motion.
5. **UIP-05 (P5) — E2E matrix**: `devices.ts` + `e2e-journey.spec.ts` for the
   5 phones + desktop; overflow/target assertions; docs update.

## 6. Acceptance criteria

- `npm run build`, `npm run lint` (no new warning classes),
  `npx playwright test` all green.
- Journey passes on all 5 phone profiles + desktop; zero `pageerror`; zero
  horizontal overflow; all primary buttons ≥44px.
- No file in `features/*` imports across features; visualization layer diagram
  in `AGENTS.md` still holds.
- `AGENTS.md`/`README.md` updated if user-visible behavior changes.
