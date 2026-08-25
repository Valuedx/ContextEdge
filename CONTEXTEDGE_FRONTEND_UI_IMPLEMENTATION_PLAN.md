# ContextEdge Frontend UI Improvement Plan

## 1. Scope

This document defines a frontend-only implementation plan for improving the ContextEdge console. It covers:

- Light and dark theme consistency.
- AutomationEdge brand color alignment.
- Application shell and navigation.
- Shared UI components.
- All existing tabbed workflows.
- Pattern list, detail, and graph experiences.
- Route-by-route improvements.
- Responsive behavior, accessibility, and visual testing.

The plan does not change backend APIs, retrieval behavior, graph logic, permissions, or workflow rules unless a UI requirement explicitly needs an API extension.

## 2. Current UI Gaps

### 2.1 Theme inconsistency

- Light mode is primarily neutral black and white.
- Dark mode uses purple aurora backgrounds and translucent glass surfaces.
- The two modes do not look like variants of the same product.
- Many screens bypass theme tokens with direct `slate`, `indigo`, `purple`, `rose`, and dark-only classes.
- Graph components are permanently configured for dark mode.

### 2.2 Navigation and responsive behavior

- The sidebar presents approximately 25 destinations as one flat list.
- Closely related workflows are not visually grouped.
- The desktop sidebar is hidden on mobile, but the header does not provide a mobile navigation control.
- Active states depend heavily on subtle background and border differences.
- The product identity is rendered as gradient text instead of a stable brand lockup.

### 2.3 Information architecture

- Page headers are rendered as floating glass cards.
- Large detail pages use long stacks of cards without meaningful navigation.
- Pattern detail has graph, resolution steps, summary, signals, and evidence on one page without tabs.
- Playbook, Evidence, Episode, and Source detail pages have enough content to require structured sections.
- Some pages contain nested cards and long instructional paragraphs that reduce scanability.

### 2.4 Tabs

- Seven pages currently use tabs, but their styling and state behavior are inconsistent.
- Some tabs are URL-backed while others reset after navigation or refresh.
- Long tab lists do not have a standard mobile overflow treatment.
- Active states, spacing, icons, and content margins vary by page.
- Settings exposes a Retention tab that currently contains placeholder content.

### 2.5 Patterns and graphs

- The Pattern list graph tab renders a separate 600px React Flow canvas for every loaded Pattern.
- This produces a long, expensive page and weak comparison behavior.
- Pattern list actions mix links, text buttons, colors, and browser confirmation dialogs.
- Pattern graph colors, controls, panels, legends, and empty states are dark-only.
- Floating graph panels can compete for canvas space on narrow screens.
- Color is sometimes the primary way to identify graph entity types.

### 2.6 Shared components

- Buttons and cards contain hardcoded glass and purple shadow effects.
- Table sorting uses clickable containers instead of semantic buttons.
- Empty states are generic and do not explain whether filters, permissions, or missing data caused the result.
- Status colors are maintained as direct utility classes instead of semantic intents.
- Pagination behavior and presentation are duplicated.

## 3. Visual Design Contract

### 3.1 Brand roles

The EvalEdge visual reference should be used for its AutomationEdge palette and hierarchy, but not copied as light-only CSS or inline styles.

| Role | Light theme | Dark theme | Usage |
| --- | --- | --- | --- |
| Application background | `#f4f6f9` | `#0b111b` | Main application canvas |
| Primary surface | `#ffffff` | `#111827` | Panels, tables, dialogs |
| Raised surface | `#f8fafc` | `#172033` | Menus and selected regions |
| Primary text | `#0f172a` | `#f8fafc` | Titles and important values |
| Secondary text | `#475569` | `#aebbd0` | Descriptions and metadata |
| Border | `#e2e8f0` | `#2a3648` | Component separation |
| Brand navy | `#0a1f3d` | `#162c4e` | Shell and brand regions |
| Primary action | `#ff6a1a` | `#ff7d38` | Main commands and emphasis |
| Link/information | `#0093d5` | `#38bdf8` | Links, selection, information |

