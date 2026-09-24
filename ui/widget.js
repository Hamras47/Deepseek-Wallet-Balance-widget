/* ===========================================================================
   DeepSeek Balance widget — renderer.

   The card shows what is left in the wallet and what the last stretch cost.
   All wallet facts arrive from balance.py as a payload; this file only draws
   them.  There are no timers: nothing here changes on its own, because the
   widget only reads the wallet when the user asks it to.

   Without the Python host the page still renders from ui/state.preview.json,
   so the design can be reviewed in any browser.
   =========================================================================== */

'use strict';

const PREVIEW_STATE = 'state.preview.json';
// WebView2 can take several seconds to inject the host API on a cold start, so
// the page waits rather than immediately falling back to the preview payload.
const BRIDGE_WAIT_ATTEMPTS = 30;
const BRIDGE_WAIT_MS = 200;
const BRIDGE_RETRY_MS = 3000;
// A click has to be a click: the host moves the window natively, so a drag leaves
// clientX/clientY almost unchanged and only screenX/screenY give it away.
const CLICK_SLOP_PX = 6;
// Pressing inside this many pixels of the bottom-right corner resizes instead of moving.
// The corner region is tested rather than the small grip element, because a press that
// only just misses the glyph would otherwise start a window move.
const RESIZE_CORNER_PX = 26;

const API_METHODS = ['get_state', 'refresh', 'set_pref', 'hide_window', 'quit_app', 'boot_report'];

const PREF_KEYS = {
  on_top: 'sw-on-top',
  autostart: 'sw-autostart',
  notify: 'sw-notify',
  glass: 'sw-glass',
  draggable: 'sw-draggable',
};

const BADGES = {
  ok: 'BALANCE',
  low: 'OUT OF CREDIT',
  empty: 'NO CREDIT',
  stale: 'LAST KNOWN',
  offline: 'OFFLINE',
  no_key: 'NO API KEY',
  auth: 'KEY REJECTED',
  error: 'PROBLEM',
};

const el = (id) => document.getElementById(id);
const hasBridge = () => Boolean(window.pywebview && window.pywebview.api);

let state = null;
let previewMode = false;
// Where a press started, in SCREEN coordinates: the host moves the window natively,
// so a drag leaves clientX/clientY almost unchanged and only screenX/screenY show it.
let pressed = null;

/* ------------------------------------------------------------- bridge --- */

async function call(name, ...args) {
  if (!hasBridge()) return null;
  try {
    return await window.pywebview.api[name](...args);
  } catch (error) {
    console.error('[widget] bridge call failed:', name, error);
    return null;
  }
}

/** Every interaction and boot stage goes to widget.log (the widget has no console). */
function report(stage, detail = '') {
  call('boot_report', stage, String(detail));
}

async function waitForBridge(attempts = BRIDGE_WAIT_ATTEMPTS, delayMs = BRIDGE_WAIT_MS) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (hasBridge()) return true;
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
  return false;
}

/* ------------------------------------------------------------ render --- */

function render(next) {
  if (!next) return;
  state = next;

  const root = el('widget');
  const card = el('card');
  root.dataset.mode = next.state || 'error';
  // One material, two densities: "clear" over a dark backdrop, "dense" over a
  // bright one (see styles.css).  With the glass switched off there is no native
  // blur to be transparent with, so the card paints itself solid instead.
  const on = next.glass_enabled !== false;
  root.dataset.glass = on ? (next.glass === 'dense' ? 'dense' : 'clear') : 'off';
  root.dataset.refreshing = String(Boolean(next.refreshing));

  el('badge-label').textContent = next.refreshing
    ? 'READING\u2026'
    : BADGES[next.state] || String(next.state || '').toUpperCase();
  el('badge').title = next.tip || '';

  const amount = next.amount || '--';
  el('balance').textContent = amount;
  el('balance').dataset.long = String(amount.length > 7);
  el('currency').innerHTML = next.currency ? String(next.currency) : '&nbsp;';

  el('foot-arrow').textContent = next.refreshing ? '\u22ef' : '\u21bb';
  el('foot-line').textContent = next.foot || '';

  const prefs = next.prefs || {};
  root.dataset.draggable = String(prefs.draggable !== false);
  for (const [key, id] of Object.entries(PREF_KEYS)) {
    const node = el(id);
    if (node) node.setAttribute('aria-checked', String(Boolean(prefs[key])));
  }

  if (!previewMode) {
    report(
      'layout',
      `viewport=${window.innerWidth}x${window.innerHeight} ` +
        `card=${card.offsetWidth}x${card.offsetHeight} state=${next.state} reads=${next.reads}`
    );
  }
}

