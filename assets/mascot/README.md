# Sample character pack — "Neon Mascot"

This folder is the **sample starter character pack** for the Kokertech Ai Hub
desktop companion. It is auto-loaded the first time the Hub starts (until you pick a
different pack or choose **Use built-in neon mascot**), and it doubles as a reference
implementation of the pack format.

| File | State | Content |
| --- | --- | --- |
| `idle.png` | `idle` | Cyan/white neon line-art mascot, centered, transparent background |
| `working.png` | `working` | Cyan-dominant variant for the working state |
| `attention.png` | `attention` | Red/yellow accent variant for errors and focus requests |
| `success.png` | `success` | Green/white accent variant for completed actions |

All four are 1024×1024 RGBA stills (no animation) — animated packs use GIF or animated
WebP files with the same stems. Format details, fallback rules, and state triggers are
documented in [`docs/character_pack_format.md`](../../docs/character_pack_format.md).

To try it: run the app, open **Character artwork → Choose character pack folder…**,
and select this folder (it is also the default when no preference is stored).

To build your own pack: copy this folder, replace the images (any Qt-supported
extension), and keep the `idle` / `working` / `attention` / `success` stems — or ship a
single shared `mascot.<ext>` / `animation.<ext>` / `idle.<ext>` image for all states.
