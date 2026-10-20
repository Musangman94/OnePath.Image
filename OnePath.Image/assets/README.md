# OnePath.Image local assets

- `doto-variable.ttf` and `Doto-OFL.txt`: existing Doto font and its license text.
- `matrix-wallpaper-clean.png`: existing centered-cover wallpaper used by the page.
- `nous-stamp-light.svg`: local copy of the Nous girl seal reference supplied for this UI, sourced from the official Nous Research site at `https://nousresearch.com/assets/nous-web/nous-stamp-light.svg` (retrieved 2026-10-20). The Hermes sky motif displays it locally with a mint CSS tint; the page makes no runtime request to Nous Research.

The original wallpaper variants remain alongside the active image. No external JavaScript, CSS, font, or image dependency is required at runtime.

## Agent icon sources (2026-10-20)
- Hermes original favicon: https://raw.githubusercontent.com/NousResearch/hermes-agent/main/website/static/img/favicon.svg . Original retained as hermes-agent.svg; transparent portrait geometry extracted to hermes-mark.svg for the borderless picker.
- DeepSeek official whale icon: https://raw.githubusercontent.com/deepseek-ai/awesome-deepseek-integration/main/docs/_logo%20svg/ICON.svg . Retained as deepseek-icon-source.svg and displayed as an accent-colored alpha mask.
- Gandalf wizard is an original code-native SVG.
- Constellation reference art and generation prompts: constellation-references/README.md.
- Local/Fusion are saved UI connection preferences; agent transport is not configured. The chat composer keeps drafts in tab memory and generates no simulated agent response.
