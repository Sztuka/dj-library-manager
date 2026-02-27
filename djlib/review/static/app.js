/* ============================================================
 *  DJ Library — Review UI
 *  Keyboard-driven track preview & approval workflow
 *
 *  Space     = play / pause
 *  ↑ / ↓     = navigate rows
 *  Enter     = play selected
 *  A         = accept (+ auto-advance)
 *  R         = reject (+ auto-advance)
 *  V         = review (+ auto-advance)
 *  D         = toggle done
 *  Esc       = stop playback
 * ============================================================ */

(function () {
  'use strict';

  // ── State ─────────────────────────────────────────────────
  let allTracks = [];
  let filteredTracks = [];
  let genres = [];
  let currentIndex = -1;
  let currentSource = 'unsorted';
  let sortKey = null;
  let sortDir = 1;  // 1 = ascending, -1 = descending
  let savePending = null;  // debounce timer

  // ── DOM refs ──────────────────────────────────────────────
  const audio            = document.getElementById('audio-player');
  const tableHead        = document.getElementById('table-head');
  const tableBody        = document.getElementById('table-body');
  const emptyState       = document.getElementById('empty-state');
  const sourceSelect     = document.getElementById('source-select');
  const searchInput      = document.getElementById('search-input');
  const filterStatus     = document.getElementById('filter-status');
  const filterDone       = document.getElementById('filter-done');
  const trackCount       = document.getElementById('track-count');
  const nowArtist        = document.getElementById('now-artist');
  const nowTitle         = document.getElementById('now-title');
  const nowIndicator     = document.getElementById('now-playing-indicator');
  const playerProgress   = document.getElementById('player-progress');
  const progressHover    = document.getElementById('player-progress-hover');
  const progressContainer= document.getElementById('player-progress-container');
  const playerTime       = document.getElementById('player-time');
  const genreSources     = document.getElementById('genre-sources');
  const toast            = document.getElementById('toast');

  // ── Column definitions per source ─────────────────────────
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

  // ── Helpers ───────────────────────────────────────────────
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
    toast._timer = setTimeout(() => { toast.className = 'toast hidden'; }, 1200);
  }

  // ── Data loading ──────────────────────────────────────────
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

  // ── Filtering & sorting ───────────────────────────────────
  function applyFilters() {
    const q = searchInput.value.toLowerCase().trim();
    const sf = filterStatus.value;
    const df = filterDone.value;

    filteredTracks = allTracks.filter(t => {
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
      filteredTracks.sort((a, b) => {
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
  }

  // ── Table rendering ───────────────────────────────────────
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
        th.textContent += sortDir === 1 ? ' ▲' : ' ▼';
      }
      th.addEventListener('click', () => handleSort(col.key));
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
          cb.addEventListener('change', (e) => {
            e.stopPropagation();
            track[col.key] = cb.checked ? 'TRUE' : 'FALSE';
            saveTrackField(track, col.key, track[col.key]);
            tr.classList.toggle('is-done', cb.checked);
          });
          td.appendChild(cb);

        } else if (col.type === 'genre-select') {
          const sel = buildGenreSelect(track[col.key]);
          sel.addEventListener('change', (e) => {
            e.stopPropagation();
            track[col.key] = sel.value;
            saveTrackField(track, col.key, sel.value);
          });
          sel.addEventListener('mousedown', (e) => e.stopPropagation());
          td.appendChild(sel);

        } else if (col.type === 'dest-select') {
          const sel = buildDestSelect(track[col.key]);
          sel.addEventListener('change', (e) => {
            e.stopPropagation();
            track[col.key] = sel.value;
            saveTrackField(track, col.key, sel.value);
          });
          sel.addEventListener('mousedown', (e) => e.stopPropagation());
          td.appendChild(sel);

        } else if (col.type === 'editable') {
          td.textContent = track[col.key] || '';
          td.title = track[col.key] || '';
          td.classList.add('cell-editable');
          td.addEventListener('dblclick', (e) => {
            e.stopPropagation();
            startInlineEdit(td, track, col.key);
          });

        } else if (col.type === 'status-display') {
          td.textContent = track[col.key] || '—';
          td.classList.add('col-status');
          if (track[col.key]) td.classList.add(track[col.key]);

        } else {
          const raw = track[col.key] || '';
          td.textContent = col.fmt ? col.fmt(raw) : raw;
          td.title = raw;  // tooltip for truncated values
        }

        tr.appendChild(td);
      }

      // Row events
      tr.addEventListener('click', () => selectRow(i));
      tr.addEventListener('dblclick', () => { selectRow(i); playTrack(i); });

      frag.appendChild(tr);
    }

    tableBody.appendChild(frag);
  }

  function buildGenreSelect(currentValue) {
    const sel = document.createElement('select');
    sel.classList.add('inline-select');

    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = '—';
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

  // ── Inline editing (double-click on artist/title/version/year) ──
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
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      if (e.key === 'Escape') { input.value = oldVal; input.blur(); }
      e.stopPropagation();  // Prevent keyboard shortcuts while editing
    });
  }
  // ── Row selection ─────────────────────────────────────────
  function selectRow(index) {
    if (index < 0 || index >= filteredTracks.length) return;

    const prev = currentIndex;
    currentIndex = index;
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
    genreSources.querySelectorAll('.gs-suggest.clickable').forEach(el => {
      el.addEventListener('click', () => {
        const g = el.dataset.genre;
        if (!g || currentSource !== 'unsorted' || currentIndex < 0) return;
        const t = filteredTracks[currentIndex];
        t.genre = g;
        saveTrackField(t, 'genre', g);
        showToast('Genre: ' + g, '');
        // Update the dropdown in the current row
        const cols = COLUMNS.unsorted;
        const genreColIdx = cols.findIndex(c => c.key === 'genre');
        if (genreColIdx >= 0 && tableBody.children[currentIndex]) {
          const cell = tableBody.children[currentIndex].children[genreColIdx];
          const sel = cell.querySelector('select');
          if (sel) sel.value = g;
        }
      });
    });
  }

  function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  // ── Audio playback ────────────────────────────────────────
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
    audio.play().catch(e => {
      console.warn('Playback failed:', e);
      showToast('Playback failed — file may not exist or format unsupported', '');
    });
  }

  function togglePlayPause() {
    if (!audio.paused) {
      audio.pause();
    } else if (audio.src && audio.currentTime > 0) {
      audio.play().catch(() => {});
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

  function navigateRow(delta) {
    const newIndex = currentIndex + delta;
    if (newIndex < 0 || newIndex >= filteredTracks.length) return;

    const wasPlaying = !audio.paused;
    selectRow(newIndex);
    if (wasPlaying) {
      playTrack(newIndex);
    }
  }

  // ── Audio events ──────────────────────────────────────────
  audio.addEventListener('play', () => {
    nowIndicator.classList.add('visible');
  });

  audio.addEventListener('pause', () => {
    nowIndicator.classList.remove('visible');
  });

  audio.addEventListener('timeupdate', () => {
    if (!audio.duration) return;
    const pct = (audio.currentTime / audio.duration) * 100;
    playerProgress.style.width = pct + '%';
    playerTime.textContent = fmtTime(audio.currentTime) + ' / ' + fmtTime(audio.duration);
  });

  audio.addEventListener('ended', () => {
    nowIndicator.classList.remove('visible');
    // Auto-advance
    if (currentIndex < filteredTracks.length - 1) {
      selectRow(currentIndex + 1);
      playTrack(currentIndex);
    }
  });

  // Progress bar interaction
  progressContainer.addEventListener('click', (e) => {
    if (!audio.duration) return;
    const rect = progressContainer.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    audio.currentTime = pct * audio.duration;
  });

  progressContainer.addEventListener('mousemove', (e) => {
    const rect = progressContainer.getBoundingClientRect();
    const pct = ((e.clientX - rect.left) / rect.width) * 100;
    progressHover.style.width = Math.min(100, Math.max(0, pct)) + '%';
  });

  progressContainer.addEventListener('mouseleave', () => {
    progressHover.style.width = '0%';
  });

  // ── Save ──────────────────────────────────────────────────
  function saveTrackField(track, key, value) {
    if (currentSource !== 'unsorted') return;
    const id = trackId(track);
    if (!id) return;

    fetch('/api/tracks/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_id: id, fields: { [key]: value } }),
    }).catch(e => console.error('Save failed:', e));
  }

  function setStatus(status) {
    if (currentIndex < 0 || currentSource !== 'unsorted') return;
    const track = filteredTracks[currentIndex];
    track.status = status;
    saveTrackField(track, 'status', status);

    showToast(status.toUpperCase(), status);

    // Re-render current row status cell & classes
    const row = tableBody.children[currentIndex];
    if (row) {
      row.classList.remove('status-accept', 'status-reject');
      if (status === 'accept') row.classList.add('status-accept');
      else if (status === 'reject') row.classList.add('status-reject');

      // Update status cell text
      const cols = COLUMNS[currentSource];
      const statusColIdx = cols.findIndex(c => c.key === 'status');
      if (statusColIdx >= 0 && row.children[statusColIdx]) {
        const td = row.children[statusColIdx];
        td.textContent = status || '—';
        td.className = 'col-status' + (status ? ' ' + status : '');
      }
    }

    // Auto-advance
    if (currentIndex < filteredTracks.length - 1) {
      setTimeout(() => navigateRow(1), 150);
    }
  }

  function toggleDone() {
    if (currentIndex < 0 || currentSource !== 'unsorted') return;
    const track = filteredTracks[currentIndex];
    const newVal = track.done === 'TRUE' ? 'FALSE' : 'TRUE';
    track.done = newVal;
    saveTrackField(track, 'done', newVal);
    showToast(newVal === 'TRUE' ? 'Done ✓' : 'Not done', '');

    const row = tableBody.children[currentIndex];
    if (row) {
      row.classList.toggle('is-done', newVal === 'TRUE');
      // Update checkbox
      const cols = COLUMNS[currentSource];
      const doneColIdx = cols.findIndex(c => c.key === 'done');
      if (doneColIdx >= 0 && row.children[doneColIdx]) {
        const cb = row.children[doneColIdx].querySelector('input[type="checkbox"]');
        if (cb) cb.checked = newVal === 'TRUE';
      }
    }
  }

  // ── Sort ──────────────────────────────────────────────────
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

  // ── Keyboard ──────────────────────────────────────────────
  document.addEventListener('keydown', (e) => {
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
        navigateRow(1);
        break;

      case 'ArrowUp':
        e.preventDefault();
        navigateRow(-1);
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

      case 'KeyD':
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          toggleDone();
        }
        break;

      // Page navigation for speed
      case 'PageDown':
        e.preventDefault();
        navigateRow(10);
        break;

      case 'PageUp':
        e.preventDefault();
        navigateRow(-10);
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

  // ── UI event listeners ────────────────────────────────────
  sourceSelect.addEventListener('change', () => {
    currentIndex = -1;
    sortKey = null;
    sortDir = 1;
    // Show/hide unsorted-only filters
    filterStatus.style.display = sourceSelect.value === 'unsorted' ? '' : 'none';
    filterDone.style.display = sourceSelect.value === 'unsorted' ? '' : 'none';
    loadTracks(sourceSelect.value);
  });

  searchInput.addEventListener('input', () => {
    applyFilters();
    if (filteredTracks.length > 0 && currentIndex < 0) selectRow(0);
  });

  filterStatus.addEventListener('change', () => {
    applyFilters();
    if (filteredTracks.length > 0) selectRow(0);
  });

  filterDone.addEventListener('change', () => {
    applyFilters();
    if (filteredTracks.length > 0) selectRow(0);
  });

  // ── Init ──────────────────────────────────────────────────
  Promise.all([loadGenres()]).then(() => {
    loadTracks('unsorted');
  });

})();
