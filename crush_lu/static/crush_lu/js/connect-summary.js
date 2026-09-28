(() => {
  const nav = document.querySelector('[data-connect-summary]');
  if (!nav) return;
  // Narrow phones (FR, counts): the strip scrolls sideways, so keep the
  // current tab in view instead of starting every page at scrollLeft 0.
  function revealCurrent() {
    const tab = nav.querySelector('[aria-current="page"]');
    if (!tab) return;
    const box = nav.getBoundingClientRect(), r = tab.getBoundingClientRect();
    if (r.right > box.right) nav.scrollLeft += r.right - box.right;
    else if (r.left < box.left) nav.scrollLeft -= box.left - r.left;
  }
  revealCurrent();
  window.addEventListener('load', revealCurrent);  // web fonts change tab widths
  let timer, loading = false;
  async function refresh() {
    clearTimeout(timer);
    if (document.hidden || loading) return;
    loading = true;
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timeout = controller ? setTimeout(() => controller.abort(), 12000) : null;
    try {
      const response = await fetch(nav.dataset.connectSummary, {credentials: 'same-origin', cache: 'no-store', signal: controller?.signal});
      if (!response.ok || response.redirected) return;
      const data = await response.json();
      for (const key of ['pending_requests', 'unread_chats']) {
        const badge = nav.querySelector('[data-count="' + key + '"]');
        badge.textContent = data[key] ? ' (' + data[key] + ')' : '';
      }
      revealCurrent();
    } catch { /* Keep the last known counts; never invent zeros on failure. */ }
    finally {
      if (timeout !== null) clearTimeout(timeout);
      loading = false;
      if (!document.hidden) timer = setTimeout(refresh, 30000);
    }
  }
  document.addEventListener('visibilitychange', () => { clearTimeout(timer); if (!document.hidden) refresh(); });
  timer = setTimeout(refresh, 30000);
})();
