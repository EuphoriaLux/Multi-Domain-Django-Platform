(() => {
  const nav = document.querySelector('[data-connect-summary]');
  if (!nav) return;
  let timer, loading = false;
  async function refresh() {
    clearTimeout(timer);
    if (document.hidden || loading) return;
    loading = true;
    try {
      const response = await fetch(nav.dataset.connectSummary, {credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(12000)});
      if (!response.ok || response.redirected) return;
      const data = await response.json();
      for (const key of ['pending_requests', 'unread_chats']) {
        const badge = nav.querySelector('[data-count="' + key + '"]');
        badge.textContent = data[key] ? ' (' + data[key] + ')' : '';
      }
    } catch { /* Keep the last known counts; never invent zeros on failure. */ }
    finally { loading = false; if (!document.hidden) timer = setTimeout(refresh, 30000); }
  }
  document.addEventListener('visibilitychange', () => { clearTimeout(timer); if (!document.hidden) refresh(); });
  timer = setTimeout(refresh, 30000);
})();
