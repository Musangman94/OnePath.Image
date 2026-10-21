# Shared DeepSeek blend height

The blend band uses DeepSeek's artwork dimensions and visible lower edge as the shared framing reference for every agent. Switching agents no longer changes the height. Resizing the viewport recalculates the same DeepSeek-based setting for that screen size.

Measured before the change with DeepSeek selected, and verified after the change with all four agents:

| Viewport | Shared blend height |
|---|---:|
| 1440 × 900 | 509.797 px |
| 390 × 844 | 138.062 px |
| 3840 × 2160 | 1359.47 px |

The browser checks covered all four agents with all five landscapes on desktop, plus all four agents on mobile and a 4K viewport. All 62 height assertions passed, including measurements during and after agent transitions and after reloading. Resize checks returned the original DeepSeek-based height at each viewport.

Parallax movement remains enabled: the terrain uses its 320 ms response and the blend uses 352 ms (10% longer), with the same travel and final endpoint. The motion checks were repeated for all six wallpaper choices, plus Motion off and reduced-motion handling.
