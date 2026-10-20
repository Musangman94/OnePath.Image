# Wallpaper size and movement checks

The native source dimensions are nearly identical. Pixel size does not set parallax travel: movement is in viewport units after the renderer fits each source image to the viewport.

| File | Width | Height |
|---|---:|---:|
| matrix-wallpaper-starless.png | 1586 | 992 |
| 01-dune-sea.png | 1586 | 992 |
| 02-distant-city.png | 1585 | 992 |
| 03-sunken-sanctuary.png | 1586 | 992 |
| 04-glacial-inlet.png | 1585 | 992 |
| 05-cloudport.png | 1585 | 992 |

Two new wallpaper files match the original exactly; the other three are one pixel narrower. The shared cloud texture is 1672 × 941 and is deliberately fitted to its own atmospheric band. None of these are native 4K exports.

## Cause and correction

Previously, the new landscapes used 1.5vw/1.5vh travel while the original used 4vw/4vh, and the shared atmosphere was static. This caused a different perceived movement between wallpaper choices and separated the moving terrain from the clouds.

All landscapes now share 4vw/4vh travel and 1.12× symmetric overscan. The blend follows the same targets and travel using an easing response of 352 ms, compared with 320 ms for the landscape. Thus its response time is 10% longer; this is a temporal lag, not a permanent 10% reduction in distance. Both layers settle at identical endpoints. The animation continues until both layers have settled.

The blend follows on both axes. Motion off, touch/reduced-motion preferences, and disabling pointer motion reset both offsets together. The cloud's vertical placement still adapts to the visible constellation artwork.

## Results

All six wallpaper choices passed directional motion, measured 1.10 response-time ratio, matching endpoint, and matching scale checks. Motion off and reduced-motion checks passed with both layers at zero offsets and no browser errors.

The 30 cloud-overlap checks were rerun using the actual scaled cloud geometry. All passed: five wallpapers with four agents on desktop, plus all five with Gandalf on mobile and at a 4K viewport size.

[Motion measurements](blend/motion-checks.json) · [Cloud overlap measurements](blend/checks.json)
