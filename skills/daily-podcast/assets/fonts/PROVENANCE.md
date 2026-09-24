# Video fonts

Used only by `video_frames.py` (the episode video). Both families are under the SIL
Open Font License 1.1; the license texts sit beside the files.

| File | Family | Source |
| --- | --- | --- |
| `space-grotesk-{400,500,700}.ttf` | Space Grotesk | `@fontsource/space-grotesk` 5.3.0, `latin` subset, WOFF2 → TTF |
| `jetbrains-mono-{400,500,700}.ttf` | JetBrains Mono | `@fontsource/jetbrains-mono` 5.3.0, `latin` subset, WOFF2 → TTF |

Converted with fontTools (`TTFont(woff2).flavor = None`) — no glyph changes. The
Latin subset is why `video_frames.clean_text` folds anything outside it to its
closest Latin form before drawing.
