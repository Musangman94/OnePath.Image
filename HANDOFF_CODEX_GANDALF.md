# Handoff to Codex Gandalf — OnePath.Image

Prepared 20 October 2026 (Asia/Kuala_Lumpur).

## Start here

Continue work on **Musangman94/OnePath.Image**, a retro modern ambient home interface intended to become a local agent OS/manager. The current request is to prepare this handoff. **The OS screens are design previews only; no launcher/workspace implementation has been made.** The latest seven-screen set has been delivered, but the user has not yet given feedback on that set. Do not describe it as an approved final design.

Repository: https://github.com/Musangman94/OnePath.Image

Live dashboard: https://musangman94.github.io/OnePath.Image/OnePath.Image/index.html

Source baseline before this handoff: `604a40481da78b49619ede29becfd83b40308984`, pushed to remote `main`. In the current environment the local branch is `work`; pushes have used `git push origin HEAD:main`. Check branch, remote and working tree before continuing.

## User's intent and non-negotiable design constraints

The OS should feel like a custom Android launcher: multiple open spaces that can eventually be customized. Moving between spaces swaps the central content **within the existing page**, rather than loading another HTML document.

- **C is the single space-navigation control.** The user explicitly rejected invented bottom navigation. No bottom dock, page dots, persistent navigation tabs or replacement app sidebar.
- Follow the original Home's simple retro modern language: generous empty space, quiet mint/teal phosphor accents, dotted/italic display type, readable body text, subtle CRT, pixel landscape, fog, stars and constellation.
- Earlier dashboard concepts were rejected as cluttered. Do not bring back grids of cards, multiple visible inspectors, hardware charts, logs and task history all at once.
- Use one clear purpose/action in each space. Open secondary details, settings, logs, history and editors on demand.
- On leaving Home, the large centre clock should fade/shrink to the top left while the new central workspace fades in. Home restores the original clock composition. This transition is requested, **not implemented**.
- Preserve the background layers, agent rail, C control and NAVI across central-space changes. Avoid restarting scene canvases or resetting agent/wallpaper/focus state on navigation.
- Keep navigation and appearance consistent with the actual current source, not with inventions in generated pictures. The existing top-right clock/status also needs a deliberate treatment when implementing the requested top-left compact clock; do not accidentally add redundant clocks.
- Users will enter substantial text. Global shortcuts must stay disabled while typing, including editable fields and IME composition.

## Latest complete preview set

Gallery: https://github.com/Musangman94/OnePath.Image/tree/main/OnePath.Image/web-os-preview-v1

ZIP: https://raw.githubusercontent.com/Musangman94/OnePath.Image/604a40481da78b49619ede29becfd83b40308984/OnePath.Image/downloads/web-os-preview-v1.zip

Local/repository paths, relative to repository root:

| Screen | File | Intended interaction |
| --- | --- | --- |
| Home | `OnePath.Image/web-os-preview-v1/01-home.png` | Original clock, greeting/quote and Focus |
| C menu | `OnePath.Image/web-os-preview-v1/02-c-menu.png` | Transient space selector opened from C |
| Workspace | `OnePath.Image/web-os-preview-v1/03-workspace.png` | Agent conversation, small task status, composer; details on demand |
| Models | `OnePath.Image/web-os-preview-v1/04-models.png` | Current model, load/offload/change, searchable alternatives |
| Skills | `OnePath.Image/web-os-preview-v1/05-skills.png` | Find/create skills and manage assignments |
| Memory | `OnePath.Image/web-os-preview-v1/06-memory.png` | Search scoped memories and edit individual entries |
| Departments | `OnePath.Image/web-os-preview-v1/07-departments.png` | Simple organization, responsibilities and department drill-down |

All seven PNGs are **1586 × 992 native generated images**, not 4K. Minor typography, clock and icon variations in generated images are not specifications. The ZIP was downloaded from GitHub and validated to contain all seven images.

Earlier iterations remain in `launcher-concepts-v1`, `launcher-concepts-v2`, `launcher-concepts-v3`. V1's dense dashboard direction was explicitly rejected. V2 wrongly added bottom navigation. V3 corrected C-only navigation but showed only Workspace; the user requested the entire OS, leading to the latest seven-screen set. Do not treat older previews as equal competing requirements.

## Actual application and source map

The checkout is currently `/workspace/OnePath.Image`; the web root is the nested `/workspace/OnePath.Image/OnePath.Image` directory. Do not confuse repository root with web root.

