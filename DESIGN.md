# REPLAN design contract

## Source of truth

- Status: Active direction; the light editorial workspace and flat operational illustrations are the current visual baseline, pending responsive and accessibility QA.
- Last refreshed: 2026-09-24.
- Primary surfaces: onboarding, Overview, Changes, Schedule, Scenarios, Actions, History.
- Evidence reviewed: `REPLAN_PROJECT_MASTER.md`, the current `/workspaces`, `/workspaces/new`, and project workspace implementations, `apps/web/app/styles.css`, `assets/brand/README.md`, `apps/web/public/images/workspace/`, the local Lazyweb REPLAN UX/UI research, and the product-owner decision that one internal project operations team operates the project through a shared account.
- This file is the portable implementation brief. The local research folder contains third-party reference screenshots and is not part of the repository.

## Brand

- Personality: calm, precise, trustworthy operational software; editorial in presentation, dense where work requires it.
- Trust signals: distinguish calculated values, inferred interpretation, evidence freshness, and items awaiting human confirmation.
- Avoid: empty AI chat as the entry point, unsupported “optimal” claims, purple gradients, glow, decorative card overload, and copying Excel wholesale into a web table.
- Use the approved **REPLAN** wordmark and compact mark from `apps/web/public/brand/`. `assets/brand/replan/README.md` is authoritative for logo usage. The older `RE:PLAN` spelling in the research and MVP copy is not the approved visual identity.

## Product goals

- Goals: let an internal project operations team understand a changed project schedule, compare feasible responses, and move its approved decision into actions and Excel output from one team workspace.
- Non-goals: general-purpose AI chat, full ERP, automatic approval without evidence and human review, or a multi-company collaboration workspace that requires suppliers and EPC partners to sign in.
- Success signals: a user can identify what changed, its schedule/cost impact, the outstanding confirmation, and the next action without prompting the AI.

### Research-derived product boundary

- The demo is deliberately narrower than generic project-risk management: one equipment package moving through delivery, site readiness, installation, testing/commissioning, and handover.
- The core unit is a change-response decision, not a four-party collaboration network. External suppliers, battery companies, EPCs, and construction partners remain evidence sources or confirmation targets while the internal team owns the decision.
- A believable change does not add the same delay to every task. Arrival, site readiness, qualified people, approvals, and reserved supplier capacity are separate conditions that must be linked before the schedule is recalculated.
- The primary response loop is `external source scan → evidence and applicability candidates → provisional impact analysis → confirm missing applicability/dates → conditional options → execution-condition evidence → approval → new schedule/export → continued monitoring`.
- Supplier messages remain an optional supplementary input. Forecasts, public calendars, notices, synthetic examples and confirmed execution facts have distinct labels. Provisional calculation is permitted before applicability confirmation; approval is not.
- AI may interpret a message, suggest affected tasks, and ask for missing facts. The deterministic scheduler remains responsible for dates, dependencies, capacity, and calculated costs. Unknown quoted cost is shown as unknown, never as zero.

## Personas and jobs

- Primary persona: a cross-functional 프로젝트 운영팀 using one shared team account. Depending on the company this may include a project manager, procurement, engineering, site, or PMO responsibilities; the product is not positioned around one department name.
- User jobs: import the team's working schedule, inspect changes received from external parties, evaluate alternatives against constraints, approve a version as the 프로젝트 운영팀, and follow the team's actions.
- External parties: EPC, equipment vendors, material suppliers, logistics providers, and site teams appear as evidence sources, task owners, and confirmation targets. They are not REPLAN workspace members in the MVP.
- Context: desktop-first, information-dense B2B work used by one internal team, with occasional smaller-screen review.

## Information architecture

- Primary navigation: Overview / Changes / Schedule / Scenarios / Actions / History.
- Workspace ownership: one 프로젝트 운영팀 account owns projects, baselines, decisions, and commits. External organizations are project data, not navigation tenants or invited users.
- First-use flow: Upload → Map → Review → Monitor → Overview.
- Repeated flow: detect change → show impact and evidence → compare scenarios → confirm assumptions → approve → create actions and Excel output.
- First demo flow: create a project, connect a baseline, bind external sources to tasks, inspect a dated external change and its evidence, compare available catalog responses, confirm conditions, approve and export.
- Overview hierarchy: decision needed today, new changes, largest impacts, active actions, then overall project status. Lead with actionable language rather than abstract health scores.

## Design principles

1. Show the decision and its evidence before exposing detailed data or AI process.
2. Put dates, cost, affected tasks, and required confirmations on common comparison axes.
3. Ask humans only for the missing facts or approvals; use structured controls for constraints.
4. Highlight the affected schedule segment and dependencies rather than shrinking an entire Gantt chart into one panel.
5. Keep one operating perspective. Show which external party supplied or must confirm a fact without switching into that party's workspace.
- Tradeoff: use the dark industrial shell only as navigation or for a deliberately focused analysis surface. Keep creation, setup, review, comparison, and approval on light editorial surfaces.

## Visual language

