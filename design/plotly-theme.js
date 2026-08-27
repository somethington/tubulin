/* ============================================================
   Tubulin — Plotly theme
   Plotly is themed in the chart config, NOT via the app CSS.
   Pass tubulinPlotlyLayout(theme) as the base `layout` and
   TUBULIN_COLORWAY as `colorway`.
   ============================================================ */

export const TUBULIN_COLORWAY = [
  '#e35d2b', '#2b7a9e', '#7c5cbf', '#1a8a4f', '#c2992b', '#6b7280'
];

export function tubulinPlotlyLayout(theme = 'light') {
  const dark = theme === 'dark';
  const text   = dark ? '#e8ebef' : '#16181d';
  const muted  = dark ? '#9aa3af' : '#5b6470';
  const grid   = dark ? '#262b33' : '#eef1f4';
  const paper  = dark ? '#15181e' : '#ffffff';

  const axis = {
    gridcolor: grid,
    zerolinecolor: grid,
    linecolor: grid,
    tickfont: { family: 'IBM Plex Mono, monospace', size: 11, color: muted },
    titlefont: { family: 'IBM Plex Sans, sans-serif', size: 13, color: muted }
  };

  return {
    colorway: TUBULIN_COLORWAY,
    paper_bgcolor: paper,
    plot_bgcolor: paper,
    font: { family: 'IBM Plex Sans, sans-serif', size: 13, color: text },
    title: { font: { family: 'Space Grotesk, sans-serif', size: 16, color: text } },
    xaxis: { ...axis },
    yaxis: { ...axis },
    legend: { font: { family: 'IBM Plex Sans, sans-serif', size: 12, color: muted } },
    margin: { l: 48, r: 16, t: 40, b: 40 },
    hoverlabel: {
      bgcolor: dark ? '#1a1e25' : '#16181d',
      font: { family: 'IBM Plex Mono, monospace', size: 12, color: '#ffffff' }
    }
  };
}