- `OnePath.Image/index.html`: live page structure; `#sceneEffects`, `.center`, agent rail, C menu, chat/name/appearance panels, NAVI, CRT overlay.
- `OnePath.Image/assets/ui.js`: scene rendering, selection, transitions, controls, persistence, keyboard handling and wallpaper selection.
- `OnePath.Image/assets/ui.css`: layout, theme, scene composition, parallax and effects. Later override rules matter; inspect computed styles before changing earlier declarations.
- `OnePath.Image/assets/navi-frames.js`: NAVI animation frames.
- `OnePath.Image/assets/doto-variable.ttf`: display font.
- `OnePath.Image/assets/constellation-references/`: generated agent portraits/constellations.
- `OnePath.Image/assets/matrix-wallpaper-starless.png`: original landscape source, 1586 × 992.
- `OnePath.Image/assets/constellation-cloud-blend.png`: shared cloud blending overlay.
- `OnePath.Image/wallpaper-review-v5/`: current alternate landscapes and comparisons.
- `OnePath.Image/wallpaper-review-v5/comparisons/original.png`: reference screenshot used for preview generation. Inspect this and the actual page before designing further.

The application is static HTML/CSS/JavaScript. No package manifest or repository test suite was found in the working tree during this handoff. No AGENTS.md was found by the repository file search; check for applicable instructions in the next environment.

### Existing C behaviour

`#controlsToggle` opens `#controlsMenu`. Its current items are Command, Agents, Appearance, Files and Your name. Command and Files currently show preview/not-connected feedback. The generated C selector's proposed spaces are **not wired into this menu yet**. Preserve access to existing controls if extending the menu.

### Backend status

There is no implemented local agent execution backend in the work completed here. Existing chat keeps a draft and explicitly says to connect an agent for replies. Local/Fusion is a connection preference preview. Model offload currently changes the frontend scene/selection; do not claim it unloads a real model. Models, skills, memory stores, department execution, tool runs, approvals and task logs shown in concepts are illustrative future UI.

NAVI has existing external local-product links to `http://127.0.0.1:5050/` and `http://127.0.0.1:8501/`; these are not the proposed internal OS navigation and do not prove those services are running.

## Completed behaviour to preserve

### Landscapes and composition

The Appearance selector persists the chosen landscape: Original, Dune sea, Distant city, Sunken sanctuary, Glacial inlet and Cloudport. Current alternate sources are the five `wallpaper-review-v5` PNGs, not the older generations.

The user wanted landscape content mostly at the bottom with empty/transparent upper areas so independent stars and constellations remain visible. Fog/clouds should slightly overlap the constellation's lower edge. Avoid a large dead gap between portrait and terrain. The original's muted teal-green palette is the reference: previous alternatives were too bright/yellow-green, had overly close foreground subjects (especially a huge desert vehicle) and weak atmospheric integration.

Some sky removal for the original opaque wallpaper happens at runtime; current alternate overlays use transparency. Do not flatten stars, constellation or UI into landscape replacements.

### Motion and cloud blend

- Terrain/fallback and blend use shared framing and parallax endpoints. Current final CSS overrides set terrain scale to 1.12 and travel to 4vw/4vh.
- Blend movement follows the landscape with a **10% slower response**: landscape 320 ms, cloud blend 352 ms. This is response lag, not a smaller final travel distance.
- Blend height uses the **DeepSeek reference framing for every agent**. It must not jump in height during selection. Viewport resizing can recalculate framing.
- Respect Motion off/reduced-motion handling and keep movement enabled normally.

### Typing-safe shortcuts

`isEditable` / `isTyping` guards cover inputs, textareas, selects, contenteditable and textbox/searchbox/combobox roles, plus composition. Both keydown and keyup are guarded. F handling is armed only by a valid shortcut keydown and canceled when focus enters an editor or the window blurs.

Current global shortcuts outside editing: C controls; A agent rail; T Appearance; H header; F short press Focus; F hold about 3 seconds toggles automatic focus; Escape closes overlays. Modified Alt/Ctrl/Meta shortcuts bypass page actions. Preserve this when adding new editors.

### Agent-dependent Matrix rain

- Rain is available only with an agent selected. Offload hides it.
- Rain transitions follow constellation selection with a normal **1-second delay**, **3.2-second fade-in** and **1.6-second fade-out**.
- Interrupted fades snapshot their current visual state and reverse without snapping. Stale animation callbacks are canceled/ignored.
- Fully hidden rain stops drawing; offload remains reversible by selecting an agent.
- Reduced-motion/Motion-off transition timing is shorter and has no delay, but see the outstanding rain-loop issue in the performance section.