### 3.2 Color rules

- Orange identifies primary commands and important active emphasis.
- Blue identifies links, selected navigation, and informational states.
- Green is reserved for successful or approved states.
- Amber is reserved for warning, pending, review, or stale states.
- Red is reserved for failure, destructive actions, or critical risk.
- Neutral colors represent inactive, draft, unknown, or unavailable states.
- Status meaning must always include text or an icon, never color alone.
- Remove purple gradients, aurora backgrounds, decorative blur, and glass surfaces.
- Dark mode should use neutral charcoal surfaces with navy brand regions instead of a dark-blue-only palette.

### 3.3 Typography and spacing

- Keep Poppins for the interface and Geist Mono for identifiers and code.
- Remove the duplicate runtime Google Fonts CSS import because `next/font` already loads Poppins.
- Use compact operational typography: 24px page titles, 16px section headings, and 13-14px supporting content.
- Use monospace only for IDs, stable keys, traces, payloads, and code.
- Use 6-8px component radii unless an existing primitive requires otherwise.
- Use 40px as the standard control height and 32px for compact table actions.
- Page sections should be unframed layouts; cards should represent actual repeated objects or tools.

## 4. Implementation Phases

### Phase 1: Theme foundation

#### `frontend/src/app/globals.css`

- Replace current neutral and purple theme variables with semantic AutomationEdge tokens.
- Add surface, text, border, action, link, status, chart, graph, sidebar, and focus-ring roles.
- Remove aurora radial gradients and glass utility classes.
- Add stable page, panel, toolbar, and interactive-state utilities only where Tailwind tokens are insufficient.
- Add `color-scheme: light dark` behavior.
- Ensure every foreground/background combination meets WCAG AA contrast.

#### `frontend/src/app/layout.tsx`

- Continue loading Poppins and Geist Mono through `next/font`.
- Remove dependency on the CSS `@import` font request.
- Configure theme-aware Sonner toast variables.
- Preserve the pre-hydration theme script to prevent appearance flashing.

#### `frontend/src/components/theme-provider.tsx`

- Preserve `light`, `dark`, and `system` choices.
- Default new users to `system` while respecting an existing stored preference.
- Keep resolved theme available to chart and graph components.

#### `frontend/src/components/theme-toggle.tsx`

- Keep the familiar sun, moon, and monitor icons.
- Add a visible tooltip and clearer active selection.
- Verify menu contrast and focus behavior in both themes.

### Phase 2: Brand and application shell

#### New: `frontend/src/components/brand/brand.tsx`

- Create a reusable AutomationEdge and ContextEdge brand lockup.
- Support full, compact, light-surface, and dark-surface variants.
- Use a real approved logo asset rather than gradient text.

#### `frontend/src/app/(auth)/login/page.tsx`

- Replace decorative blurred circles and glass treatment with a restrained branded sign-in screen.
- Use navy brand presence, a solid form surface, orange submit action, and accessible error messaging.
- Keep the form as the first and primary experience.

#### `frontend/src/app/(dashboard)/layout.tsx`

- Replace the glass sidebar and header with solid theme surfaces.
- Use a stable desktop sidebar width and responsive content constraints.
- Reduce excessive page padding at tablet widths.
- Keep page content within the current operational maximum width.

#### `frontend/src/components/shell/sidebar-nav.tsx`

- Group routes under `Workspace`, `Knowledge`, `Governance`, `Operations`, and `Administration`.
- Preserve role-based visibility.
- Add collapsible groups and an optional compact sidebar state.
- Add a navigation search or command menu for the large route set.
- Use blue for active navigation and orange only for urgent counters or review attention.

#### New: `frontend/src/components/shell/mobile-nav.tsx`

