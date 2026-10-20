# OnePath.Image performance review

Reviewed deployed revision b6330ed. Downloaded the deployed JavaScript and verified it matches the workspace. Repository source was not changed. Benchmarked the local copy of the exact application with temporary browser instrumentation; no changes were deployed.

## Method and limits

Headless Chromium 154.0.8037.57, four CPU-equivalent quota, 32 GiB memory limit. Chromium reports SwiftShader software rendering, software canvas/rasterization and disabled GPU compositing. These FPS measurements describe this cloud lab and must not be presented as the user's laptop frame rate. Each steady-state window was about four seconds, after finishing finite entry transitions. The samples are short; these are comparative diagnostics, not a Lighthouse score, mobile-device benchmark or memory-leak test. CPU metrics cover renderer main-thread tasks and exclude much raster/compositor work. Frame timing counts RAF opportunities, not independently verified presented display frames. A few comparisons varied across repeats; treat small differences cautiously.

Animation callbacks were timed and rain glyph draws counted. Instrumentation adds some overhead. Initial load used a fresh browser context. Transfer numbers come from a local uncompressed server and identify asset payload, not Internet load time or production CDN compression.

## Baseline measurements

| Configuration | RAF FPS | p95 frame interval ms | Main-thread busy % | Minimum canvas buffers MiB |
|---|---:|---:|---:|---:|
| 1080p rain + CRT | 5.5 | 183.4 | 5.7 | 23.7 |
| 1080p rain, CRT off | 7.7 | 133.4 | 11.7 | 23.7 |
| 1080p offloaded + CRT | 12.7 | 83.4 | 5.4 | 23.7 |
| 1080p Motion off + CRT | 6.2 | 300.0 | 3.1 | 23.7 |
| 1080p Retina rain + CRT | 5.5 | 183.4 | 13.8 | 94.9 |
| 4K rain + CRT | 1.2 | 916.7 | 4.2 | 94.9 |
| 4K offloaded + CRT | 2.5 | 750.0 | 2.6 | 94.9 |
| 4K Motion off + CRT | 1.5 | 766.6 | 3.0 | 94.9 |
| 4K Retina rain + CRT | 1.0 | 1583.3 | 7.7 | 289.1 |
| 1080p rain + CRT repeat | 6.2 | 316.6 | 6.5 | 23.7 |
| 4K rain + CRT repeat | 1.0 | 816.6 | 3.1 | 94.9 |

Canvas memory estimates use width × height × four bytes for the stars, landscape and rain buffers. They exclude decoded images, texture copies, temporary filters, masks and compositor surfaces. 4K DPR 2 means the stars and rain allocate 7680 × 4320 each while landscape backing size is capped at 4096 along the longest dimension. This explains the 289 MiB minimum, not a measured total browser memory footprint.

## Isolation experiments — browser only

| Temporary experiment | RAF FPS | p95 frame interval ms |
|---|---:|---:|
| Motion off baseline | 6.7 | 150.1 |
| Motion off, stop star redraw TEST ONLY | 51.4 | 16.8 |
| Rain no shadows CRT off TEST ONLY | 7.7 | 249.9 |
| OS reduced motion, rain check | 18.5 | 66.7 |
| 1080p omit rain only TEST ONLY | 6.2 | 166.8 |
| 1080p omit aurora only TEST ONLY | 10.0 | 183.4 |
| 1080p omit rain and CRT TEST ONLY | 7.7 | 133.4 |

Offloading removes both the portrait/aurora and the rain, so that comparison cannot be attributed to rain alone. The separate rain-only and aurora-only experiments keep the other effects present. Removing aurora alone improved the software-rendered result substantially; removing rain alone helped much less. CRT removal also improved the baseline. Glow removal reduced measured rain callback time but did not materially improve the overall frame rate in this lab.

## Confirmed problems

1. `drawStars` still clears and paints the full canvas every frame when Motion off or reduced motion freezes the scene. The counter records redraws even though star positions/light do not change. A browser-only experiment skipped this redundant loop: approximately 6.7 to 51.4 RAF FPS with Motion off, CRT retained. A proper fix should paint once and invalidate when needed, rather than skipping the initial paint as the diagnostic did.
2. Rain's frozen check uses only the page's `no-motion` class, not `prefers-reduced-motion`. The OS preference shortens the reveal but does not stop the falling characters. The reduced-motion experiment confirmed continued rain drawing (about 17,000 glyph draws/sec in that sample).
3. Pixel ratio is capped at two but pixel count is not capped for the star/rain canvases. Large/Retina viewports multiply the raster and memory cost. Treat effect-canvas resolution separately from the wallpaper artwork.
4. Aurora animates a large gradient background position through masks with multiple drop shadows. CRT adds a viewport-wide backdrop filter and scanline/phosphor overlays. These are substantial raster/compositor costs even while JS execution stays small.
5. Loading uses full-size artwork for thumbnail previews and eagerly loads the 1.77 MB DeepSeek reference PNG solely to calculate the shared blend framing, even when the original wallpaper/another agent is selected. The fixed height can use precomputed metadata without loading another portrait.

## Loading

Original wallpaper + Gandalf cold resources: 6.06 MiB, excluding the small HTML document. After opening Appearance: 9.84 MiB; additional full-size wallpaper downloads: 3.78 MiB. Desert + Gandalf samples used approximately 6.37 MiB, including the shared cloud texture.

The JS/CSS are modest (about 48 KiB and 37 KiB on disk); images and the font dominate transfer. JavaScript heap was roughly 2–3 MiB in these short measurements, which does not include graphics resources. No application JavaScript exceptions were recorded in the benchmark cases. This does not establish the absence of a long-session memory leak.

## Recommended order

1. Make Motion off render static stars once; honor reduced motion in the rain loop.
2. Add actual small wallpaper thumbnails; replace the DeepSeek framing image load with measured geometry metadata. Consider smaller alpha-capable image encodings and a WOFF2 font.
3. Cap star/rain backing pixels at large viewports and make effect quality adaptive, keeping wallpaper artwork independent.
4. Rework aurora motion toward compositor-friendly transforms/cached textures; avoid repainting large gradients and recomputing glow every frame. Retain an optional lighter CRT mode.
5. Reprofile on a real GPU-equipped laptop using Chrome/Edge DevTools while moving the pointer and switching agents. The steady-state lab tests do not measure peak transition blur cost or the user's interaction latency.