### Relevant commits

| Commit | Purpose |
| --- | --- |
| `9df98be` | Persistent landscape selector and transparent wallpaper rendering |
| `38ad058` | Wallpaper scale, palette and atmosphere refinement |
| `6a5844d` | Cloud overlap and wallpaper/agent combination checks |
| `ae285ac` | Landscape/blend motion with 10% slower blend response |
| `7784891` | Disable shortcuts while editing |
| `f8d2a8f` | Shared DeepSeek blend height |
| `479abb4` | Selected-agent-only rain experiment |
| `b6330ed` | Delayed cinematic rain dissolve; latest live-code change |
| `604a404` | Complete seven-screen preview suite; no live-code changes |

The user requested rollback readiness for the rain experiment. Its changes are isolated in the rain commits above. Any rollback must account for the later dependent transition commit and subsequent work; inspect diffs rather than blindly resetting the repository.

## Performance review: known outstanding work

A review of deployed revision `b6330ed` was completed. Subsequent commits only added concepts, so it remains relevant to current live code. No performance fixes were implemented as part of that review.

The supporting report is included at [docs/PERFORMANCE_REVIEW_2026-10-20.md](docs/PERFORMANCE_REVIEW_2026-10-20.md). Original raw samples currently exist only under `/workspace/performance-review/` and may not survive an environment change.

Confirmed priorities:

1. Stars still repaint every frame with Motion off/reduced motion. Render once and invalidate when necessary.
2. Rain's drawing loop checks `no-motion` but not the OS reduced-motion preference. The reveal respects the preference; falling glyphs do not fully respect it.
3. Star/rain canvas pixel budgets are not capped independently of DPR. Large 4K/Retina viewports can allocate substantial graphics buffers.
4. Masked animated aurora gradients, shadows and viewport CRT filtering are expensive. Profile these before assuming rain is the only bottleneck.
5. Appearance downloads full-size wallpaper images for thumbnails. DeepSeek's reference image is also loaded solely for fixed blend geometry; precomputed geometry could remove that transfer.

Measurements used software-rendered Chromium/SwiftShader in a cloud lab. **Do not report those FPS numbers as the user's laptop performance.** Minimum raw buffers for three scene canvases were about 23.7 MiB at 1080p, 94.9 MiB at 4K and 289.1 MiB at 4K DPR 2; these exclude images and compositor surfaces. Reprofile on real GPU hardware when possible.

## Running and validation

From repository root:

```sh
python3 -m http.server 8000 --directory OnePath.Image
```

Open `http://localhost:8000/index.html`. Use HTTP serving rather than relying on file URLs; assets and image/canvas behaviour matter. Check port availability instead of assuming old servers are healthy.

Previous checks used Playwright with `/usr/bin/chromium` where available. Temporary browser scripts and servers are environment-local; inspect availability in a new session. No formal persistent test suite was added for the preview-only changes.

For future implementation, validate central navigation without a document reload, clock transitions, C opening/closing, text-entry shortcut isolation, each agent/offload/reselection, rapid transition reversals, all landscapes, shared cloud height, responsive layouts, Motion off and OS reduced motion. Check console errors and refresh persistence. Preserve existing focus and chat-draft behaviour.

## Suggested continuation

Use the latest preview suite plus the actual Home as visual references. The latest suite is awaiting user feedback; the current task does not itself request implementing it. When the user requests implementation, build a coherent central-space shell first, reuse the existing C interaction, and keep persistent scene/control elements mounted. Represent unconnected backend actions honestly. Extend one space at a time without rebuilding the original home or introducing a framework solely for navigation.

Backend architecture, runtime/API integrations, memory persistence format, tool permissions and department orchestration have not been decided in this conversation. Treat them as open implementation questions, not completed requirements.

User delivery preference: inline images and local/sandbox file links have repeatedly been inaccessible to them. **Provide GitHub-viewable galleries and downloadable ZIPs** for image sets. Do not claim a file was saved to their laptop Downloads folder; this workspace is a separate cloud machine. All published previews and prior functional changes were pushed to GitHub main.

The earlier request to convert all five wallpaper images to 4K remains outstanding. Do not label native ~1586 × 992 images as 4K or assume merely enlarging UI canvases fulfills that request.
