# Tubulin — Design Kit

A dependency-free visual system for the Tubulin runtime (React 18 + TS frontend
talking to the Rust runtime over WebSocket). Everything is driven by CSS custom
properties, so the whole app restyles by editing tokens — light and dark.

> Replaces the original Streamlit-clone look. New identity: geometric-technical
> type, a warm orange accent, and a stepped mark evoking tubulin dimers assembling
> into a microtubule — uniform parts, self-assembly, scale.

---

## Contents

| File | What it is |
|------|------------|
| `index.html` | Launcher — open this first |
| `Tubulin Design System.dc.html` | Tokens, type scale, component states, all 14 widgets |
| `Tubulin App.dc.html` | Full app mockup (sidebar + stacked blocks) with a live light↔dark toggle |
| `Tubulin Landing.dc.html` | Marketing page for the framework |
| `tokens.css` | The actual CSS variables — paste into / import from `styles.css` |
| `plotly-theme.js` | Plotly layout + colorway (Plotly is themed in chart config, not CSS) |
| `support.js` | Runtime for the `.dc.html` files — keep it beside them |

Open the `.dc.html` files directly in a browser, or `npx serve` this folder.

---

## Wiring it into your app

### 1. Tokens → `styles.css`
`tokens.css` defines every variable at `:root` with a `[data-theme="dark"]`
override. Either `@import 'tokens.css';` at the top of `styles.css` or paste the
two blocks in. Toggle dark mode by setting `data-theme="dark"` on `<html>` or
`<body>`.

```css
.sidebar   { background: var(--sidebar); border-right: 1px solid var(--border); }
.block     { background: var(--bg); color: var(--text); }
.btn-primary { background: var(--primary); color: var(--primary-contrast); }
.btn-primary:hover { background: var(--primary-strong); }
```

### 2. Token map (old → new)
The original Streamlit-derived names map cleanly:

- `--bg` `#ffffff` → unchanged (now also has a dark value)
- `--sidebar` `#f0f2f6` → `#f4f6f8`
- `--text` `#31333f` → `#16181d`
- `--muted` `#808495` → `#5b6470` (added `--faint` for a third tier)
- `--primary` `#ff4b4b` → `#e35d2b` (brightens to `#f1703c` in dark)

New tokens added: `--app`, `--surface-2`, `--border-soft`, `--border-strong`,
`--track`, `--primary-soft`, `--primary-strong`, status colors, `--log-bg`,
font/radius/spacing/shadow scales, and `--viz-1..6`.

### 3. Type
Source Sans 3 → a three-family system, all on Google Fonts (`@import` is in
`tokens.css`):

- **Space Grotesk** — display & headers (`--font-display`)
- **IBM Plex Sans** — UI & body (`--font-sans`)
- **IBM Plex Mono** — values, data, code, logs (`--font-mono`)

### 4. Plotly
```js
import Plotly from 'plotly.js-dist-min';
import { tubulinPlotlyLayout, TUBULIN_COLORWAY } from './plotly-theme.js';

Plotly.newPlot(el, traces, {
  ...tubulinPlotlyLayout(isDark ? 'dark' : 'light'),
}, { displayModeBar: false });
```

---

## Widget coverage (`src/widgets/`)

**Inputs** — Button · Checkbox · Slider · NumberInput · Select · FileUpload
**Outputs** — Text · Header · TablePreview · ImagePlot · PlotlyChart · Gallery · Download · JobStatus

Each appears restyled in the Design System gallery and again in real context in
the App mockup.

---

## Notes
- The `.dc.html` files are streaming design components; `support.js` must stay in
  the same folder.
- `ImagePlot` / `Gallery` use striped placeholders where your real SVG/images go.
- Accent is `#e35d2b` (calmed from the requested `#ea580c`) — change `--primary`
  to retune the entire app.
