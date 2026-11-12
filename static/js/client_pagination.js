(function () {
  const selector = '[data-client-pagination]';
  const states = new WeakMap();

  function initialize(container) {
    const groups = Array.from(container.querySelectorAll('[data-client-pagination-group]'));
    if (!groups.length) groups.push(container);

    const pageSize = Math.max(1, Number.parseInt(container.dataset.pageSize || '20', 10) || 20);
    const controls = container.querySelector('[data-client-pagination-controls]');
    const state = { currentPage: 1, groups, pageSize, render: null };

    function render(resetPage) {
      const visibleGroups = groups.map((group) => {
        const items = Array.from(group.querySelectorAll('[data-client-page-item]'));
        return {
          group,
          items,
          visibleItems: items.filter((item) => item.dataset.clientPageFiltered !== 'true'),
        };
      });
      const total = visibleGroups.length ? visibleGroups[0].visibleItems.length : 0;
      const pageCount = Math.ceil(total / pageSize);

      if (resetPage) state.currentPage = 1;
      state.currentPage = Math.max(1, Math.min(state.currentPage, pageCount || 1));
      container.querySelectorAll('[data-client-pagination-status]').forEach((status) => {
        status.textContent = `Page ${state.currentPage} / ${pageCount || 1}`;
      });
      const firstIndex = (state.currentPage - 1) * pageSize;
      const lastIndex = firstIndex + pageSize;

      visibleGroups.forEach(({ group, items, visibleItems }) => {
        const pageItems = new Set(visibleItems.slice(firstIndex, lastIndex));
        items.forEach((item) => {
          item.style.display = pageItems.has(item) ? '' : 'none';
        });
        group.querySelectorAll('[data-client-page-separator]').forEach((separator) => {
          separator.style.display = 'none';
        });
        pageItems.forEach((item) => {
          let previous = item.previousElementSibling;
          while (previous && previous.hasAttribute('data-client-page-separator')) {
            previous.style.display = '';
            previous = previous.previousElementSibling;
          }
        });
      });

      if (!controls) return;
      controls.replaceChildren();
      controls.hidden = pageCount <= 1;
      controls.style.display = pageCount <= 1 ? 'none' : '';
      if (pageCount <= 1) return;
      controls.style.display = '';

      const nav = document.createElement('nav');
      nav.setAttribute('aria-label', 'Pagination des résultats');
      const list = document.createElement('ul');
      list.className = 'pagination pagination-sm mb-0 justify-content-center align-items-center gap-2';
      if (container.dataset.paginationClass) {
        list.classList.add(...container.dataset.paginationClass.split(/\s+/).filter(Boolean));
      }

      function addButton(label, ariaLabel, page, disabled) {
        const item = document.createElement('li');
        item.className = `page-item${disabled ? ' disabled' : ''}`;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'page-link btn btn-sm btn-outline-secondary d-inline-flex align-items-center gap-1 shadow-sm';
        button.textContent = label;
        button.setAttribute('aria-label', ariaLabel);
        button.disabled = disabled;
        button.addEventListener('click', () => {
          state.currentPage = page;
          render(false);
        });
        item.appendChild(button);
        list.appendChild(item);
      }

      addButton('Précédent', 'Page précédente', state.currentPage - 1, state.currentPage === 1);
      const pageLabel = document.createElement('li');
      pageLabel.className = 'page-item active';
      pageLabel.setAttribute('aria-current', 'page');
      const pageText = document.createElement('span');
      pageText.className = 'page-link';
      pageText.textContent = `Page ${state.currentPage} sur ${pageCount} · ${total} résultats`;
      pageLabel.appendChild(pageText);
      list.appendChild(pageLabel);
      addButton('Suivant', 'Page suivante', state.currentPage + 1, state.currentPage === pageCount);

      nav.appendChild(list);
      controls.appendChild(nav);
      container.dispatchEvent(new CustomEvent('client-pagination-page-changed', {
        bubbles: true,
      }));
    }

    state.render = render;
    states.set(container, state);
    render(false);
  }

  document.querySelectorAll(selector).forEach(initialize);

  window.refreshClientPagination = function (container, resetPage) {
    const state = container && states.get(container);
    if (state) state.render(Boolean(resetPage));
  };
})();
