/* Progressive enhancement for the existing seven-step Django forms. */
(() => {
  const form = document.querySelector('[data-connect-form]');
  if (!form) return;
  const snapshot = () => JSON.stringify([...new FormData(form).entries()].filter(([key]) => key !== 'csrfmiddlewaretoken'));
  const original = snapshot();
  // A rejected POST displays submitted values which have never been saved.
  const isDirty = () => form.dataset.initialDirty === 'true' || snapshot() !== original;
  let leaving = false;
  window.addEventListener('beforeunload', event => {
    if (!leaving && isDirty()) { event.preventDefault(); event.returnValue = ''; }
  });
  form.addEventListener('submit', () => { leaving = true; });
  document.querySelector('[data-exit]')?.addEventListener('click', event => {
    if (isDirty() && !window.confirm(form.dataset.unsaved)) event.preventDefault();
    else leaving = true;
  });
  const focusField = name => {
    const input = [...form.elements].find(element => element.name === name && element.type !== 'hidden');
    if (!input) return false;
    for (let parent = input.parentElement; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
    (input.closest('label') || input).scrollIntoView({block: 'center'});
    input.focus({preventScroll: true});
    return true;
  };
  const errors = form.querySelector('[data-validation-summary]');
  if (errors) {
    const first = errors.querySelector('[data-error-field]');
    if (!first || !focusField(first.dataset.errorField)) errors.focus();
    errors.addEventListener('click', event => {
      const link = event.target.closest('[data-error-field]');
      if (link) { event.preventDefault(); focusField(link.dataset.errorField); }
    });
  }
  function selections() {
    form.querySelectorAll('[data-selection]').forEach(summary => {
      const name = summary.dataset.selection;
      let labels;
      if (name === 'questions') {
        labels = [...form.querySelectorAll('[data-gate-row]')].filter(row => row.querySelector('input:checked')?.value).map(row => row.querySelector('[data-question-label]').textContent.trim());
      } else {
        labels = [...form.querySelectorAll('input:checked')].filter(input => input.name === name).map(input => input.closest('label').textContent.trim());
      }
      summary.querySelector('[data-selection-count]').textContent = '(' + labels.length + ')';
      const list = summary.querySelector('[data-selected-items]');
      list.replaceChildren(...labels.map(label => { const item = document.createElement('li'); item.textContent = label; return item; }));
    });
  }
  form.addEventListener('change', selections);
  selections();
  form.querySelector('[data-interest-search]')?.addEventListener('input', event => {
    const query = event.target.value.trim().toLocaleLowerCase();
    form.querySelectorAll('[data-interest-category]').forEach(category => {
      let matches = 0;
      category.querySelectorAll('label').forEach(label => {
        const match = label.textContent.toLocaleLowerCase().includes(query);
        label.hidden = !match;
        if (match) matches++;
      });
      category.hidden = !matches;
      category.open = Boolean(query && matches);
    });
  });
})();
