"use strict";

// 零构建内联 SVG 图标集（ADR 0008：不引入图标库）。
// 每个图标 16x16 viewBox，currentColor 着色。
(function () {
  const PATHS = {
    browser: "M2 3h12a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H2a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm1 2a1 1 0 1 0 0 2 1 1 0 0 0 0-2zm3 0a1 1 0 1 0 0 2 1 1 0 0 0 0-2zM3 8h10v1H3z",
    data: "M3 2h8l4 4v8a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zm7 1v3h3M5 9h6M5 11h6",
    transform: "M8 2v3m0 0a4 4 0 1 0 4 4M8 5L5 2m3 3l3-3",
    desktop: "M2 3h12a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H2a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm3 11h6m-3-3v3",
    window: "M2 3h12a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H2a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm-1 2h14M3 4.5h.01M4.5 4.5h.01",
    branch: "M4 3v4a2 2 0 0 0 2 2h5M4 3a1.5 1.5 0 1 0 0 .01M11 9l3 2.5L11 14",
    loop: "M13 3a5.5 5.5 0 1 0 .7 5.5M13 3v3h-3",
    shield: "M8 1l6 2v5c0 4-2.5 6.5-6 8-3.5-1.5-6-4-6-8V3z",
    back: "M13 3H5a3 3 0 0 0-3 3v1m11 4 3-3-3-3M3 13h8",
    play: "M4 2l9 6-9 6z",
  };
  window.RPA_ICONS = {
    get(name) {
      const path = PATHS[name] || PATHS.data;
      return `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="${path}"/></svg>`;
    },
  };
})();