- Implement a Sheet-based mobile navigation menu.
- Reuse the same grouped and role-filtered navigation model as the desktop sidebar.
- Close after navigation and restore focus to the menu trigger.
- Ensure all authorized routes remain reachable on mobile.

#### `frontend/src/components/shell/app-header.tsx`

- Add the mobile menu trigger and compact brand lockup.
- Keep theme, notifications, and account actions aligned consistently.
- Add accessible names to all icon-only controls.
- Improve notification empty, unread, loading, and error states.

### Phase 3: Shared UI components

#### `frontend/src/components/common/page-header.tsx`

- Replace the floating glass panel with an unframed page header.
- Support breadcrumbs, title, concise description, status, metadata, and actions.
- Stack actions below the title on narrow screens without clipping.

#### `frontend/src/components/ui/button.tsx`

- Implement semantic `primary`, `secondary`, `outline`, `ghost`, `danger`, and `link` variants.
- Remove hardcoded purple shadows and translucent white outline styling.
- Standardize control heights, icon spacing, loading states, and focus rings.

#### `frontend/src/components/ui/card.tsx`

- Use solid surfaces, subtle borders, restrained shadows, and an 8px maximum radius.
- Remove backdrop blur and glass styling.
- Avoid automatic decorative footer backgrounds.

#### `frontend/src/components/ui/tabs.tsx`

- Add `contained` and `underline` variants.
- Support optional icons and count badges.
- Add horizontal overflow with visible scroll affordance on mobile.
- Preserve stable tab height and prevent trigger text from shrinking or clipping.
- Use a blue active indicator and theme-aware hover/focus states.

#### `frontend/src/components/common/data-table.tsx`

- Replace clickable sort containers with semantic buttons.
- Add sticky headers, compact/default density, stable row IDs, and optional row navigation.
- Support contextual loading, error, filtered-empty, and true-empty states.
- Keep horizontal overflow inside the table container on mobile.
- Move row operations into consistent icon or overflow menus.

#### `frontend/src/components/common/pagination-controls.tsx`

- Make this the single pagination implementation.
- Display page range, total when available, previous/next labels, and accessible icon names.
- Preserve control dimensions when counts change.

#### `frontend/src/components/common/status-badge.tsx`

- Replace direct color mappings with semantic status intents.
- Normalize labels to readable title case.
- Add icons where useful for critical, warning, approved, and running states.
- Create a separate confidence component instead of treating confidence as status.

#### New shared components

- `frontend/src/components/common/empty-state.tsx`: icon, title, explanation, primary recovery action, and optional reset-filter action.
- `frontend/src/components/common/filter-toolbar.tsx`: search, filters, result count, reset, and page actions.
- `frontend/src/components/common/metric-tile.tsx`: compact KPI presentation without card nesting.
- `frontend/src/components/common/detail-tabs.tsx`: consistent URL-backed detail navigation where multiple pages need the same behavior.

### Phase 4: Pattern workflow

#### `frontend/src/app/(dashboard)/patterns/page.tsx`

- Keep two primary views: `List` and `Relationship map`.
- Store the active view in `?view=list` or `?view=graph`.
- Add search, Pattern type, playbook state, confidence range, and active-state filters.
- Show result counts and active filter summaries.
- Display Pattern type and playbook state through semantic badges.
- Replace browser `confirm()` with an accessible destructive confirmation dialog.
- Move secondary Generate, Update, View, and Delete commands into a predictable action menu.
- Remove decorative emoji from the deduplication action.

#### Pattern relationship map behavior

- Do not render one React Flow canvas per Pattern row.
- Render one graph at a time.
- Add a searchable Pattern selector and selected Pattern summary.
- Preserve the selected Pattern using `?pattern_id=`.
- Provide direct links to the selected Pattern and linked Playbook.
- Show graph loading, error, no-selection, and no-relations states separately.

#### `frontend/src/app/(dashboard)/patterns/[id]/page.tsx`

Introduce URL-backed detail tabs:

