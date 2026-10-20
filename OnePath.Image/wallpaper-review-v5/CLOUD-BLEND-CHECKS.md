# Cloud overlap checks

Historical overlap results from the earlier per-agent height implementation. The current blend height is shared across all agents using DeepSeek as its reference; see [fixed-height checks](FIXED-BLEND-HEIGHT.md).

All five landscapes were tested individually with Gandalf, Hermes, Atomic and DeepSeek at 1440 × 900 (20 combinations). Each landscape was also checked with Gandalf at 390 × 844 and 3840 × 2160 (10 further combinations). All 30 checks passed; no browser exceptions or failing HTTP responses occurred.

The check sampled visible lower-portrait pixels and the cloud texture alpha in their rendered positions, including the cloud opacity and mask. Every combination had cloud coverage over visible portrait pixels. Coverage remained translucent; effective cloud opacity in the sampled overlap stayed below 0.5. CSS stacking was checked so the clouds render in front of the constellation, behind the landscape and UI.

The blend band uses the visible artwork's lower edge rather than the image box. This matters because Atomic and DeepSeek have different empty padding, and because portraits fit differently on narrow screens. The shared cloud texture moves vertically with the artwork bounds; a separate broader haze continues toward the terrain. The original landscape retains its existing cloud rendering.

| Wallpaper | Gandalf | Hermes | Atomic | DeepSeek | Mobile / 4K with Gandalf |
|---|---|---|---|---|---|
| Dune sea | Pass | Pass | Pass | Pass | Pass / Pass |
| Distant city | Pass | Pass | Pass | Pass | Pass / Pass |
| Sunken sanctuary | Pass | Pass | Pass | Pass | Pass / Pass |
| Glacial inlet | Pass | Pass | Pass | Pass | Pass / Pass |
| Cloudport | Pass | Pass | Pass | Pass | Pass / Pass |

[Raw coverage results](blend/checks.json). [Shared transparent cloud texture](blend/constellation-cloud-blend.png).

These checks verify overlap and translucency. Final fog strength and artistic preference remain a visual judgment; the screenshots in the gallery show the final composite.

The overlap checks were repeated after unifying terrain/cloud overscan to 1.12× and adding delayed cloud movement. Cloud alpha was sampled using its actual scaled placement. See [movement checks](MOTION-CHECKS.md).