- Color direction: warm ivory `#F4F1EA`, paper gray-green `#EEF0EC`, ink `#172033`, deep navy `#0B1628`, mint `#9FE1BE`, muted amber `#D6B26E`, and restrained coral `#C77F6A`. Cobalt is reserved for links or exceptional focus, not the default workspace accent.
- Typography: strong editorial headings, compact and highly legible operational text; use a Korean-capable system/Pretendard fallback stack.
- Spacing/layout rhythm: generous landing whitespace; tighter workspace spacing with stable navigation and a contextual evidence panel.
- Shape/elevation: thin borders, restrained shadows, roughly 10–12px radii; reserve rounded pills for actual statuses.
- Motion: subtle state transitions only; no animation that delays reading or decisions.
- Imagery/iconography: use the approved flat operational illustration style for orientation, onboarding, empty states, and lightweight status summaries. Prefer human operators, schedules, equipment, and site context in muted navy/mint/ivory tones; avoid robots, brains, magic effects, glossy 3D, and generic AI gradients.
- Illustration hierarchy: at most one focal illustration per major surface. An illustration may explain where the user is or what to do next, but it must not compete with source evidence, scenario metrics, approval blockers, or action lists.
- Illustration truthfulness: illustrated screens, maps, and charts are decorative and must never resemble live calculated evidence closely enough to be mistaken for product data. Mark decorative assets with empty alt text; provide meaningful alt text only when the illustration itself conveys required information.
- Illustration delivery: render responsive assets through `next/image`, prioritize only the first above-the-fold image, lazy-load the rest, and verify the transferred image size at desktop and mobile breakpoints. Remove superseded CSS-drawn scene parts instead of maintaining two illustration systems.

## Components

- Existing components to reuse: the current page's upload, preview, event, scenario, approval, and export interactions; preserve their API behavior while restructuring presentation.
- New/changed components: AppShell with a 프로젝트 운영팀 shared-account context, SideNav, TopContext, decision-first Overview, Impact Timeline, evidence drawer, common-axis Scenario Compare, confirmation checklist, decision receipt, and a small `StateIllustration` wrapper with documented placement and accessibility variants.
- Variants and states: operational versus demo provenance in evidence details, calculated versus inferred versus needs confirmation, no update versus no impact versus failed/stale source, budget/target/approval eligibility. Provenance is not a project-creation choice.
- Ownership: `apps/web/app/styles.css` owns shared tokens until a component structure justifies extraction. Avoid adding a UI framework solely for visual restyling.

## Accessibility

- Target standard: WCAG 2.2 AA as the implementation target; verification is pending.
- Keyboard/focus: all controls and drawer actions must work in logical tab order with visible focus.
- Contrast/readability: status must not depend on color alone; show text labels and units for every cost/date delta.
- Semantics: headings, form labels, error associations, table headers, and live status updates must remain understandable to screen readers. Decorative illustrations use `alt=""` and cannot be the only carrier of status or instructions.
- Motion: respect reduced-motion preferences.

## Responsive behavior

- Desktop: persistent navigation and optional right evidence drawer.
- Tablet: reduce workspace columns and move the drawer below or into an explicit panel.
- Mobile: one primary task at a time; keep comparisons readable by stacking alternatives rather than compressing their metrics. Crop or move illustrations below the primary action rather than shrinking operational text.
- Do not hide required confirmations or approval blockers at any breakpoint.

## Interaction states

- Loading: name the running step (import, analysis, simulation, export), preserve context, and prevent duplicate submission.
- Empty: explain the next action, especially before the first Excel upload or change event.
- Error: distinguish validation, authorization, unavailable source, and failed analysis; never label retrieval failure “no risk.”
- Success: show the resulting version/action/export and a clear next step.
- Disabled: explain which missing confirmation, permission, or constraint prevents approval.
- Slow/offline: retain the last known result and its timestamp; make staleness visible.

## Content voice

- Tone: calm, direct, factual Korean; concise verbs and concrete consequences. English is limited to small eyebrows or familiar technical identifiers and never replaces the primary Korean task label.
- Terminology: use “기준 일정”, “변경”, “영향”, “대응안”, “확인 필요”, “승인”, and “실행 항목” consistently.
- Account terminology: call the operating identity “프로젝트 운영팀 공용 계정”. Do not imply that suppliers, EPC partners, or other external parties have accounts or edit the shared baseline.
- Page-title rule: use only the product name, object, or current record as the primary title, such as “REPLAN”, “데모”, “프로젝트”, “새 프로젝트”, or the actual project name. Do not put a job title, target department, product promise, or directional slogan in an application-page title; place necessary context in supporting copy instead.
- Microcopy: pair numbers with actions and provenance. Do not present inferred statements as calculated facts.

## Implementation constraints

- Framework: Next.js 16, React 19, TypeScript, and repo-native CSS; no new UI dependency is required for the first redesign.
- Keep API contracts and deterministic schedule/cost calculations unchanged during visual work.
- The MVP uses one team-level identity and does not implement invitations, per-person roles, or multi-organization permissions. Preserve evidence and approval timestamps even though individual attribution is out of scope.
- Test/screenshot expectations: verify upload-to-export interaction, keyboard use, responsive layouts, and the distinct loading/empty/error/success states before calling a screen complete.

## Open questions

- [ ] Decide when a real deployment needs individual attribution behind the 프로젝트 운영팀 account without changing the one-team operating model (product/security owner; affects auditability, not the MVP flow).
- [ ] Decide whether public landing and authenticated workspace should share one shell (product owner; affects routing).
- [ ] Validate the palette, typography, and mobile layout against actual users and screenshots (design/engineering; affects visual acceptance).