1. `Overview`: Pattern summary, confidence, freshness, contradiction score, episode count, state, and linked Playbook.
2. `Diagnosis`: Trigger conditions, core entities, observed errors, and root causes.
3. `Resolution`: Resolution steps, Playbook generation/update status, and Playbook link.
4. `Evidence`: Evidence links, link type, weight, search, add-link workflow, and delete confirmation.
5. `Graph`: Full-width interactive Pattern graph.

Additional changes:

- Keep Approve, Generate/Update Playbook, and More actions in a stable header area.
- Disable actions with a visible reason rather than relying only on opacity.
- Use readable Pattern type labels instead of raw underscore values.
- Replace dark-only card classes with semantic surfaces.
- Make evidence linking use a searchable Evidence selector instead of requiring a raw UUID when the API permits it.

#### `frontend/src/components/patterns/pattern-graph.tsx`

- Resolve React Flow `colorMode` from the active application theme.
- Replace the fixed `#020617` canvas background with graph surface tokens.
- Move the node inspector into a collapsible side panel that does not cover important nodes.
- Replace emoji-based node descriptions with Lucide icons.
- Use a compact wrapping legend and allow it to collapse on mobile.
- Provide fit, reset, zoom, and inspector controls with tooltips.
- Ensure node labels wrap or truncate consistently without resizing nodes unexpectedly.
- Use a bounded minimum height and responsive height instead of an unconditional 600px canvas everywhere.
- Stop automatic animation for users who prefer reduced motion.

#### `frontend/src/components/graph/graph-constants.ts`

- Define light and dark node palettes through semantic roles.
- Preserve entity distinction using color, icon, and text labels together.
- Increase edge and label contrast in light mode.
- Provide a neutral fallback for unknown node and edge types.

### Phase 5: Existing tabbed routes

#### `frontend/src/app/(dashboard)/graph-explorer/page.tsx`

- Retain URL-backed `Statistics`, `Subgraph`, `Neighbors`, `Agent Context`, and `Proposals` tabs.
- Add responsive overflow and consistent icons.
- Show counts where API data is available.
- Disable context-specific tabs with an explanation when required node data is missing.
- Make query controls sticky within the Graph Explorer workspace when appropriate.

#### `frontend/src/app/(dashboard)/runtime/page.tsx`

- Rename tabs to `Match sandbox` and `Retrieval feedback`.
- Store the selected tab in the URL.
- Reduce long instructional text and use concise inline field help.
- Separate request input, results, explanation, and raw response into clear regions.

#### `frontend/src/app/(dashboard)/sessions/page.tsx`

- Use `Trace`, `Decisions`, and `Outcome` labels.
- Preserve selected session and tab in URL parameters.
- Keep close-session and outcome commands in a stable action area.
- Add tab counts and distinct empty states.

#### `frontend/src/app/(dashboard)/decisions/page.tsx`

- Use `Decision detail` and `Decision chain` labels.
- Keep detail tabs visually subordinate to the selected table row or drawer.
- Preserve keyboard focus when opening or changing a selected decision.

#### `frontend/src/app/(dashboard)/evaluations/page.tsx`

- Keep `Runs` and `Datasets` with counts.
- Add clear `New dataset` and `Run evaluation` actions.
- Move raw JSON dataset editing into a focused dialog or editor drawer.
- Add structured validation errors with line-level feedback where possible.

#### `frontend/src/app/(dashboard)/settings/page.tsx`

- Use URL-backed `General`, `Workspaces`, `Domains`, and `Users` tabs.
- Remove the empty Retention tab until it is implemented.
- Link users to Policies for existing retention controls.
- Add per-tab actions and contextual empty states.

### Phase 6: Detail-page information architecture

#### Evidence detail

File: `frontend/src/app/(dashboard)/evidence/[id]/page.tsx`

- Add `Overview`, `Conversation`, `Attachments`, and `Policy` tabs.
- Keep source reference and trust/applicability metadata visible near the title.
- Use a readable prose surface for body content and monospace only for raw payloads.
- Preserve conversation counts and hydration state.

