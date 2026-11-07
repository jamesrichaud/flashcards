MathJax 3.2.2 (https://www.mathjax.org/), the build that desktop Anki ships: `tex-chtml-full.js`,
the CHTML fonts (`output/chtml/fonts/woff-v2/`) and the accessibility add-ons (`a11y/`), taken from
the `aqt` 26.9.4 wheel (`_aqt/data/web/js/vendor/mathjax/`). Bundled so cards with `\( \)` / `\[ \]`
render without a CDN, also when studying offline. Served at `/static/mathjax/` (flashcards/offline.py).

Copyright The MathJax Consortium. Licensed under the Apache License, Version 2.0 (LICENSE.txt).
