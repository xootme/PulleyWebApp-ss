<?php
/**
 * Plugin Name: CCT tool loading spinner
 * Description: Shows a spinner as soon as a visitor clicks a link to one of the
 *   web tools (/tools/..., /pulleys/). The tools run on Google Cloud Run; when a
 *   server has to start, the first load takes a few seconds, and the browser
 *   keeps showing this page until the tool answers — so without this the site
 *   looks frozen. Installed as a must-use plugin (wp-content/mu-plugins/);
 *   source kept in PulleyWebApp-ss/site/cct-tool-loading.php.
 */
add_action('wp_footer', function () { ?>
<style>
#cct-tool-loading{display:none;position:fixed;inset:0;z-index:99999;background:rgba(15,23,42,.72);
  backdrop-filter:blur(2px);align-items:center;justify-content:center;flex-direction:column;
  color:#fff;font-family:Roboto,system-ui,sans-serif;text-align:center}
#cct-tool-loading .cct-spin{width:56px;height:56px;border:6px solid rgba(255,255,255,.25);
  border-top-color:#fff;border-radius:50%;animation:cct-spin 1s linear infinite;margin-bottom:18px}
#cct-tool-loading p{margin:0;font-size:1.2rem;font-weight:500}
#cct-tool-loading small{display:block;margin-top:6px;font-size:.9rem;opacity:.8}
@keyframes cct-spin{to{transform:rotate(360deg)}}
</style>
<div id="cct-tool-loading" role="status" aria-live="polite">
  <div class="cct-spin"></div>
  <p>Opening the tool…<small>The first load can take a few seconds.</small></p>
</div>
<script>
(function () {
  var box = document.getElementById('cct-tool-loading');
  var TOOL = /^\/(tools(\/|$)|pulleys\/?$)/;
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || (a.target && a.target !== '_self') || a.hasAttribute('download')) return;
    var u;
    try { u = new URL(a.href, location.href); } catch (err) { return; }
    if (u.origin !== location.origin || !TOOL.test(u.pathname)) return;
    box.style.display = 'flex';
  });
  // Coming back with the Back button restores this page from cache: hide it.
  window.addEventListener('pageshow', function () { box.style.display = 'none'; });
})();
</script>
<?php });