#### `frontend/src/components/common/thread-conversation.tsx`

- Replace dark-only styling with theme tokens.
- Improve sender/time/current-message hierarchy.
- Use a timeline treatment without placing a card inside another card.
- Keep hydration state and missing-message warnings visible.

#### Episode detail

File: `frontend/src/app/(dashboard)/episodes/[id]/page.tsx`

- Add `Summary`, `Timeline`, and `Evidence` tabs.
- Keep confidence, review state, and outcome near the page title.
- Make timeline steps scannable and preserve observation editing.

#### Playbook detail

File: `frontend/src/app/(dashboard)/playbooks/[id]/page.tsx`

- Split the current large page into `Overview`, `Steps`, `Evidence`, `Versions`, `Governance`, and `Execution` tabs.
- Preserve active tab in the URL.
- Keep lifecycle state and transition actions in the page header.
- Keep applicability, contradictions, references, and observed practice distinct.
- Show why execution mode is capped or approval is required.

#### Source detail

File: `frontend/src/app/(dashboard)/sources/[id]/page.tsx`

- Add `Overview`, `Inventory`, `Sync history`, `Policies`, and `Configuration` tabs.
- Keep pause, stop, resume, discover, and sync actions in one status-aware command area.
- Present destructive or interrupting actions through confirmation dialogs.

### Phase 7: Route-by-route consistency

#### Knowledge and ingestion

- `overview/page.tsx`: compact KPI row, prioritized review queues, actionable health summaries, and direct drill-down links.
- `sources/page.tsx`: shared filter toolbar, connector status, last activity, and consistent source actions.
- `inventory/[id]/page.tsx`: clearer discovery states, bulk approval toolbar, and stable backfill controls.
- `sync/page.tsx`: status filters, source filter, duration, processed count, and retry/dead-letter actions.
- `evidence/page.tsx`: preserve search in the URL, show active filters, and improve record/title/source hierarchy.
- `episodes/page.tsx`: separate lifecycle state, human review, and AI verdict; keep bulk actions in a selection toolbar.
- `playbooks/page.tsx`: add state/risk/automation/freshness filters and stale guidance indicators.

#### Governance and review

- `review/page.tsx`: retain master-detail layout, improve selected-session visibility, and use a persistent decision footer.
- `suggestions/page.tsx`: use tabs or segmented views for Semantic, Fleet, and Identity queues with counts.
- `contradictions/page.tsx`: improve source comparison and move resolution into a focused drawer/dialog.
- `negative-knowledge/page.tsx`: clarify scope, failure reason, status, and linked Pattern/Playbook context.
- `drift/page.tsx`: distinguish stale, changed, failed-feedback, and expired signals without relying only on color.
- `correlations/page.tsx`: improve source-target comparison and evidence selection.
- `policies/page.tsx`: group policy types, replace raw JSON as the primary editing experience, and retain an advanced JSON mode.
- `execution/page.tsx`: emphasize requested action, risk, evidence, policy gate, and decision deadline.

#### Operations and administration

- `audit/page.tsx`: compact filter toolbar, expandable event details, and copyable resource identifiers.
- `identities/page.tsx`: improve alias display, merge preview, and destructive merge confirmation.
- `admin/pipeline/page.tsx`: standard metric tiles, semantic queue health, responsive pipeline flow, and chart theme tokens.
- `admin/cost/page.tsx`: split budget, usage, cache, and model/task breakdown into navigable sections; improve chart contrast and number formatting.

### Phase 8: File decomposition

Large page files should be split by feature responsibility without changing APIs:

- `playbooks/[id]/page.tsx`: header actions, steps, references, governance, versions, and execution components.
- `review/page.tsx`: queue list, session context, option comparison, approval bar, and modification dialog.
- `admin/cost/page.tsx`: budget editor, usage summary, usage charts, cache summary, and breakdown tables.
- `components/sources/add-source-dialog.tsx`: connector selector plus connector-specific form sections.
- `components/patterns/pattern-graph.tsx`: data mapping, graph canvas, inspector, legend, and empty state.

