/**
 * Speemail keyboard navigation
 *
 * Global shortcuts:
 *   c          Compose new email
 *   g i        Go to Inbox
 *   g q        Go to Queue (approval)
 *   g h        Go to History
 *   g s        Go to Settings
 *   ?          Toggle shortcut help
 *   Escape     Close modal / deselect
 *
 * Inbox / Queue list:
 *   j / ↓      Next message
 *   k / ↑      Prev message
 *   Enter / o  Open selected message
 *   u          Mark unread
 *   #          Trash (inbox only)
 *
 * Message detail (inbox):
 *   r          Reply
 *   f          Forward
 *   u          Mark unread & back to list
 *   Escape     Back to list
 *
 * Approval queue cards:
 *   a          Approve & send focused card
 *   e          Edit focused card
 *   x          Reject focused card
 */

(function () {
  'use strict';

  let gPrefix = false; // tracking 'g' chord
  let gTimer = null;
  let focusedIndex = -1;

  // ── Helpers ────────────────────────────────────────────────────────────

  function isTyping() {
    const tag = document.activeElement?.tagName?.toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' ||
      document.activeElement?.isContentEditable;
  }

  function isModalOpen() {
    return !!document.querySelector('dialog[open]');
  }

  function getFocusableItems() {
    // Works for both inbox rows and queue cards
    return Array.from(
      document.querySelectorAll('.message-row, .email-card:not(.card-sent):not(.card-rejected)')
    );
  }

  function setFocus(index) {
    const items = getFocusableItems();
    if (!items.length) return;
    focusedIndex = Math.max(0, Math.min(index, items.length - 1));
    items.forEach((el, i) => el.classList.toggle('keyboard-focused', i === focusedIndex));
    items[focusedIndex]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function clearFocus() {
    getFocusableItems().forEach(el => el.classList.remove('keyboard-focused'));
    focusedIndex = -1;
  }

  function openFocused() {
    const items = getFocusableItems();
    if (focusedIndex < 0 || focusedIndex >= items.length) return;
    const item = items[focusedIndex];
    // Inbox row: click the row to load detail
    if (item.classList.contains('message-row')) {
      item.click();
    }
  }

  function toast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => el.remove(), 3000);
  }

  // ── Compose modal ──────────────────────────────────────────────────────

  function openCompose(prefill = {}) {
    const existing = document.getElementById('compose-modal');
    if (existing) { existing.showModal(); return; }

    // Inject modal into #modal-container via HTMX-style fetch
    fetch('/partials/compose?' + new URLSearchParams(prefill))
      .then(r => r.text())
      .then(html => {
        document.getElementById('modal-container').innerHTML = html;
        htmx.process(document.getElementById('modal-container'));
        const dialog = document.querySelector('#modal-container dialog');
        if (dialog) dialog.showModal();
      });
  }

  // ── Navigation shortcuts ───────────────────────────────────────────────

  function navigate(path) {
    window.location.href = path;
  }

  // ── Approval queue actions ────────────────────────────────────────────

  function queueAction(action) {
    const items = getFocusableItems();
    if (focusedIndex < 0 || focusedIndex >= items.length) return;
    const card = items[focusedIndex];
    const id = card.id?.replace('card-', '');
    if (!id) return;

    if (action === 'approve') {
      const btn = card.querySelector('[hx-post*="/approve"]');
      if (btn) htmx.trigger(btn, 'click');
    } else if (action === 'edit') {
      const btn = card.querySelector('[hx-get*="/draft"]');
      if (btn) htmx.trigger(btn, 'click');
    } else if (action === 'reject') {
      const btn = card.querySelector('[hx-post*="/reject"]');
      if (btn) htmx.trigger(btn, 'click');
    }
  }

  // ── Reply / forward (inbox detail) ───────────────────────────────────

  function triggerReply() {
    const btn = document.querySelector('#reply-btn, [data-action="reply"]');
    if (btn) btn.click();
  }

  function triggerForward() {
    const btn = document.querySelector('#forward-btn, [data-action="forward"]');
    if (btn) btn.click();
  }

  // ── Shortcuts help overlay ────────────────────────────────────────────

  function toggleHelp() {
    let overlay = document.getElementById('shortcuts-overlay');
    if (overlay) {
      overlay.remove();
      return;
    }
    overlay = document.createElement('div');
    overlay.id = 'shortcuts-overlay';
    overlay.className = 'shortcuts-overlay';
    overlay.innerHTML = `
      <div class="shortcuts-panel">
        <div class="shortcuts-header">
          <span>Keyboard shortcuts</span>
          <button onclick="document.getElementById('shortcuts-overlay').remove()">✕</button>
        </div>
        <div class="shortcuts-grid">
          <div class="shortcuts-section">
            <div class="shortcuts-group-title">Navigation</div>
            <div class="shortcut-row"><kbd>g</kbd><kbd>i</kbd> <span>Go to Inbox</span></div>
            <div class="shortcut-row"><kbd>g</kbd><kbd>q</kbd> <span>Go to AI Queue</span></div>
            <div class="shortcut-row"><kbd>g</kbd><kbd>t</kbd> <span>Go to Tasks</span></div>
            <div class="shortcut-row"><kbd>g</kbd><kbd>h</kbd> <span>Go to History</span></div>
            <div class="shortcut-row"><kbd>g</kbd><kbd>s</kbd> <span>Go to Settings</span></div>
          </div>
          <div class="shortcuts-section">
            <div class="shortcuts-group-title">List</div>
            <div class="shortcut-row"><kbd>j</kbd> / <kbd>↓</kbd> <span>Next</span></div>
            <div class="shortcut-row"><kbd>k</kbd> / <kbd>↑</kbd> <span>Previous</span></div>
            <div class="shortcut-row"><kbd>Enter</kbd> <span>Open</span></div>
            <div class="shortcut-row"><kbd>#</kbd> <span>Trash (inbox)</span></div>
          </div>
          <div class="shortcuts-section">
            <div class="shortcuts-group-title">Message</div>
            <div class="shortcut-row"><kbd>r</kbd> <span>Reply</span></div>
            <div class="shortcut-row"><kbd>f</kbd> <span>Forward</span></div>
            <div class="shortcut-row"><kbd>Esc</kbd> <span>Back / close</span></div>
          </div>
          <div class="shortcuts-section">
            <div class="shortcuts-group-title">Queue</div>
            <div class="shortcut-row"><kbd>a</kbd> <span>Approve &amp; send</span></div>
            <div class="shortcut-row"><kbd>e</kbd> <span>Edit draft</span></div>
            <div class="shortcut-row"><kbd>x</kbd> <span>Reject</span></div>
          </div>
          <div class="shortcuts-section">
            <div class="shortcuts-group-title">Global</div>
            <div class="shortcut-row"><kbd>c</kbd> <span>Compose</span></div>
            <div class="shortcut-row"><kbd>\</kbd> <span>Toggle AI chat</span></div>
            <div class="shortcut-row"><kbd>?</kbd> <span>This help</span></div>
          </div>
        </div>
      </div>`;
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  }

  // ── Main keydown handler ───────────────────────────────────────────────

  document.addEventListener('keydown', function (e) {
    // Never intercept when typing in a field
    if (isTyping()) return;

    const key = e.key;
    const modal = isModalOpen();

    // Escape: close modal or clear focus
    if (key === 'Escape') {
      if (modal) {
        document.querySelector('dialog[open]')?.close();
      } else {
        // If in detail view, go back to list
        const detail = document.getElementById('message-detail-pane');
        if (detail && detail.children.length) {
          detail.innerHTML = '';
          document.getElementById('inbox-list-pane')?.classList.remove('detail-open');
          clearFocus();
        } else {
          clearFocus();
        }
      }
      return;
    }

    // Don't process further shortcuts when modal is open
    if (modal) return;

    // ── g-chord navigation ──────────────────────────────────────────────
    if (key === 'g' && !gPrefix) {
      gPrefix = true;
      clearTimeout(gTimer);
      gTimer = setTimeout(() => { gPrefix = false; }, 1000);
      return;
    }

    if (gPrefix) {
      gPrefix = false;
      clearTimeout(gTimer);
      e.preventDefault();
      switch (key) {
        case 'i': navigate('/inbox'); break;
        case 'q': navigate('/queue'); break;
        case 'h': navigate('/history'); break;
        case 's': navigate('/settings'); break;
        case 't': navigate('/tasks'); break;
      }
      return;
    }

    // ── Single-key shortcuts ────────────────────────────────────────────
    switch (key) {
      case '?':
        e.preventDefault();
        toggleHelp();
        break;

      case '\\':
        e.preventDefault();
        if (typeof toggleChat === 'function') toggleChat();
        break;

      case 'c':
        e.preventDefault();
        // Open compose modal via HTMX click on the compose button
        document.getElementById('compose-btn')?.click() ||
          document.getElementById('nav-compose')?.click();
        break;

      case 'j':
      case 'ArrowDown': {
        e.preventDefault();
        const items = getFocusableItems();
        if (!items.length) break;
        setFocus(focusedIndex < 0 ? 0 : focusedIndex + 1);
        break;
      }

      case 'k':
      case 'ArrowUp': {
        e.preventDefault();
        const items = getFocusableItems();
        if (!items.length) break;
        setFocus(focusedIndex < 0 ? items.length - 1 : focusedIndex - 1);
        break;
      }

      case 'Enter':
      case 'o':
        e.preventDefault();
        openFocused();
        break;

      case 'r':
        e.preventDefault();
        triggerReply();
        break;

      case 'f':
        e.preventDefault();
        triggerForward();
        break;

      case '#': {
        e.preventDefault();
        const items = getFocusableItems();
        if (focusedIndex >= 0 && focusedIndex < items.length) {
          const row = items[focusedIndex];
          const trashBtn = row.querySelector('[data-action="trash"]');
          if (trashBtn) trashBtn.click();
        }
        break;
      }

      // Queue-specific
      case 'a':
        e.preventDefault();
        queueAction('approve');
        break;
      case 'e':
        e.preventDefault();
        queueAction('edit');
        break;
      case 'x':
        e.preventDefault();
        queueAction('reject');
        break;
    }
  });

  // Re-set focus index to 0 after HTMX swaps in a new list
  document.body.addEventListener('htmx:afterSwap', function (e) {
    const target = e.detail.target;
    if (
      target.id === 'pending-queue' ||
      target.id === 'inbox-message-list' ||
      target.id === 'history-list'
    ) {
      focusedIndex = -1;
    }
  });

})();
