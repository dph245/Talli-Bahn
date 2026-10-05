'use strict';
try {
  const light = JSON.parse(localStorage.getItem('talli:theme')) === 'light';
  document.documentElement.dataset.theme = light ? 'light' : 'dark';
  document.querySelector('meta[name="theme-color"]').content = light ? '#f3f4f1' : '#121820';
} catch { /* The page also works without browser storage or JavaScript. */ }
