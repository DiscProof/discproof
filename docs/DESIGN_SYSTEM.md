# Perfect Audio Copy — Design System

> A **soft dark** desktop UI that matches the **portfolio-tracker** project: quiet surfaces, crisp tabular numbers, and colour used only for meaning.
> Tokens come from portfolio-tracker's `ui/theme/tokens.py` (its `docs/DESIGN_SYSTEM.md` is the reference).
> Implementation: `src/perfect_audio_copy/ui/qml/Pac/Theme.qml` (QML singleton: the single source of truth) and the `Pac` component module. The Qt palette in `ui/backend/theme.py` mirrors the same colours for popups, tooltips and scroll bars.
> Rule: **components never hard-code a colour, font size or spacing value.** They reference `Theme`.

The app draws every control itself on top of Qt Quick Controls' **Basic** style. It does not follow the Plasma colour scheme; that is a deliberate choice, so the app looks the same as portfolio-tracker.

## 1. Principles

1. **The rip result is the content.** CRCs, times, lengths and confidences use JetBrains Mono with tabular figures, so digits line up down columns.
2. **Colour means something.** Neutral greys carry structure. Colour is reserved for meaning, and every coloured status also has text or an icon (never colour alone).
3. **Calm density.** 4 px grid, flat surfaces, hairline borders, no shadows or gradients. Elevation comes from stepping `bg → surface → surface2 → surface3`.

## 2. Colour tokens

| Token | Hex | Use |
|---|---|---|
| `bg` | `#0E1116` | Window, header, footer |
| `surface` | `#151A21` | Cards, track table |
| `surface2` | `#1B222C` | Inputs, table header, dialogs, raised cards |
| `surface3` | `#232B37` | Pressed/hover states, neutral badges, pending sectors |
| `border` / `borderStrong` | `#2A3340` / `#3A4555` | Hairlines / input borders |
| `text` / `textMuted` / `textFaint` | `#E6EDF3` / `#8B97A6` / `#5D6876` | Primary / labels / placeholders and disabled |
| `textOnAccent` | `#0B0E12` | Text on accent fills |

Semantic colours, as they apply to ripping:

| Token | Hex | Meaning here |
|---|---|---|
| `accent` | `#4C9AFF` | Primary action (Rip), focus ring, selection, the track being read |
| `positive` | `#2FBF71` | Accurately ripped (AR ✓), first-pass sector match, exact disc-ID match |
| `warning` | `#E3B341` | Recovered after re-reads, drive not set up, re-reading |
| `negative` | `#F2545B` | Suspicious sectors, errors, Cancel |
| `info` | `#9A8CFF` | Informational: fuzzy match, not in AccurateRip |

Tints: `soft` = 12% over surface (badges), `rowTint` = 6% (whole-row/card highlight), hover 6%, selected 14% plus a 2 px left bar.

## 3. Typography

| Kind (`PLabel.kind`) | Family | px | Weight | Use |
|---|---|---|---|---|
| `display` | Inter | 28 | 600 | (reserved) |
| `kpi` | JetBrains Mono | 20 | 600 | KPI card values |
| `title` | Inter | 15 | 600 | App title, dialog titles, results headline |
| `body` / `strong` | Inter | 13 | 400 / 600 | Default text / table headers |
| `num` | JetBrains Mono | 13 | 400 | Lengths, CRCs, times, offsets (`tnum`) |
| `caption` / `overline` | Inter | 11 | 500 | Secondary lines / uppercase labels and badges (+0.04em) |

Fonts are bundled under `ui/assets/fonts` (SIL OFL). If they cannot be loaded, the system UI and monospace fonts are used.

## 4. Spacing and sizes

Spacing `s1…s8` = 4, 8, 12, 16, 20, 24, 32, 40. Radii 4 (badges) / 6 (inputs, buttons) / 10 (cards, dialogs). Header 56, controls 32 (28 compact), table rows 40, badges 22, progress bars 4, sector-map strips 3. Window padding 16, gap between sections 12, card padding 12/16.

## 5. Components (`ui/qml/Pac`)

- **Card**: `surface`, 1px `border`, radius 10.
- **Badge / StatusChip**: uppercase caption on a soft tint. Track statuses: Pending (neutral), Reading/Testing/Encoding (accent), Re-reading (warning, ↻ icon), AR ✓ N (positive, badge-check icon), AR ✗ (warning), OK (positive), Suspicious (negative, ⚠ icon), Error (solid negative).
- **PButton**: `primary` (accent fill), `secondary` (surface2 + border), `ghost`, `danger` (negative on soft tint). Focus ring 2px accent.
- **Rip split-button**: primary "Rip · Mode", plus a chevron for the mode menu (Secure / Paranoid / Burst, each with a one-line explanation).
- **Sector map**: a 3px strip under each track row. Green = matched on the first comparison, amber = recovered after re-reads, red = suspicious, grey = not read yet. Each pixel shows the worst status of the sectors it covers, and amber/red marks are widened to at least 3 px so a single bad sector stays visible.
- **KpiCard**: overline label, mono value, caption sub-line.
- **PDialog**: `surface2` body, radius 10, title row with close button, footer actions right-aligned.
- **Icons**: Lucide outline icons (ISC licence, `ui/assets/icons`), tinted from tokens by the `image://icon/<name>/<RRGGBB>` provider, at a 1.75 stroke. `eject.svg` is drawn in Lucide's style because Lucide has none.
