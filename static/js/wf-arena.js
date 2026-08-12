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

  var el = {
    timer: document.getElementById('wf-timer'),
    timerValue: document.getElementById('wf-timer-value'),
    progressCount: document.getElementById('wf-progress-count'),
    progressFill: document.getElementById('wf-progress-fill'),
    html: document.getElementById('wf-html'),
    css: document.getElementById('wf-css'),
    preview: document.getElementById('wf-preview'),
    run: document.getElementById('wf-run'),
    reset: document.getElementById('wf-reset'),
    saveState: document.getElementById('wf-save-state'),
    objectives: document.getElementById('wf-objectives'),
    modal: document.getElementById('wf-modal'),
    modalIcon: document.getElementById('wf-modal-icon'),
    modalTitle: document.getElementById('wf-modal-title'),
    modalText: document.getElementById('wf-modal-text'),
    modalScore: document.getElementById('wf-modal-score'),
    modalClose: document.getElementById('wf-modal-close')
  };

  var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
  var endsAt = Date.now() + state.remaining * 1000;
  var locked = false;
  var previewTimer = null;
  var saveTimer = null;

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

  // ------------------------------------------------------------- preview --

  function renderPreview() {
    // The player's page lives in a sandboxed iframe: no scripts, no access to
    // this document, and its CSS cannot reach the game shell around it.
    el.preview.srcdoc =
      '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<style>' + el.css.value + '</style></head><body>' +
      el.html.value +
      '</body></html>';
  }

  function queuePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(renderPreview, PREVIEW_DEBOUNCE_MS);
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

  function pad(value) { return value < 10 ? '0' + value : String(value); }

  function paintTimer(seconds) {
    el.timerValue.textContent = pad(Math.floor(seconds / 60)) + ':' + pad(seconds % 60);
    var mode = 'normal';
    if (seconds <= 0) { mode = 'over'; }
    else if (seconds <= timerConfig.danger) { mode = 'danger'; }
    else if (seconds <= timerConfig.warning) { mode = 'warning'; }
    el.timer.setAttribute('data-state', mode);
  }

  function tick() {
    var seconds = Math.max(0, Math.round((endsAt - Date.now()) / 1000));
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
    paintTimer(data.remaining);
    if (data.completed) { lock('completed', data); }
    else if (data.expired) { lock('expired', data); }
  }

  // ----------------------------------------------------------- objectives --

  function paintChecks(checks, passed) {
    checks.forEach(function (check) {
      var node = el.objectives.querySelector('[data-check-id="' + check.id + '"]');
      if (!node) { return; }
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
      if (data.checks) { paintChecks(data.checks, data.passed !== undefined ? data.passed : data.score); }
      setSaveState('saved', 'saved');
      applyState(data);
      if (!data.completed && !data.expired) {
        var passed = data.passed || 0;
        flashResult(passed);
      }
    }).catch(function () {
      setSaveState('error', 'check failed — try again');
    }).finally(function () {
      if (!locked) { el.run.disabled = false; }
      el.run.textContent = 'Run checks';
    });
  }

  function flashResult(passed) {
    var remainingCount = state.total - passed;
    setSaveState('saved', remainingCount + ' objective' + (remainingCount === 1 ? '' : 's') + ' left');
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

    if (reason === 'completed') {
      el.timer.setAttribute('data-state', 'normal');
      showModal('🏆', 'Website repaired!', 'You cleared every objective before the clock ran out.',
        (data.score || state.total) + ' / ' + state.total + ' objectives');
    } else {
      paintTimer(0);
      showModal('⏳', "Time's up", 'The 30 minute session is over. Your last saved work has been kept.',
        (data.score || 0) + ' / ' + state.total + ' objectives');
    }
  }

  function showModal(icon, title, text, score) {
    el.modalIcon.textContent = icon;
    el.modalTitle.textContent = title;
    el.modalText.textContent = text;
    el.modalScore.textContent = score;
    el.modal.hidden = false;
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
    if (!(event.ctrlKey || event.metaKey)) { return; }
    if (event.key === 'Enter') { event.preventDefault(); runChecks(); }
    if (event.key.toLowerCase() === 's') { event.preventDefault(); clearTimeout(saveTimer); save(); }
  });

  window.addEventListener('focus', syncState);

  // ----------------------------------------------------------------- boot --

  renderPreview();
  paintTimer(state.remaining);
  setInterval(tick, 250);
  setInterval(syncState, timerConfig.sync * 1000);

  if (state.completed) { lock('completed', state); }
  else if (state.expired) { lock('expired', state); }
}());
