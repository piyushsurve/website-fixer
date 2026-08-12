/*
 * Challenge arena controller: editor, sandboxed preview, countdown, checks.
 *
 * The countdown rendered here is only a display. `remaining` always comes
 * from the server (page load + periodic /api/state/ sync), and the server
 * refuses saves and checks once the session is over, so editing the numbers
 * in devtools buys the player nothing.
 */
(function () {
  'use strict';

  var config = JSON.parse(document.getElementById('wf-arena-data').textContent);
  var state = config.state;
  var timerConfig = config.timer;
  var urls = config.urls;

  var PREVIEW_DEBOUNCE_MS = 400;
  var AUTOSAVE_DEBOUNCE_MS = 1200;

  // Virtual viewport widths for the preview (see fitPreview).
  var DESKTOP_WIDTH = 1120;
  var PHONE_WIDTH = 390;

  var el = {
    timer: document.getElementById('wf-timer'),
    timerValue: document.getElementById('wf-timer-value'),
    progressCount: document.getElementById('wf-progress-count'),
    progressFill: document.getElementById('wf-progress-fill'),
    html: document.getElementById('wf-html'),
    css: document.getElementById('wf-css'),
    preview: document.getElementById('wf-preview'),
    previewWrap: document.getElementById('wf-preview-wrap'),
    run: document.getElementById('wf-run'),
    reset: document.getElementById('wf-reset'),
    saveState: document.getElementById('wf-save-state'),
    objectives: document.getElementById('wf-objectives'),
    modal: document.getElementById('wf-modal'),
    modalBox: document.getElementById('wf-modal-box'),
    modalIcon: document.getElementById('wf-modal-icon'),
    modalTitle: document.getElementById('wf-modal-title'),
    modalText: document.getElementById('wf-modal-text'),
    modalObjectives: document.getElementById('wf-modal-objectives'),
    modalTimeLabel: document.getElementById('wf-modal-timelabel'),
    modalTime: document.getElementById('wf-modal-time'),
    modalStatus: document.getElementById('wf-modal-status'),
    modalClose: document.getElementById('wf-modal-close')
  };

  var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
  var endsAt = Date.now() + state.remaining * 1000;
  var lastRemaining = state.remaining;
  var locked = false;
  var previewTimer = null;
  var saveTimer = null;
  var cleared = {};

  // ------------------------------------------------------------- helpers --

  function post(url, payload) {
    var body = new URLSearchParams(payload || {});
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-CSRFToken': csrfToken,
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: body.toString(),
      credentials: 'same-origin'
    }).then(function (response) { return response.json(); });
  }

  function submission() {
    return { html: el.html.value, css: el.css.value };
  }

  function pad(value) { return value < 10 ? '0' + value : String(value); }

  function clock(seconds) {
    return pad(Math.floor(seconds / 60)) + ':' + pad(seconds % 60);
  }

  // ------------------------------------------------------------- preview --

  /*
   * NovaCloud is a complete HTML document, so it is rendered as written and
   * its <link rel="stylesheet"> is swapped for the player's live CSS. The
   * stylesheet link is matched loosely (any href ending in style.css) so a
   * player who retypes or moves it does not lose their preview.
   */
  var STYLESHEET_LINK = /<link\b[^>]*href\s*=\s*["'][^"']*style\.css["'][^>]*>/i;

  function previewDocument() {
    var html = el.html.value;
    var style = '<style>' + el.css.value + '</style>';

    if (STYLESHEET_LINK.test(html)) {
      return html.replace(STYLESHEET_LINK, style);
    }
    if (/<\/head\s*>/i.test(html)) {
      return html.replace(/<\/head\s*>/i, style + '</head>');
    }
    // Not a full document (they deleted the head, or pasted a fragment):
    // wrap it so the preview still works.
    return '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1">' +
      style + '</head><body>' + html + '</body></html>';
  }

  function renderPreview() {
    // The player's page lives in a sandboxed iframe: no scripts, no access to
    // this document, and its CSS cannot reach the game shell around it.
    el.preview.srcdoc = previewDocument();
  }

  function queuePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(renderPreview, PREVIEW_DEBOUNCE_MS);
  }

  /*
   * Render the page at a fixed virtual width and scale it to fit the panel.
   * Letting the iframe be panel-width instead would sit permanently inside
   * NovaCloud's own 760px breakpoint, hiding the desktop-only mistakes.
   */
  function fitPreview() {
    var phone = el.previewWrap.getAttribute('data-width') === 'phone';
    var virtual = phone ? PHONE_WIDTH : DESKTOP_WIDTH;
    var width = el.previewWrap.clientWidth;
    var height = el.previewWrap.clientHeight;
    if (!width || !height) { return; }

    var scale = Math.min(1, width / virtual);
    el.preview.style.width = virtual + 'px';
    el.preview.style.height = Math.round(height / scale) + 'px';
    el.preview.style.transform = 'scale(' + scale + ')';
    el.preview.style.left = Math.max(0, (width - virtual * scale) / 2) + 'px';
  }

  // --------------------------------------------------------------- saving --

  function setSaveState(value, text) {
    el.saveState.setAttribute('data-state', value);
    el.saveState.textContent = text;
  }

  function save() {
    if (locked) { return Promise.resolve(); }
    setSaveState('saving', 'saving…');
    return post(urls.save, submission()).then(function (data) {
      applyState(data);
      setSaveState(data.error ? 'error' : 'saved', data.error || 'saved');
    }).catch(function () {
      setSaveState('error', 'offline — retrying');
    });
  }

  function queueSave() {
    clearTimeout(saveTimer);
    setSaveState('dirty', 'unsaved changes');
    saveTimer = setTimeout(save, AUTOSAVE_DEBOUNCE_MS);
  }

  // ---------------------------------------------------------------- timer --

  function paintTimer(seconds) {
    el.timerValue.textContent = clock(seconds);
    var mode = 'normal';
    if (seconds <= 0) { mode = 'over'; }
    else if (seconds <= timerConfig.danger) { mode = 'danger'; }
    else if (seconds <= timerConfig.warning) { mode = 'warning'; }
    el.timer.setAttribute('data-state', mode);
  }

  function tick() {
    var seconds = Math.max(0, Math.round((endsAt - Date.now()) / 1000));
    lastRemaining = seconds;
    paintTimer(seconds);
    if (seconds <= 0 && !locked) { syncState(); }
  }

  function syncState() {
    return fetch(urls.state, { credentials: 'same-origin' })
      .then(function (response) { return response.json(); })
      .then(applyState)
      .catch(function () { /* offline: keep counting locally until it returns */ });
  }

  function applyState(data) {
    if (!data || typeof data.remaining !== 'number') { return; }
    endsAt = Date.now() + data.remaining * 1000;
    lastRemaining = data.remaining;
    paintTimer(data.remaining);
    if (data.completed) { lock('completed', data); }
    else if (data.expired) { lock('expired', data); }
  }

  // ----------------------------------------------------------- objectives --

  function paintChecks(checks, passed) {
    checks.forEach(function (check) {
      var node = el.objectives.querySelector('[data-check-id="' + check.id + '"]');
      if (!node) { return; }

      if (check.passed && !cleared[check.id]) {
        cleared[check.id] = true;
        node.classList.add('is-just-done');
        setTimeout(function () { node.classList.remove('is-just-done'); }, 700);
      }
      if (!check.passed) { cleared[check.id] = false; }

      node.classList.toggle('is-done', check.passed);
      node.querySelector('.wf-objective__mark').textContent = check.passed ? '✓' : '';
    });

    el.progressCount.textContent = passed + '/' + state.total;
    el.progressFill.style.width = (passed / state.total * 100) + '%';
  }

  function runChecks() {
    if (locked) { return; }
    el.run.disabled = true;
    el.run.textContent = 'Checking…';
    post(urls.check, submission()).then(function (data) {
      var passed = data.passed !== undefined ? data.passed : (data.score || 0);
      if (data.checks) { paintChecks(data.checks, passed); }
      setSaveState('saved', 'saved');
      applyState(data);
      if (!data.completed && !data.expired) {
        var left = state.total - passed;
        setSaveState('saved', left + ' objective' + (left === 1 ? '' : 's') + ' left');
      }
    }).catch(function () {
      setSaveState('error', 'check failed — try again');
    }).finally(function () {
      if (!locked) { el.run.disabled = false; }
      el.run.textContent = 'Run checks';
    });
  }

  // --------------------------------------------------------------- hints --

  function revealHint(button) {
    var item = button.closest('.wf-objective');
    var hints = item.querySelectorAll('.wf-objective__hint');
    var next = null;
    for (var i = 0; i < hints.length; i++) {
      if (hints[i].hidden) { next = hints[i]; break; }
    }
    if (!next) { return; }
    next.hidden = false;

    var shown = 0;
    for (var j = 0; j < hints.length; j++) {
      if (!hints[j].hidden) { shown += 1; }
    }
    if (shown >= hints.length) {
      button.hidden = true;
    } else {
      button.textContent = 'Show hint ' + (shown + 1) + ' of ' + hints.length;
    }
  }

  // --------------------------------------------------------------- ending --

  function lock(reason, data) {
    if (locked) { return; }
    locked = true;
    clearTimeout(saveTimer);
    el.html.disabled = true;
    el.css.disabled = true;
    el.run.disabled = true;
    el.reset.disabled = true;

    var score = data.score || 0;

    if (reason === 'completed') {
      el.timer.setAttribute('data-state', 'normal');
      showModal({
        outcome: 'win',
        icon: '🏆',
        title: 'Website fixed!',
        text: 'You cleared every objective before the clock ran out.',
        objectives: state.total + ' / ' + state.total,
        timeLabel: 'Time remaining',
        time: clock(Math.max(0, lastRemaining)),
        status: 'PASSED'
      });
      confetti();
    } else {
      paintTimer(0);
      showModal({
        outcome: 'timeout',
        icon: '⏳',
        title: "Time's up",
        text: 'The session is over. Your last saved work has been kept.',
        objectives: score + ' / ' + state.total,
        timeLabel: 'Objectives left',
        time: String(state.total - score),
        status: 'TIMEOUT'
      });
    }
  }

  function showModal(view) {
    el.modalBox.setAttribute('data-outcome', view.outcome);
    el.modalIcon.textContent = view.icon;
    el.modalTitle.textContent = view.title;
    el.modalText.textContent = view.text;
    el.modalObjectives.textContent = view.objectives;
    el.modalTimeLabel.textContent = view.timeLabel;
    el.modalTime.textContent = view.time;
    el.modalStatus.textContent = view.status;
    el.modal.hidden = false;
    el.modalClose.focus();
  }

  function confetti() {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) { return; }
    var colors = ['#7c3aed', '#3b82f6', '#06b6d4', '#f472b6', '#facc15', '#22c55e'];
    var layer = document.createElement('div');
    layer.className = 'wf-confetti';
    for (var i = 0; i < 70; i++) {
      var piece = document.createElement('i');
      piece.style.left = Math.random() * 100 + '%';
      piece.style.background = colors[i % colors.length];
      piece.style.animationDuration = (2.2 + Math.random() * 1.8) + 's';
      piece.style.animationDelay = (Math.random() * 0.7) + 's';
      layer.appendChild(piece);
    }
    document.body.appendChild(layer);
    setTimeout(function () { layer.remove(); }, 5200);
  }

  // ----------------------------------------------------------------- wire --

  function bindEditor(area) {
    area.addEventListener('input', function () { queuePreview(); queueSave(); });
    area.addEventListener('keydown', function (event) {
      if (event.key !== 'Tab') { return; }
      event.preventDefault();
      var start = area.selectionStart;
      var end = area.selectionEnd;
      area.value = area.value.slice(0, start) + '  ' + area.value.slice(end);
      area.selectionStart = area.selectionEnd = start + 2;
      queuePreview();
      queueSave();
    });
  }

  bindEditor(el.html);
  bindEditor(el.css);

  Array.prototype.forEach.call(document.querySelectorAll('[data-tab]'), function (tab) {
    tab.addEventListener('click', function () {
      var target = tab.getAttribute('data-tab');
      Array.prototype.forEach.call(document.querySelectorAll('[data-tab]'), function (other) {
        other.setAttribute('aria-selected', String(other === tab));
      });
      el.html.hidden = target !== 'html';
      el.css.hidden = target !== 'css';
      (target === 'html' ? el.html : el.css).focus();
    });
  });

  Array.prototype.forEach.call(document.querySelectorAll('[data-width]'), function (button) {
    button.addEventListener('click', function () {
      var width = button.getAttribute('data-width');
      Array.prototype.forEach.call(document.querySelectorAll('[data-width]'), function (other) {
        other.setAttribute('aria-pressed', String(other === button));
      });
      el.previewWrap.setAttribute('data-width', width);
      fitPreview();
    });
  });

  window.addEventListener('resize', fitPreview);

  el.objectives.addEventListener('click', function (event) {
    var button = event.target.closest('.wf-hint-btn');
    if (button) { revealHint(button); }
  });

  el.run.addEventListener('click', runChecks);

  el.reset.addEventListener('click', function () {
    if (locked || !window.confirm('Restore the original broken page? Your edits will be lost.')) { return; }
    post(urls.reset, {}).then(function (data) {
      if (data.html !== undefined) {
        el.html.value = data.html;
        el.css.value = data.css;
        renderPreview();
        setSaveState('saved', 'reset to the broken version');
      }
      applyState(data);
    });
  });

  el.modalClose.addEventListener('click', function () { el.modal.hidden = true; });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !el.modal.hidden) { el.modal.hidden = true; return; }
    if (!(event.ctrlKey || event.metaKey)) { return; }
    if (event.key === 'Enter') { event.preventDefault(); runChecks(); }
    if (event.key.toLowerCase() === 's') { event.preventDefault(); clearTimeout(saveTimer); save(); }
  });

  window.addEventListener('focus', syncState);

  // ----------------------------------------------------------------- boot --

  Array.prototype.forEach.call(el.objectives.querySelectorAll('.wf-objective.is-done'), function (item) {
    cleared[item.getAttribute('data-check-id')] = true;
  });

  renderPreview();
  fitPreview();
  paintTimer(state.remaining);
  setInterval(tick, 250);
  setInterval(syncState, timerConfig.sync * 1000);

  if (state.completed) { lock('completed', state); }
  else if (state.expired) { lock('expired', state); }
}());
