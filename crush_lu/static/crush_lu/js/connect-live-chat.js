/* Incremental Connect chat. Text is inserted as textContent, never HTML. */
(() => {
  const root = document.querySelector('[data-connect-chat]');
  if (!root) return;
  const thread = root.querySelector('[data-thread]');
  const form = root.querySelector('[data-composer]');
  const older = root.querySelector('[data-older]');
  const newButton = root.querySelector('[data-new]');
  const pollStatus = root.querySelector('[data-poll-status]');
  const sendStatus = root.querySelector('[data-send-status]');
  const csrf = root.querySelector('[name=csrfmiddlewaretoken]')?.value;
  const ids = new Set([...thread.querySelectorAll('[data-message-id]')].map(e => +e.dataset.messageId));
  const acknowledged = new Set();
  let cursor = Math.max(0, ...ids), oldest = Math.min(Infinity, ...ids);
  let timer, polling = false, stopped = !form, sending = false, historyLoading = false;
  let pending = null;
  const nearBottom = () => thread.scrollHeight - thread.clientHeight - thread.scrollTop < 70;
  const bottom = () => { thread.scrollTop = thread.scrollHeight; newButton.hidden = true; acknowledge(); };
  const stop = () => {
    stopped = true;
    clearTimeout(timer);
    pollStatus.textContent = root.dataset.closed;
    if (form) form.querySelector('button').disabled = true;
  };
  async function request(url, options = {}) {
    // AbortController also works in WebViews without AbortSignal.timeout.
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timeout = controller ? setTimeout(() => controller.abort(), 12000) : null;
    try {
      const response = await fetch(url, {
        credentials: 'same-origin', cache: 'no-store', ...options,
        headers: {Accept: 'application/json', 'X-CSRFToken': csrf || '', ...options.headers},
        signal: controller?.signal,
      });
      if ([401, 403, 404, 410].includes(response.status) || response.redirected) stop();
      if (!response.ok || response.redirected) throw new Error('request_failed');
      return await response.json();
    } finally {
      if (timeout !== null) clearTimeout(timeout);
    }
  }
  function bubble(message) {
    if (ids.has(message.id)) return null;
    ids.add(message.id);
    oldest = Math.min(oldest, message.id);
    root.querySelector('[data-empty]')?.remove();
    const element = document.createElement('div');
    element.dataset.messageId = message.id;
    element.dataset.mine = String(message.mine);
    element.className = 'max-w-[85%] rounded-2xl px-3.5 py-2 text-sm ' + (message.mine
      ? 'self-end bg-gradient-to-br from-crush-purple to-crush-pink text-white'
      : 'self-start bg-gray-100 dark:bg-slate-700 text-gray-800 dark:text-gray-100');
    const text = document.createElement('p');
    text.className = 'mb-0 whitespace-pre-wrap break-words';
    text.textContent = message.text;
    const time = document.createElement('time');
    time.className = 'block text-xs opacity-80';
    time.dateTime = message.sent_at;
    time.textContent = new Date(message.sent_at).toLocaleString(document.documentElement.lang);
    element.append(text, time);
    return element;
  }
  function append(message) {
    const element = bubble(message);
    if (!element) return false;
    // A send response can arrive before the next poll; keep server order.
    const next = [...thread.querySelectorAll('[data-message-id]')].find(e => +e.dataset.messageId > message.id);
    thread.insertBefore(element, next || null);
    return true;
  }
  async function acknowledge() {
    if (document.hidden || stopped) return;
    const bounds = thread.getBoundingClientRect();
    const visible = [...thread.querySelectorAll('[data-mine="false"]')].filter(e => {
      const rect = e.getBoundingClientRect();
      return rect.bottom > Math.max(bounds.top, 0) && rect.top < Math.min(bounds.bottom, window.innerHeight) && !acknowledged.has(+e.dataset.messageId);
    }).slice(0, 100).map(e => +e.dataset.messageId);
    if (!visible.length) return;
    visible.forEach(id => acknowledged.add(id));
    const body = new URLSearchParams();
    visible.forEach(id => body.append('message_ids', id));
    try { await request(root.dataset.readUrl, {method: 'POST', body}); }
    catch { visible.forEach(id => acknowledged.delete(id)); }
  }
  async function poll() {
    clearTimeout(timer);
    if (stopped || document.hidden || polling) return;
    polling = true;
    let delay = 5000;
    try {
      const data = await request(root.dataset.messagesUrl + '?after=' + cursor);
      if (!data.is_open) stop();
      const stick = nearBottom();
      let added = false;
      for (const message of data.messages) {
        added = append(message) || added;
        cursor = Math.max(cursor, message.id);
      }
      if (added && stick) bottom();
      else if (added) newButton.hidden = false;
      if (data.has_more) delay = 0;
      if (!stopped) pollStatus.textContent = '';
      acknowledge();
    } catch {
      delay = 30000;
      if (!stopped) pollStatus.textContent = root.dataset.retrying;
    } finally {
      polling = false;
      if (!stopped && !document.hidden) timer = setTimeout(poll, delay);
    }
  }
  older.addEventListener('click', async () => {
    if (historyLoading || !Number.isFinite(oldest)) return;
    historyLoading = true;
    older.disabled = true;
    try {
      const data = await request(root.dataset.messagesUrl + '?before=' + oldest);
      if (!data.is_open) stop();
      const height = thread.scrollHeight, top = thread.scrollTop;
      data.messages.forEach(append);
      thread.scrollTop = top + thread.scrollHeight - height;
      older.hidden = !data.has_older;
      acknowledge();
    } catch { pollStatus.textContent = root.dataset.retrying; }
    finally { historyLoading = false; older.disabled = false; }
  });
  form?.addEventListener('submit', async event => {
    event.preventDefault();
    if (sending || stopped || !form.reportValidity()) return;
    const input = form.elements.message;
    const text = input.value;
    if (!pending || pending.text !== text) pending = {text, id: crypto.randomUUID()};
    sending = true;
    form.querySelector('button').disabled = true;
    sendStatus.textContent = root.dataset.sending;
    const body = new FormData(form);
    body.set('client_submission_id', pending.id);
    try {
      const data = await request(form.action, {method: 'POST', body});
      append(data.message);
      if (input.value === text) input.value = '';
      pending = null;
      sendStatus.textContent = '';
      bottom();
      // Do not advance the polling cursor: unseen incoming messages may precede this send.
      poll();
    } catch { sendStatus.textContent = root.dataset.failed; }
    finally { sending = false; form.querySelector('button').disabled = stopped; }
  });
  thread.addEventListener('scroll', () => { if (nearBottom()) newButton.hidden = true; acknowledge(); });
  window.addEventListener('scroll', acknowledge, {passive: true});
  newButton.addEventListener('click', bottom);
  document.addEventListener('visibilitychange', () => {
    clearTimeout(timer);
    if (!document.hidden) { poll(); acknowledge(); }
  });
  const keyboard = () => {
    const viewport = window.visualViewport;
    if (viewport) root.style.setProperty('--connect-keyboard', Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop) + 'px');
  };
  window.visualViewport?.addEventListener('resize', keyboard);
  window.visualViewport?.addEventListener('scroll', keyboard);
  keyboard();
  older.hidden = ids.size < 50;
  bottom();
  poll();
})();
