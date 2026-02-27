/* ============================================================
 *  DJ Library — Review UI
 *  Keyboard-driven track preview & approval workflow
 *
 *  Space        = play / pause
 *  Up / Down    = navigate rows
 *  Shift+Up/Dn  = extend batch selection
 *  Enter        = play selected
 *  Esc          = stop playback
 *  A            = accept (+ auto-advance)
 *  R            = reject (+ auto-advance)
 *  V            = review (+ auto-advance)
 *  G            = apply genre suggestion
 *  N            = jump to next undecided
 *  D            = toggle done
 *  Ctrl/Cmd+Z   = undo last status/dest change
 * ============================================================ */

(function () {
  'use strict';

  // -- State --------------------------------------------------
  let allTracks = [];
  let filteredTracks = [];
  let genres = [];
  let currentIndex = -1;
  let currentSource = 'unsorted';
  let sortKey = null;
  let sortDir = 1;  // 1 = ascending, -1 = descending

  // Auto-play on navigation
  let autoPlay = false;

  // Auto-destination mapping
  const AUTO_DEST = { accept: 'library', reject: 'reject' };

  // Batch selection
  let selectedSet = new Set();   // indices of selected rows
  let selectionAnchor = -1;

  // Undo stack (max 50)
  const MAX_UNDO = 50;
  let undoStack = [];

  // Debounced saves: { trackId: { timer, fields } }
  let pendingSaves = {};
  const SAVE_DEBOUNCE_MS = 80;

  // Preload debounce
  let _preloadTimer = null;

  // -- DOM refs -----------------------------------------------
  const audio            = document.getElementById('audio-player');
  const tableHead        = document.getElementById('table-head');
  const tableBody        = document.getElementById('table-body');
  const emptyState       = document.getElementById('empty-state');
  const sourceSelect     = document.getElementById('source-select');
  const searchInput      = document.getElementById('search-input');
  const filterStatus     = document.getElementById('filter-status');
  const filterDone       = document.getElementById('filter-done');
  const trackCount       = document.getElementById('track-count');
  const statsBar         = document.getElementById('stats-bar');
  const autoPlayCheckbox = document.getElementById('auto-play-checkbox');
  const nowArtist        = document.getElementById('now-artist');
  const nowTitle         = document.getElementById('now-title');
  const nowIndicator     = document.getElementById('now-playing-indicator');
  const playerProgress   = document.getElementById('player-progress');
  const progressHover    = document.getElementById('player-progress-hover');
  const progressContainer= document.getElementById('player-progress-container');
  const waveformCanvas   = document.getElementById('waveform-canvas');
  const playerTime       = document.getElementById('player-time');
  const genreSources     = document.getElementById('genre-sources');
  const toast            = document.getElementById('toast');

  // -- Column definitions per source --------------------------
  const COLUMNS = {
    unsorted: [
      { key: '_index',       label: '#',       width: '36px' },
      { key: 'artist',       label: 'Artist',  width: '16%',  type: 'editable' },
      { key: 'title',        label: 'Title',   width: '18%',  type: 'editable' },
      { key: 'version_info', label: 'Version', width: '11%',  type: 'editable' },
      { key: 'genre',        label: 'Genre',   width: '11%',  type: 'genre-select' },
      { key: 'year',         label: 'Year',    width: '48px', type: 'editable', cls: 'col-bpm' },
      { key: 'bpm',          label: 'BPM',     width: '46px', cls: 'col-bpm' },
      { key: 'key_camelot',  label: 'Key',     width: '40px', cls: 'col-key' },
      { key: 'destination',  label: 'Dest',    width: '72px', type: 'dest-select' },
      { key: 'status',       label: 'Status',  width: '68px', type: 'status-display' },
      { key: 'done',         label: '\u2713', width: '32px', type: 'checkbox' },
    ],
    library: [
      { key: '_index',           label: '#',      width: '36px' },
      { key: 'artist',           label: 'Artist', width: '22%' },
      { key: 'title',            label: 'Title',  width: '28%' },
      { key: 'bpm',              label: 'BPM',    width: '50px',  cls: 'col-bpm' },
      { key: 'key',              label: 'Key',    width: '44px',  cls: 'col-key' },
      { key: 'duration_seconds', label: 'Dur',    width: '55px',  cls: 'col-bpm', fmt: fmtDuration },
      { key: 'play_count',       label: 'Plays',  width: '44px',  cls: 'col-bpm' },
      { key: 'date_added',       label: 'Added',  width: '90px' },
    ],
  };

  // -- Helpers ------------------------------------------------
  function fmtTime(sec) {
    if (!sec || isNaN(sec)) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return m + ':' + String(s).padStart(2, '0');
  }

  function fmtDuration(val) {
    const n = parseFloat(val);
    if (!n || isNaN(n)) return '';
    return fmtTime(n);
  }

  function audioPath(track) {
    return track.file_path || track.old_full_path || '';
  }

  function trackId(track) {
    return track.track_id || track.file_hash || '';
  }

  function showToast(msg, cls) {
    toast.textContent = msg;
    toast.className = 'toast visible' + (cls ? ' toast-' + cls : '');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function() { toast.className = 'toast hidden'; }, 1200);
  }

  // -- Data loading -------------------------------------------
  async function loadTracks(source) {
    currentSource = source;
    try {
      const resp = await fetch('/api/tracks?source=' + source);
      allTracks = await resp.json();
    } catch (e) {
      allTracks = [];
      console.error('Failed to load tracks:', e);
    }
    currentIndex = -1;
    selectedSet.clear();
    selectionAnchor = -1;
    undoStack = [];
    applyFilters();
    // Auto-select first row
    if (filteredTracks.length > 0) {
      selectRow(0);
    }
  }

  async function loadGenres() {
    try {
      const resp = await fetch('/api/genres');
      genres = await resp.json();
    } catch (e) {
      genres = [];
    }
  }

  // -- Filtering & sorting ------------------------------------
  function applyFilters() {
    const q = searchInput.value.toLowerCase().trim();
    const sf = filterStatus.value;
    const df = filterDone.value;

    filteredTracks = allTracks.filter(function(t) {
      // Text search
      if (q) {
        const hay = [t.artist, t.title, t.version_info, t.genre, t.tag_genre_original]
          .filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      // Status filter
      if (sf) {
        if (sf === 'undecided') {
          if (t.status && t.status !== '') return false;
        } else {
          if (t.status !== sf) return false;
        }
      }
      // Done filter
      if (df) {
        if (t.done !== df) return false;
      }
      return true;
    });

    // Sort
    if (sortKey && sortKey !== '_index') {
      filteredTracks.sort(function(a, b) {
        const va = (a[sortKey] || '').toString();
        const vb = (b[sortKey] || '').toString();
        const na = parseFloat(va), nb = parseFloat(vb);
        if (!isNaN(na) && !isNaN(nb)) return (na - nb) * sortDir;
        return va.localeCompare(vb, undefined, { sensitivity: 'base' }) * sortDir;
      });
    }

    renderTable();
    trackCount.textContent = filteredTracks.length + ' / ' + allTracks.length;
    emptyState.style.display = filteredTracks.length === 0 ? '' : 'none';
    document.getElementById('tracks-table').style.display = filteredTracks.length === 0 ? 'none' : '';
    updateStats();
  }

  // -- Stats bar ----------------------------------------------
  function updateStats() {
    if (currentSource !== 'unsorted') {
      statsBar.innerHTML = '';
      return;
    }
    var acc = 0, rej = 0, rev = 0, und = 0;
    for (var i = 0; i < allTracks.length; i++) {
      var t = allTracks[i];
      if (t.status === 'accept') acc++;
      else if (t.status === 'reject') rej++;
      else if (t.status === 'review') rev++;
      else und++;
    }
    statsBar.innerHTML =
      '<span class="stat-accept">' + acc + ' acc</span> \u00b7 ' +
      '<span class="stat-reject">' + rej + ' rej</span> \u00b7 ' +
      '<span class="stat-review">' + rev + ' rev</span> \u00b7 ' +
      '<span class="stat-undecided">' + und + ' todo</span>';
  }

  // -- Table rendering ----------------------------------------
  function renderTable() {
    const cols = COLUMNS[currentSource] || COLUMNS.unsorted;

    // Header
    tableHead.innerHTML = '';
    const hr = document.createElement('tr');
    for (const col of cols) {
      const th = document.createElement('th');
      th.textContent = col.label;
      if (col.width) th.style.width = col.width;
      th.dataset.key = col.key;
      if (sortKey === col.key) {
        th.classList.add('sorted');
        th.textContent += sortDir === 1 ? ' \u25B2' : ' \u25BC';
      }
      th.addEventListener('click', function() { handleSort(col.key); });
      hr.appendChild(th);
    }
    tableHead.appendChild(hr);

    // Body
    tableBody.innerHTML = '';
    const frag = document.createDocumentFragment();

    for (let i = 0; i < filteredTracks.length; i++) {
      const track = filteredTracks[i];
      const tr = document.createElement('tr');
      tr.dataset.idx = i;

      // Row state classes
      if (i === currentIndex) tr.classList.add('active');
      if (selectedSet.has(i)) tr.classList.add('selected');
      if (track.status === 'accept') tr.classList.add('status-accept');
      else if (track.status === 'reject') tr.classList.add('status-reject');
      if (track.done === 'TRUE') tr.classList.add('is-done');

      for (const col of cols) {
        const td = document.createElement('td');
        if (col.cls) td.classList.add(col.cls);

        if (col.key === '_index') {
          td.textContent = i + 1;
          td.classList.add('col-index');

        } else if (col.type === 'checkbox') {
          const cb = document.createElement('input');
          cb.type = 'checkbox';
          cb.checked = track[col.key] === 'TRUE';
          cb.addEventListener('change', (function(track, col, cb, tr) {
            return function(e) {
              e.stopPropagation();
              track[col.key] = cb.checked ? 'TRUE' : 'FALSE';
              saveTrackField(track, col.key, track[col.key]);
              tr.classList.toggle('is-done', cb.checked);
            };
          })(track, col, cb, tr));
          td.appendChild(cb);

        } else if (col.type === 'genre-select') {
          const sel = buildGenreSelect(track[col.key]);
          sel.addEventListener('change', (function(track, col, sel) {
            return function(e) {
              e.stopPropagation();
              track[col.key] = sel.value;
              saveTrackField(track, col.key, sel.value);
            };
          })(track, col, sel));
          sel.addEventListener('mousedown', function(e) { e.stopPropagation(); });
          td.appendChild(sel);

        } else if (col.type === 'dest-select') {
          const sel = buildDestSelect(track[col.key]);
          sel.addEventListener('change', (function(track, col, sel) {
            return function(e) {
              e.stopPropagation();
              track[col.key] = sel.value;
              saveTrackField(track, col.key, sel.value);
            };
          })(track, col, sel));
          sel.addEventListener('mousedown', function(e) { e.stopPropagation(); });
          td.appendChild(sel);

        } else if (col.type === 'editable') {
          td.textContent = track[col.key] || '';
          td.title = track[col.key] || '';
          td.classList.add('cell-editable');
          td.addEventListener('dblclick', (function(td, track, col) {
            return function(e) {
              e.stopPropagation();
              startInlineEdit(td, track, col.key);
            };
          })(td, track, col));

        } else if (col.type === 'status-display') {
          td.textContent = track[col.key] || '\u2014';
          td.classList.add('col-status');
          if (track[col.key]) td.classList.add(track[col.key]);

        } else {
          const raw = track[col.key] || '';
          td.textContent = col.fmt ? col.fmt(raw) : raw;
          td.title = raw;
        }

        tr.appendChild(td);
      }

      // Row events
      tr.addEventListener('click', (function(i) {
        return function(e) {
          if (e.shiftKey) {
            extendSelection(i);
          } else {
            clearSelection();
            selectRow(i);
          }
        };
      })(i));
      tr.addEventListener('dblclick', (function(i) {
        return function() { selectRow(i); playTrack(i); };
      })(i));

      frag.appendChild(tr);
    }

    tableBody.appendChild(frag);
  }

  function buildGenreSelect(currentValue) {
    const sel = document.createElement('select');
    sel.classList.add('inline-select');

    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = '\u2014';
    sel.appendChild(empty);

    for (const g of genres) {
      const o = document.createElement('option');
      o.value = g;
      o.textContent = g;
      if (g === currentValue) o.selected = true;
      sel.appendChild(o);
    }
    return sel;
  }
  const DEST_OPTIONS = ['', 'library', 'reject', 'archive', 'mixes'];

  function buildDestSelect(currentValue) {
    const sel = document.createElement('select');
    sel.classList.add('inline-select');
    for (const d of DEST_OPTIONS) {
      const o = document.createElement('option');
      o.value = d;
      o.textContent = d || '\u2014';
      if (d === currentValue) o.selected = true;
      sel.appendChild(o);
    }
    return sel;
  }

  // -- Inline editing (double-click on artist/title/version/year) --
  function startInlineEdit(td, track, key) {
    if (td.querySelector('input')) return;  // already editing
    const oldVal = track[key] || '';
    const input = document.createElement('input');
    input.type = 'text';
    input.value = oldVal;
    input.className = 'inline-edit';
    td.textContent = '';
    td.appendChild(input);
    input.focus();
    input.select();

    function commit() {
      const newVal = input.value.trim();
      td.textContent = newVal;
      td.title = newVal;
      if (newVal !== oldVal) {
        track[key] = newVal;
        saveTrackField(track, key, newVal);
        showToast(key + ' updated', '');
      }
    }

    input.addEventListener('blur', commit);
    input.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      if (e.key === 'Escape') { input.value = oldVal; input.blur(); }
      e.stopPropagation();  // Prevent keyboard shortcuts while editing
    });
  }

  // -- Batch selection ----------------------------------------
  function clearSelection() {
    for (const idx of selectedSet) {
      if (idx < tableBody.children.length) {
        tableBody.children[idx].classList.remove('selected');
      }
    }
    selectedSet.clear();
  }

  function extendSelection(toIndex) {
    if (selectionAnchor < 0) selectionAnchor = currentIndex >= 0 ? currentIndex : 0;
    clearSelection();
    const lo = Math.min(selectionAnchor, toIndex);
    const hi = Math.max(selectionAnchor, toIndex);
    for (let i = lo; i <= hi; i++) {
      selectedSet.add(i);
      if (i < tableBody.children.length) {
        tableBody.children[i].classList.add('selected');
      }
    }
    // Move cursor to toIndex
    const prev = currentIndex;
    currentIndex = toIndex;
    if (prev >= 0 && prev < tableBody.children.length) {
      tableBody.children[prev].classList.remove('active');
    }
    if (toIndex < tableBody.children.length) {
      const row = tableBody.children[toIndex];
      row.classList.add('active');
      row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
    // Update player info
    const track = filteredTracks[toIndex];
    if (track) {
      nowArtist.textContent = track.artist || 'Unknown Artist';
      const ver = track.version_info ? ' (' + track.version_info + ')' : '';
      nowTitle.textContent = (track.title || 'Unknown Title') + ver;
      updateGenreSources(track);
    }
  }

  // -- Row selection ------------------------------------------
  function selectRow(index) {
    if (index < 0 || index >= filteredTracks.length) return;

    const prev = currentIndex;
    currentIndex = index;
    selectionAnchor = index;
    const track = filteredTracks[index];

    // Update visual state (swap classes instead of full re-render)
    if (prev >= 0 && prev < tableBody.children.length) {
      tableBody.children[prev].classList.remove('active');
    }
    if (index < tableBody.children.length) {
      const row = tableBody.children[index];
      row.classList.add('active');
      row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    // Player info
    nowArtist.textContent = track.artist || 'Unknown Artist';
    const ver = track.version_info ? ' (' + track.version_info + ')' : '';
    nowTitle.textContent = (track.title || 'Unknown Title') + ver;

    // Genre sources
    updateGenreSources(track);

    // Preload next track audio
    preloadNextTrack();
  }

  function updateGenreSources(track) {
    const parts = [];
    if (track.genres_musicbrainz)
      parts.push('<span class="gs gs-mb">MB: ' + escHtml(track.genres_musicbrainz) + '</span>');
    if (track.genres_lastfm)
      parts.push('<span class="gs gs-lfm">Last.fm: ' + escHtml(track.genres_lastfm) + '</span>');
    if (track.genres_soundcloud)
      parts.push('<span class="gs gs-sc">SC: ' + escHtml(track.genres_soundcloud) + '</span>');
    if (track.genres_beatport)
      parts.push('<span class="gs gs-bp">BP: ' + escHtml(track.genres_beatport) + '</span>');
    if (track.genre_suggest)
      parts.push('<span class="gs gs-suggest clickable" data-genre="' + escHtml(track.genre_suggest) + '">\u2192 ' + escHtml(track.genre_suggest) + '</span>');
    genreSources.innerHTML = parts.join('');

    // Make genre suggestion clickable to apply it
    genreSources.querySelectorAll('.gs-suggest.clickable').forEach(function(el) {
      el.addEventListener('click', function() { applyGenreSuggestionFromBadge(el); });
    });
  }

  function applyGenreSuggestionFromBadge(el) {
    const g = el.dataset.genre;
    if (!g || currentSource !== 'unsorted' || currentIndex < 0) return;
    const t = filteredTracks[currentIndex];
    t.genre = g;
    saveTrackField(t, 'genre', g);
    showToast('Genre: ' + g, '');
    // Update the dropdown in the current row
    const cols = COLUMNS.unsorted;
    const genreColIdx = cols.findIndex(function(c) { return c.key === 'genre'; });
    if (genreColIdx >= 0 && tableBody.children[currentIndex]) {
      const cell = tableBody.children[currentIndex].children[genreColIdx];
      const sel = cell.querySelector('select');
      if (sel) sel.value = g;
    }
  }

  function applyGenreSuggestion() {
    if (currentIndex < 0 || currentSource !== 'unsorted') return;
    const track = filteredTracks[currentIndex];
    const g = track.genre_suggest;
    if (!g) {
      showToast('No genre suggestion', '');
      return;
    }
    track.genre = g;
    saveTrackField(track, 'genre', g);
    showToast('Genre: ' + g, '');
    // Update the dropdown in the current row
    const cols = COLUMNS.unsorted;
    const genreColIdx = cols.findIndex(function(c) { return c.key === 'genre'; });
    if (genreColIdx >= 0 && tableBody.children[currentIndex]) {
      const cell = tableBody.children[currentIndex].children[genreColIdx];
      const sel = cell.querySelector('select');
      if (sel) sel.value = g;
    }
  }

  function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  // -- Audio playback -----------------------------------------
  let playingIndex = -1;

  function playTrack(index) {
    if (index < 0 || index >= filteredTracks.length) return;
    const track = filteredTracks[index];
    const path = audioPath(track);
    if (!path) {
      showToast('No audio file path', '');
      return;
    }

    selectRow(index);
    playingIndex = index;
    audio.src = '/api/audio?path=' + encodeURIComponent(path);
    audio.play().catch(function(e) {
      console.warn('Playback failed:', e);
      showToast('Playback failed \u2014 file may not exist or format unsupported', '');
    });

    // Load waveform
    loadWaveform(path);
  }

  function togglePlayPause() {
    if (!audio.paused) {
      audio.pause();
    } else if (audio.src && audio.currentTime > 0) {
      audio.play().catch(function() {});
    } else if (currentIndex >= 0) {
      playTrack(currentIndex);
    }
  }

  function stopPlayback() {
    audio.pause();
    audio.currentTime = 0;
    playingIndex = -1;
    nowIndicator.classList.remove('visible');
  }

  function navigateRow(delta, shiftHeld) {
    const newIndex = currentIndex + delta;
    if (newIndex < 0 || newIndex >= filteredTracks.length) return;

    if (shiftHeld) {
      extendSelection(newIndex);
    } else {
      clearSelection();
      selectRow(newIndex);
    }

    // Auto-play if enabled (only when not batch-selecting)
    if (autoPlay && !shiftHeld) {
      playTrack(newIndex);
    }
  }

  // -- Preload next track -------------------------------------
  function preloadNextTrack() {
    clearTimeout(_preloadTimer);
    _preloadTimer = setTimeout(function() {
      const nextIdx = currentIndex + 1;
      if (nextIdx >= filteredTracks.length) return;
      const path = audioPath(filteredTracks[nextIdx]);
      if (!path) return;
      const preloadAudio = new Audio();
      preloadAudio.preload = 'auto';
      preloadAudio.src = '/api/audio?path=' + encodeURIComponent(path);
      // Let it load into the browser cache, then discard
      preloadAudio.addEventListener('canplaythrough', function() {
        preloadAudio.src = '';
      }, { once: true });
    }, 300);
  }

  // -- Waveform -----------------------------------------------
  var _audioCtx = null;

  function getAudioContext() {
    if (!_audioCtx) _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    return _audioCtx;
  }

  function loadWaveform(path) {
    const url = '/api/audio?path=' + encodeURIComponent(path);
    fetch(url).then(function(r) { return r.arrayBuffer(); }).then(function(buf) {
      const ctx = getAudioContext();
      // Clone buffer for decoding (some browsers consume it)
      const clone = buf.slice(0);
      ctx.decodeAudioData(clone, function(audioBuffer) {
        drawWaveform(audioBuffer);
      }, function(err) {
        console.warn('Waveform decode error:', err);
      });
    }).catch(function(e) { console.warn('Waveform fetch error:', e); });
  }

  function drawWaveform(audioBuffer) {
    const canvas = waveformCanvas;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const data = audioBuffer.getChannelData(0);
    const step = Math.ceil(data.length / w);
    const mid = h / 2;

    ctx.strokeStyle = 'rgba(124, 108, 255, 0.5)';
    ctx.lineWidth = 1;
    ctx.beginPath();

    for (let x = 0; x < w; x++) {
      let min = 1.0, max = -1.0;
      const start = x * step;
      for (let j = 0; j < step && start + j < data.length; j++) {
        const val = data[start + j];
        if (val < min) min = val;
        if (val > max) max = val;
      }
      const yLow  = mid + min * mid;
      const yHigh = mid + max * mid;
      ctx.moveTo(x + 0.5, yLow);
      ctx.lineTo(x + 0.5, yHigh);
    }
    ctx.stroke();
  }

  function resizeWaveform() {
    // Re-draw if we have audio data
    if (audio.src && playingIndex >= 0) {
      const path = audioPath(filteredTracks[playingIndex]);
      if (path) loadWaveform(path);
    }
  }

  window.addEventListener('resize', resizeWaveform);

  // -- Audio events -------------------------------------------
  audio.addEventListener('play', function() {
    nowIndicator.classList.add('visible');
  });

  audio.addEventListener('pause', function() {
    nowIndicator.classList.remove('visible');
  });

  audio.addEventListener('timeupdate', function() {
    if (!audio.duration) return;
    const pct = (audio.currentTime / audio.duration) * 100;
    playerProgress.style.width = pct + '%';
    playerTime.textContent = fmtTime(audio.currentTime) + ' / ' + fmtTime(audio.duration);
  });

  audio.addEventListener('ended', function() {
    nowIndicator.classList.remove('visible');
    // Auto-advance
    if (currentIndex < filteredTracks.length - 1) {
      selectRow(currentIndex + 1);
      playTrack(currentIndex);
    }
  });

  // Progress bar interaction
  progressContainer.addEventListener('click', function(e) {
    if (!audio.duration) return;
    const rect = progressContainer.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    audio.currentTime = pct * audio.duration;
  });

  progressContainer.addEventListener('mousemove', function(e) {
    const rect = progressContainer.getBoundingClientRect();
    const pct = ((e.clientX - rect.left) / rect.width) * 100;
    progressHover.style.width = Math.min(100, Math.max(0, pct)) + '%';
  });

  progressContainer.addEventListener('mouseleave', function() {
    progressHover.style.width = '0%';
  });

  // -- Auto-play toggle ---------------------------------------
  autoPlayCheckbox.addEventListener('change', function() {
    autoPlay = autoPlayCheckbox.checked;
  });

  // -- Save (debounced, merging multiple field changes) -------
  function saveTrackField(track, key, value) {
    if (currentSource !== 'unsorted') return;
    const id = trackId(track);
    if (!id) return;

    if (!pendingSaves[id]) {
      pendingSaves[id] = { timer: null, fields: {} };
    }
    pendingSaves[id].fields[key] = value;

    clearTimeout(pendingSaves[id].timer);
    pendingSaves[id].timer = setTimeout(function() {
      const fields = pendingSaves[id].fields;
      delete pendingSaves[id];
      fetch('/api/tracks/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: id, fields: fields }),
      }).catch(function(e) { console.error('Save failed:', e); });
    }, SAVE_DEBOUNCE_MS);
  }

  // -- Undo ---------------------------------------------------
  function pushUndo(track, fields) {
    const entry = { trackId: trackId(track), prev: {} };
    for (const k of Object.keys(fields)) {
      entry.prev[k] = track[k] || '';
    }
    undoStack.push(entry);
    if (undoStack.length > MAX_UNDO) undoStack.shift();
  }

  function performUndo() {
    if (undoStack.length === 0) {
      showToast('Nothing to undo', '');
      return;
    }
    const entry = undoStack.pop();
    // Find the track in allTracks
    const track = allTracks.find(function(t) { return trackId(t) === entry.trackId; });
    if (!track) {
      showToast('Undo: track not found', '');
      return;
    }
    // Restore fields
    for (const [k, v] of Object.entries(entry.prev)) {
      track[k] = v;
      // Flush to server immediately
      const id = trackId(track);
      fetch('/api/tracks/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: id, fields: { [k]: v } }),
      }).catch(function(e) { console.error('Undo save failed:', e); });
    }

    // Always re-filter/re-render to handle filtered-out tracks
    applyFilters();

    // Try to navigate to the undone track in filtered view
    const idx = filteredTracks.indexOf(track);
    if (idx >= 0) {
      selectRow(idx);
    }

    showToast('Undo: ' + Object.keys(entry.prev).join(', '), '');
  }

  // -- Status / actions ---------------------------------------
  function setStatus(status) {
    if (currentSource !== 'unsorted') return;

    // Determine target tracks: batch selection or single
    const targets = [];
    if (selectedSet.size > 0) {
      for (const idx of selectedSet) {
        if (idx >= 0 && idx < filteredTracks.length) {
          targets.push({ idx: idx, track: filteredTracks[idx] });
        }
      }
    } else if (currentIndex >= 0 && currentIndex < filteredTracks.length) {
      targets.push({ idx: currentIndex, track: filteredTracks[currentIndex] });
    }

    if (targets.length === 0) return;

    // Apply to each target
    for (const { idx, track } of targets) {
      // Push undo BEFORE changing
      const undoFields = { status: status };
      if (AUTO_DEST[status]) undoFields.destination = AUTO_DEST[status];
      pushUndo(track, undoFields);

      track.status = status;
      saveTrackField(track, 'status', status);

      // Auto-dest
      if (AUTO_DEST[status]) {
        track.destination = AUTO_DEST[status];
        saveTrackField(track, 'destination', AUTO_DEST[status]);
      }

      // Re-render row status cell & classes
      const row = tableBody.children[idx];
      if (row) {
        row.classList.remove('status-accept', 'status-reject');
        if (status === 'accept') row.classList.add('status-accept');
        else if (status === 'reject') row.classList.add('status-reject');

        // Update status cell text
        const cols = COLUMNS[currentSource];
        const statusColIdx = cols.findIndex(function(c) { return c.key === 'status'; });
        if (statusColIdx >= 0 && row.children[statusColIdx]) {
          const td = row.children[statusColIdx];
          td.textContent = status || '\u2014';
          td.className = 'col-status' + (status ? ' ' + status : '');
        }

        // Update destination cell if auto-dest was applied
        if (AUTO_DEST[status]) {
          const destColIdx = cols.findIndex(function(c) { return c.key === 'destination'; });
          if (destColIdx >= 0 && row.children[destColIdx]) {
            const sel = row.children[destColIdx].querySelector('select');
            if (sel) sel.value = AUTO_DEST[status];
          }
        }
      }
    }

    const label = targets.length > 1
      ? status.toUpperCase() + ' \u00d7' + targets.length
      : status.toUpperCase();
    showToast(label, status);
    updateStats();

    // Clear batch selection after action
    clearSelection();

    // Auto-advance (single track only)
    if (targets.length === 1 && currentIndex < filteredTracks.length - 1) {
      setTimeout(function() { navigateRow(1, false); }, 150);
    }
  }

  function toggleDone() {
    if (currentIndex < 0 || currentSource !== 'unsorted') return;
    const track = filteredTracks[currentIndex];
    const newVal = track.done === 'TRUE' ? 'FALSE' : 'TRUE';
    track.done = newVal;
    saveTrackField(track, 'done', newVal);
    showToast(newVal === 'TRUE' ? 'Done \u2713' : 'Not done', '');

    const row = tableBody.children[currentIndex];
    if (row) {
      row.classList.toggle('is-done', newVal === 'TRUE');
      // Update checkbox
      const cols = COLUMNS[currentSource];
      const doneColIdx = cols.findIndex(function(c) { return c.key === 'done'; });
      if (doneColIdx >= 0 && row.children[doneColIdx]) {
        const cb = row.children[doneColIdx].querySelector('input[type="checkbox"]');
        if (cb) cb.checked = newVal === 'TRUE';
      }
    }
  }

  // -- Jump to next undecided ---------------------------------
  function jumpNextUndecided() {
    if (filteredTracks.length === 0) return;
    const start = currentIndex + 1;
    // Search forward from current position, then wrap
    for (let offset = 0; offset < filteredTracks.length; offset++) {
      const idx = (start + offset) % filteredTracks.length;
      const t = filteredTracks[idx];
      if (!t.status || t.status === '') {
        selectRow(idx);
        if (autoPlay) playTrack(idx);
        return;
      }
    }
    showToast('All tracks decided', '');
  }

  // -- Sort ---------------------------------------------------
  function handleSort(key) {
    if (key === '_index') return;
    if (sortKey === key) {
      sortDir *= -1;
    } else {
      sortKey = key;
      sortDir = 1;
    }
    applyFilters();
    // Re-select closest row
    if (currentIndex >= 0 && currentIndex < filteredTracks.length) {
      selectRow(currentIndex);
    }
  }

  // -- Keyboard -----------------------------------------------
  document.addEventListener('keydown', function(e) {
    const tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') {
      // Allow Escape to blur inputs
      if (e.code === 'Escape') { e.target.blur(); e.preventDefault(); }
      return;
    }

    switch (e.code) {
      case 'Space':
        e.preventDefault();
        togglePlayPause();
        break;

      case 'ArrowDown':
        e.preventDefault();
        navigateRow(1, e.shiftKey);
        break;

      case 'ArrowUp':
        e.preventDefault();
        navigateRow(-1, e.shiftKey);
        break;

      case 'Enter':
        e.preventDefault();
        if (currentIndex >= 0) playTrack(currentIndex);
        break;

      case 'Escape':
        stopPlayback();
        break;

      case 'KeyA':
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          setStatus('accept');
        }
        break;

      case 'KeyR':
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          setStatus('reject');
        }
        break;

      case 'KeyV':
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          setStatus('review');
        }
        break;

      case 'KeyG':
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          applyGenreSuggestion();
        }
        break;

      case 'KeyN':
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          jumpNextUndecided();
        }
        break;

      case 'KeyD':
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          toggleDone();
        }
        break;

      case 'KeyZ':
        if (e.ctrlKey || e.metaKey) {
          e.preventDefault();
          performUndo();
        }
        break;

      // Page navigation for speed
      case 'PageDown':
        e.preventDefault();
        navigateRow(10, e.shiftKey);
        break;

      case 'PageUp':
        e.preventDefault();
        navigateRow(-10, e.shiftKey);
        break;

      case 'Home':
        e.preventDefault();
        if (filteredTracks.length > 0) selectRow(0);
        break;

      case 'End':
        e.preventDefault();
        if (filteredTracks.length > 0) selectRow(filteredTracks.length - 1);
        break;
    }
  });

  // -- UI event listeners -------------------------------------
  sourceSelect.addEventListener('change', function() {
    currentIndex = -1;
    sortKey = null;
    sortDir = 1;
    selectedSet.clear();
    selectionAnchor = -1;
    undoStack = [];
    // Show/hide unsorted-only filters
    filterStatus.style.display = sourceSelect.value === 'unsorted' ? '' : 'none';
    filterDone.style.display = sourceSelect.value === 'unsorted' ? '' : 'none';
    loadTracks(sourceSelect.value);
  });

  searchInput.addEventListener('input', function() {
    applyFilters();
    if (filteredTracks.length > 0 && currentIndex < 0) selectRow(0);
  });

  filterStatus.addEventListener('change', function() {
    applyFilters();
    if (filteredTracks.length > 0) selectRow(0);
  });

  filterDone.addEventListener('change', function() {
    applyFilters();
    if (filteredTracks.length > 0) selectRow(0);
  });

  // -- Init ---------------------------------------------------
  Promise.all([loadGenres()]).then(function() {
    loadTracks('unsorted');
  });

})();