The split should avoid introducing generic abstractions unless multiple routes genuinely share the behavior.

## 5. Accessibility Requirements

- WCAG AA contrast for normal text, controls, status badges, charts, and graphs.
- Complete keyboard support for navigation, tabs, tables, dialogs, dropdowns, and graph controls.
- Visible focus rings in both themes.
- Accessible names for every icon-only command.
- No information conveyed through color alone.
- Touch targets of at least 40px for primary mobile controls.
- Reduced-motion handling for graph edges, spinners, and transitions.
- Logical heading hierarchy and landmark regions.
- Focus restoration after dialogs, drawers, and mobile navigation close.

## 6. Responsive Requirements

Validate at minimum:

- Mobile: 390 x 844.
- Small tablet: 768 x 1024.
- Desktop: 1024 x 768.
- Wide desktop: 1440 x 900.

Acceptance conditions:

- Every authorized route is reachable at every viewport.
- No page-level horizontal overflow.
- Tabs scroll only within their tab-list region when required.
- Tables scroll inside their own containers.
- Header actions wrap without covering the title or metadata.
- Fixed-format controls and graph canvases maintain stable dimensions.
- Graph panels and legends do not cover critical nodes or controls.
- Text never clips inside buttons, tabs, badges, metric tiles, or cards.

## 7. Testing Plan

### Component tests

- Theme storage and resolved theme behavior.
- Tabs keyboard navigation, active state, and overflow behavior.
- Status intent mapping and readable labels.
- Mobile navigation route and role coverage.
- Data table sorting, selection, row IDs, and empty states.
- Pattern detail tab coverage.
- Pattern graph loading, error, empty, and theme states.

### Visual regression tests

Add Playwright screenshots for both themes and all target viewports on:

- Login.
- Overview.
- Pattern list.
- Pattern relationship map.
- Pattern detail tabs.
- Graph Explorer.
- Evidence detail conversation.
- Playbook detail.
- Review Queue.
- Settings.

### Static design checks

- Prevent new hardcoded page colors outside centralized chart and graph palettes.
- Flag direct dark-only backgrounds in route files.
- Flag missing accessible names on icon-only buttons.
- Check for nested card patterns and page-level overflow.

## 8. Delivery Sequence

### PR 1: Theme and primitives

- Tokens, typography, surfaces, buttons, cards, inputs, statuses, and theme behavior.

### PR 2: Shell and navigation

- Brand component, desktop navigation groups, mobile navigation, header, and login.

### PR 3: Pattern workflow

- Pattern list filters/actions, single relationship map, Pattern detail tabs, and theme-aware graph.

### PR 4: Remaining tabs and detail pages

- Graph Explorer, Runtime, Sessions, Decisions, Evaluations, Settings, Evidence, Episode, Playbook, and Source detail structures.

### PR 5: Remaining routes

- Knowledge, governance, operations, and administration route consistency.

### PR 6: Quality gates

- Component tests, Playwright visual regression, accessibility checks, and hardcoded-color linting.

## 9. Definition of Done

- Light and dark modes use the same semantic brand roles.
- No purple aurora, decorative blur, or glass-heavy application surfaces remain.
- All routes are reachable on mobile and desktop.
- Every tab displays all intended content with responsive overflow and keyboard support.
- Pattern detail covers Overview, Diagnosis, Resolution, Evidence, and Graph.
- Pattern list renders only one relationship graph at a time.
- Graphs are readable and functional in both themes.
- Large detail pages are organized into task-oriented tabs.
- Shared controls use centralized tokens and interaction patterns.
- No text clipping, incoherent overlap, or page-level horizontal overflow occurs at supported viewports.
- Automated component and visual checks protect the finished design.