/* ---------------------------------------------------------- actions --- */

async function refresh() {
  if (!hasBridge()) {
    report('refresh ignored (no host)');
    return;
  }
  if (state && state.refreshing) return;
  report('refresh asked for');
  // The fetch runs on a worker thread in the host; the fresh payload comes back
  // later through __widgetPush, so this call only paints the "reading" state.
  const next = await call('refresh');
  if (next) render(next);
}

async function setPref(key, id) {
  const node = el(id);
  const wanted = node.getAttribute('aria-checked') !== 'true';
  const next = await call('set_pref', key, wanted);
  if (next) render(next);
}

function bindGestures() {
  // The window is frameless, so neither pywebview's drag nor the OS resize loop can
  // move it: the host performs both gestures itself. All this side does is say which
  // gesture started where -- and notice a press that never travelled, which is a click
  // and therefore the request to read the wallet again.
  const INTERACTIVE = 'button, input, select, textarea, a, .switch';
  const card = el('card');
  // The host refuses gestures while the card is locked; checking here as well just
  // saves a round trip and keeps the cursor from looking draggable.
  const locked = () => Boolean(state && state.prefs && state.prefs.draggable === false);

  card.addEventListener('mousedown', (event) => {
    if (event.button !== 0 || event.target.closest(INTERACTIVE)) return;
    if (locked()) return;

    const box = card.getBoundingClientRect();
    const corner =
      box.right - event.clientX <= RESIZE_CORNER_PX &&
      box.bottom - event.clientY <= RESIZE_CORNER_PX;
    if (corner) {
      event.preventDefault();
      report('gesture', 'resize');
      call('begin_resize');
      return;
    }

    pressed = { x: event.screenX, y: event.screenY };
    report('gesture', 'move');
    call('begin_move');
  });

  card.addEventListener('mouseup', (event) => {
    if (event.button !== 0 || !pressed) return;
    const travelled = Math.abs(event.screenX - pressed.x) + Math.abs(event.screenY - pressed.y);
    pressed = null;
    if (travelled > CLICK_SLOP_PX) return; // that was a drag, not a click
    refresh();
  });
}

function bindControls() {
  for (const [key, id] of Object.entries(PREF_KEYS)) {
    el(id).addEventListener('click', () => setPref(key, id));
  }
  el('btn-settings').addEventListener('click', () => {
    const sheet = el('sheet');
    const open = !sheet.hasAttribute('hidden');
    if (open) sheet.setAttribute('hidden', '');
    else sheet.removeAttribute('hidden');
    el('btn-settings').setAttribute('aria-expanded', String(!open));
  });
  el('sheet-close').addEventListener('click', () => {
    el('sheet').setAttribute('hidden', '');
    el('btn-settings').setAttribute('aria-expanded', 'false');
  });
  el('btn-hide').addEventListener('click', () => call('hide_window'));
  el('btn-quit').addEventListener('click', () => call('quit_app'));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') el('sheet').setAttribute('hidden', '');
  });
}

/* ------------------------------------------------------------- boot --- */

async function bootFromBridge() {
  if (!hasBridge()) return false;
  const next = await call('get_state');
  if (!next) return false;
  previewMode = false;
  render(next);
  return true;
}

async function bootFromPreview() {
  try {
    const response = await fetch(PREVIEW_STATE, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    previewMode = true;
    render(await response.json());
    report('preview payload loaded');
  } catch (error) {
    report('preview payload failed', String(error));
    el('foot-line').textContent = 'no host, no preview payload';
  }
}

(async function boot() {
  bindGestures();
  bindControls();
  report('page ready');

  if (new URLSearchParams(window.location.search).has('host')) {
    const ready = await waitForBridge();
    report('bridge wait finished', String(ready));
    if (ready && (await bootFromBridge())) {
      report('bridge connected');
      return;
    }
    // The bridge can appear after the wait (a cold WebView2 start), so keep trying.
    setInterval(async () => {
      if (!previewMode) return;
      if (await bootFromBridge()) report('bridge connected late');
    }, BRIDGE_RETRY_MS);
  }
  await bootFromPreview();
})();

// The host pushes a payload after every read, and on every preference change.
window.__widgetPush = (payload) => render(payload);
