# site/

WordPress page content kept under version control. Each `*.wp.html` file is
one Custom HTML block, pasted (or published over SSH with WP-CLI) as the
whole content of its page; `*.preview.html` opens it standalone in a browser.

- `privacy-policy.wp.html` — page ID 3, /privacy-policy/ (rewritten for the
  token model; publish at launch, not before — the live tool is still the
  pre-token version on Render)
