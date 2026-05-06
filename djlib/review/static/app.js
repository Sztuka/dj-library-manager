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
 *  Ctrl/Cmd+K   = toggle AI Chat panel
 * ============================================================ */

(function () {
  "use strict";

  // -- State --------------------------------------------------
  let allTracks = [];
  let filteredTracks = [];
  let genres = [];
  let currentIndex = -1;
  let currentSource = "unsorted";
  let sortKey = null;
  let sortDir = 1; // 1 = ascending, -1 = descending

  // Helper: is the current source editable?
  function isEditableSource() {
    return currentSource === "unsorted" || currentSource === "library-review" || currentSource === "library-fix";
  }

  // Auto-play on navigation
  let autoPlay = false;

  // ── Ghost-row review state ──────────────────────────────────────────────────
  var ghostReview = {
    active: false,
    jobId: null,
    total: 0,
    done: 0,
    locked: false,          // true while streaming: block edits + sort changes
    pollTimer: null,
    ticked: {},             // { track_id: { field: bool } }
    proposals: {},          // { track_id: { field: {value,source,confidence,was} } }
    filter: "all",          // "all" | "conflicts" | "low"
    autoThreshold: parseFloat(localStorage.getItem("ghost-auto-accept") || "0.85"),
  };

  // Auto-destination mapping
  const AUTO_DEST = { accept: "library", reject: "reject" };

  // Batch selection
  let selectedSet = new Set();
  let selectionAnchor = -1;

  // Undo stack (max 50)
  const MAX_UNDO = 50;
  let undoStack = [];

  // Debounced saves: { trackId: { timer, fields } }
  let pendingSaves = {};
  const SAVE_DEBOUNCE_MS = 80;

  // Preload debounce
  let _preloadTimer = null;

  // Library index for "already in library" matching
  let libraryIndex = new Set();

  // -- DOM refs -----------------------------------------------
  const audio = document.getElementById("audio-player");
  const tableHead = document.getElementById("table-head");
  const tableBody = document.getElementById("table-body");
  const emptyState = document.getElementById("empty-state");
  const sourceSelect = document.getElementById("source-select");
  const searchInput = document.getElementById("search-input");
  const filterStatus = document.getElementById("filter-status");
  const filterDone = document.getElementById("filter-done");
  const filterBpm = document.getElementById("filter-bpm");
  const filterKey = document.getElementById("filter-key");
  const filterRating = document.getElementById("filter-rating");
  const trackCount = document.getElementById("track-count");
  const statsBar = document.getElementById("stats-bar");
  const autoPlayCheckbox = document.getElementById("auto-play-checkbox");
  const nowArtist = document.getElementById("now-artist");
  const nowTitle = document.getElementById("now-title");
  const nowIndicator = document.getElementById("now-playing-indicator");
  const playerProgress = document.getElementById("player-progress");
  const progressHover = document.getElementById("player-progress-hover");
  const progressContainer = document.getElementById(
    "player-progress-container",
  );
  const waveformCanvas = document.getElementById("waveform-canvas");
  const playerTime = document.getElementById("player-time");
  const genreSources = document.getElementById("genre-sources");
  const toast = document.getElementById("toast");
  const contextMenu = document.getElementById("context-menu");
  const nowFilename = document.getElementById("now-filename");
  const aiBanner = document.getElementById("ai-banner");
  const aiBannerGenre = document.getElementById("ai-banner-genre");
  const aiBannerConfidence = document.getElementById("ai-banner-confidence");
  const aiBannerReasoning = document.getElementById("ai-banner-reasoning");
  const aiBannerAccept = document.getElementById("ai-banner-accept");
  const aiBannerDismiss = document.getElementById("ai-banner-dismiss");
  const ctxAiSuggest = document.getElementById("ctx-ai-suggest");
  const enrichBanner = document.getElementById("enrich-banner");
  const enrichBannerGenre = document.getElementById("enrich-banner-genre");
  const enrichBannerConf = document.getElementById("enrich-banner-confidence");
  const enrichBannerSources = document.getElementById("enrich-banner-sources");
  const enrichBannerAccept = document.getElementById("enrich-banner-accept");
  const enrichBannerDismiss = document.getElementById("enrich-banner-dismiss");
  const enrichBannerSwap = document.getElementById("enrich-banner-swap");
  const identifyBanner = document.getElementById("identify-banner");
  const identifyBannerArtist = document.getElementById(
    "identify-banner-artist",
  );
  const identifyBannerTitle = document.getElementById("identify-banner-title");
  const identifyBannerVersion = document.getElementById(
    "identify-banner-version",
  );
  const identifyBannerYear = document.getElementById("identify-banner-year");
  const identifyBannerConfidence = document.getElementById(
    "identify-banner-confidence",
  );
  const identifyBannerReasoning = document.getElementById(
    "identify-banner-reasoning",
  );
  const identifyBannerAccept = document.getElementById(
    "identify-banner-accept",
  );
  const identifyBannerDismiss = document.getElementById(
    "identify-banner-dismiss",
  );
  const classifyBanner = document.getElementById("classify-banner");
  const classifyBannerArtist = document.getElementById(
    "classify-banner-artist",
  );
  const classifyBannerTitle = document.getElementById("classify-banner-title");
  const classifyBannerVersion = document.getElementById(
    "classify-banner-version",
  );
  const classifyBannerGenre = document.getElementById("classify-banner-genre");
  const classifyBannerConfidence = document.getElementById(
    "classify-banner-confidence",
  );
  const classifyBannerReasoning = document.getElementById(
    "classify-banner-reasoning",
  );
  const classifyBannerAccept = document.getElementById(
    "classify-banner-accept",
  );
  const classifyBannerDismiss = document.getElementById(
    "classify-banner-dismiss",
  );
  const aiChatPanel = document.getElementById("ai-chat-panel");
  const aiChatTitle = document.getElementById("ai-chat-title");
  const aiChatMessages = document.getElementById("ai-chat-messages");
  const aiChatInput = document.getElementById("ai-chat-input");
  const aiChatSend = document.getElementById("ai-chat-send");
  const aiChatClose = document.getElementById("ai-chat-close");
  const aiChatMinimize = document.getElementById("ai-chat-minimize");
  const aiChatPrompts = document.getElementById("ai-chat-prompts");
  const aiChatDragHandle = document.getElementById("ai-chat-drag-handle");
  const urlScrapeBanner = document.getElementById("url-scrape-banner");
  const reviewToolbar = document.getElementById("review-toolbar");
  const reviewProgress = document.getElementById("review-progress");
  const reviewTickedCount = document.getElementById("review-ticked-count");
  const reviewApplyBtn = document.getElementById("review-apply-btn");
  const reviewCancelBtn = document.getElementById("review-cancel-btn");
  const reviewAutoThresholdInput = document.getElementById("review-auto-threshold");
  const urlScrapeBannerArtist = document.getElementById(
    "url-scrape-banner-artist",
  );
  const urlScrapeBannerTitle = document.getElementById(
    "url-scrape-banner-title",
  );
  const urlScrapeBannerVersion = document.getElementById(
    "url-scrape-banner-version",
  );
  const urlScrapeBannerGenre = document.getElementById(
    "url-scrape-banner-genre",
  );
  const urlScrapeBannerYear = document.getElementById("url-scrape-banner-year");
  const urlScrapeBannerSource = document.getElementById(
    "url-scrape-banner-source",
  );
  const urlScrapeBannerAccept = document.getElementById(
    "url-scrape-banner-accept",
  );
  const urlScrapeBannerDismiss = document.getElementById(
    "url-scrape-banner-dismiss",
  );
  const urlInputOverlay = document.getElementById("url-input-overlay");
  const urlInputField = document.getElementById("url-input-field");
  const urlInputCancel = document.getElementById("url-input-cancel");
  const urlInputSubmit = document.getElementById("url-input-submit");

  // Batch bar
  const batchBar = document.getElementById("batch-bar");
  const batchClear = document.getElementById("batch-clear");
  const batchCount = document.getElementById("batch-count");
  const batchGenre = document.getElementById("batch-genre");
  const batchYear = document.getElementById("batch-year");
  const batchArtist = document.getElementById("batch-artist");
  const batchStatus = document.getElementById("batch-status");
  const batchDest = document.getElementById("batch-dest");
  const batchRating = document.getElementById("batch-rating");
  const batchDone = document.getElementById("batch-done");
  const batchApply = document.getElementById("batch-apply");
  const batchApplyCount = document.getElementById("batch-apply-count");

  // AI availability (checked once on load)
  let aiAvailable = false;
  let aiPending = false; // prevents double-clicks
  let aiBannerTrack = null; // track the banner is showing for
  let enrichPending = false;
  let enrichBannerTrack = null;
  let identifyPending = false;
  let identifyBannerTrack = null;
  let classifyPending = false;
  let classifyBannerTrack = null;
  let chatPending = false;
  let chatTrack = null; // track the chat panel is open for
  let scrapePending = false;
  let scrapeTrack = null; // track for URL scrape

  // -- Column definitions per source --------------------------
  const COLUMNS = {
    unsorted: [
      { key: "_select", label: "", width: "28px", type: "row-select" },
      { key: "_index", label: "#", width: "36px" },
      { key: "artist", label: "Artist", width: "15%", type: "editable" },
      { key: "title", label: "Title", width: "16%", type: "editable" },
      { key: "version_info", label: "Version", width: "10%", type: "editable" },
      { key: "_in_library", label: "Lib", width: "36px", type: "in-library" },
      { key: "genre", label: "Genre", width: "11%", type: "genre-select" },
      {
        key: "year",
        label: "Year",
        width: "48px",
        type: "editable",
        cls: "col-bpm",
      },
      { key: "bpm", label: "BPM", width: "46px", cls: "col-bpm" },
      { key: "key_camelot", label: "Key", width: "40px", cls: "col-key" },
      { key: "rating", label: "Rating", width: "72px", type: "rating" },
      { key: "destination", label: "Dest", width: "72px", type: "dest-select" },
      { key: "status", label: "Status", width: "68px", type: "status-display" },
      { key: "done", label: "\u2713", width: "32px", type: "checkbox" },
    ],
    library: [
      { key: "_index", label: "#", width: "36px" },
      { key: "artist", label: "Artist", width: "18%" },
      { key: "title", label: "Title", width: "22%" },
      { key: "bpm", label: "BPM", width: "50px", cls: "col-bpm" },
      { key: "key", label: "Key", width: "44px", cls: "col-key" },
      {
        key: "duration_seconds",
        label: "Dur",
        width: "55px",
        cls: "col-bpm",
        fmt: fmtDuration,
      },
      { key: "rating", label: "Rating", width: "72px", type: "rating" },
      { key: "color", label: "Clr", width: "32px", type: "color-dot" },
      { key: "cue_count", label: "Cues", width: "38px", cls: "col-bpm" },
      { key: "play_count", label: "Plays", width: "44px", cls: "col-bpm" },
      {
        key: "external_source",
        label: "Src",
        width: "50px",
        type: "source-badge",
      },
      { key: "date_added", label: "Added", width: "90px" },
    ],
    processed: [
      { key: "_index", label: "#", width: "36px" },
      { key: "artist", label: "Artist", width: "16%" },
      { key: "title", label: "Title", width: "20%" },
      { key: "bpm", label: "BPM", width: "46px", cls: "col-bpm" },
      { key: "key", label: "Key", width: "40px", cls: "col-key" },
      { key: "rating", label: "Rating", width: "72px", type: "rating" },
      { key: "date_added", label: "Added", width: "90px" },
      { key: "destination", label: "Dest", width: "68px", type: "dest-badge" },
      { key: "play_count", label: "Plays", width: "44px", cls: "col-bpm" },
      {
        key: "in_dj_software",
        label: "DJ",
        width: "42px",
        type: "in-dj-badge",
      },
    ],
    "library-review": [
      { key: "_select", label: "", width: "28px", type: "row-select" },
      { key: "_index", label: "#", width: "36px" },
      { key: "artist", label: "Artist", width: "14%", type: "editable" },
      { key: "title", label: "Title", width: "15%", type: "editable" },
      { key: "version_info", label: "Version", width: "10%", type: "editable" },
      { key: "genre", label: "Genre", width: "10%", type: "genre-select" },
      {
        key: "year",
        label: "Year",
        width: "48px",
        type: "editable",
        cls: "col-bpm",
      },
      { key: "bpm", label: "BPM", width: "46px", cls: "col-bpm" },
      { key: "key_camelot", label: "Key", width: "40px", cls: "col-key" },
      { key: "rating", label: "Rating", width: "72px", type: "rating" },
      { key: "rekordbox_id", label: "RB", width: "36px", cls: "col-bpm" },
      { key: "traktor_id", label: "TK", width: "36px", cls: "col-bpm" },
      { key: "done", label: "\u2713", width: "32px", type: "checkbox" },
    ],
    "library-fix": [
      { key: "_select", label: "", width: "28px", type: "row-select" },
      { key: "_index", label: "#", width: "32px" },
      { key: "artist", label: "Artist", width: "11%", type: "editable" },
      { key: "title", label: "Title", width: "12%", type: "editable" },
      { key: "version_info", label: "Version", width: "8%", type: "editable" },
      { key: "genre", label: "Genre", width: "9%", type: "genre-select" },
      { key: "year", label: "Year", width: "40px", type: "editable", cls: "col-bpm" },
      { key: "bpm", label: "BPM", width: "40px", cls: "col-bpm" },
      { key: "key_camelot", label: "Key", width: "36px", cls: "col-key" },
      { key: "ai_artist", label: "AI Artist", width: "11%", cls: "col-ai" },
      { key: "ai_title", label: "AI Title", width: "12%", cls: "col-ai" },
      { key: "ai_version", label: "AI Ver", width: "8%", cls: "col-ai" },
      { key: "ai_genre", label: "AI Genre", width: "8%", cls: "col-ai" },
      { key: "ai_confidence", label: "Conf", width: "40px", cls: "col-ai col-bpm" },
      { key: "status", label: "Status", width: "60px", type: "status-badge" },
      { key: "done", label: "\u2713", width: "32px", type: "checkbox" },
    ],
  };

  // -- Helpers ------------------------------------------------
  function fmtTime(sec) {
    if (!sec || isNaN(sec)) return "0:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return m + ":" + String(s).padStart(2, "0");
  }

  function fmtDuration(val) {
    const n = parseFloat(val);
    if (!n || isNaN(n)) return "";
    return fmtTime(n);
  }

  function audioPath(track) {
    return track.file_path || track.old_full_path || "";
  }

  function trackId(track) {
    return track.track_id || track.file_hash || "";
  }

  function showToast(msg, cls) {
    toast.textContent = msg;
    toast.className = "toast visible" + (cls ? " toast-" + cls : "");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function () {
      toast.className = "toast hidden";
    }, 1200);
  }

  // -- Rating helpers -----------------------------------------
  function ratingToStars(val) {
    const n = parseFloat(val);
    if (!n || isNaN(n) || n <= 0) return "";
    const full = Math.round(n);
    let html = "";
    for (let i = 1; i <= 5; i++) {
      html +=
        i <= full
          ? '<span class="star-on">\u2605</span>'
          : '<span class="star-off">\u2605</span>';
    }
    return html;
  }

  // -- Interactive rating helper (click to set stars) --------
  function handleRatingClick(td, track, key, e) {
    var rect = td.getBoundingClientRect();
    var x = e.clientX - rect.left;
    var starWidth = rect.width / 5;
    var clicked = Math.min(5, Math.max(1, Math.ceil(x / starWidth)));
    var current = parseInt(track[key]) || 0;
    // Click same star again → clear rating
    var newVal = clicked === current ? 0 : clicked;
    track[key] = String(newVal);
    td.innerHTML = ratingToStars(newVal);
    saveTrackField(track, key, String(newVal));
    showToast(newVal > 0 ? "Rating: " + "\u2605".repeat(newVal) : "Rating cleared", "");
  }

  function setRatingForCurrent(stars) {
    if (!isEditableSource()) return;
    if (currentIndex < 0 || currentIndex >= filteredTracks.length) return;
    var track = filteredTracks[currentIndex];
    var current = parseInt(track.rating) || 0;
    var newVal = stars === current ? 0 : stars;
    track.rating = String(newVal);
    // Update the cell in the DOM
    var row = getDataRow(currentIndex);
    if (row) {
      var cells = row.querySelectorAll(".col-rating");
      if (cells.length) cells[0].innerHTML = ratingToStars(newVal);
    }
    saveTrackField(track, "rating", String(newVal));
    showToast(newVal > 0 ? "Rating: " + "\u2605".repeat(newVal) : "Rating cleared", "");
  }

  // -- Source badge helper ------------------------------------
  function sourceBadgeHtml(val) {
    const v = (val || "").toLowerCase();
    if (v === "rekordbox+traktor" || v === "traktor+rekordbox")
      return '<span class="badge-source src-both">RB+TR</span>';
    if (v === "rekordbox")
      return '<span class="badge-source src-rekordbox">RB</span>';
    if (v === "traktor")
      return '<span class="badge-source src-traktor">TR</span>';
    return escHtml(val || "");
  }

  // -- Destination badge helper (processed tab) ---------------
  function destBadgeHtml(val) {
    const v = (val || "").toLowerCase();
    if (v === "library")
      return '<span class="badge-dest dest-library">library</span>';
    if (v === "archive")
      return '<span class="badge-dest dest-archive">archive</span>';
    if (v === "rejected")
      return '<span class="badge-dest dest-rejected">rejected</span>';
    if (v === "mixes")
      return '<span class="badge-dest dest-mixes">mixes</span>';
    return escHtml(val || "");
  }

  // -- In-DJ-software badge helper ----------------------------
  function inDjBadgeHtml(val) {
    if (val === "yes") return '<span class="badge-in-lib">YES</span>';
    return '<span class="badge-no-dj">\u2014</span>';
  }

  // -- Color dot helper ---------------------------------------
  function colorDotHtml(val) {
    const n = Math.round(parseFloat(val) || 0);
    const cls = n >= 1 && n <= 8 ? "c-" + n : "c-none";
    return (
      '<span class="color-dot ' + cls + '" title="Color ' + n + '"></span>'
    );
  }

  // -- "In library" check -------------------------------------
  function isInLibrary(track) {
    const a = (track.artist || "").trim().toLowerCase();
    const t = (track.title || "").trim().toLowerCase();
    if (!a || !t) return false;
    return libraryIndex.has(a + "::" + t);
  }

  // -- Data loading -------------------------------------------
  async function loadLibraryIndex() {
    try {
      const resp = await fetch("/api/library-index");
      const keys = await resp.json();
      libraryIndex = new Set(keys);
    } catch (e) {
      libraryIndex = new Set();
      console.warn("Failed to load library index:", e);
    }
  }

  let _loadError = false;

  async function loadTracks(source) {
    currentSource = source;
    _loadError = false;
    try {
      const resp = await fetch("/api/tracks?source=" + source);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      allTracks = await resp.json();
    } catch (e) {
      allTracks = [];
      _loadError = true;
      console.error("Failed to load tracks:", e);
    }
    currentIndex = -1;
    selectedSet.clear();
    selectionAnchor = -1;
    undoStack = [];
    // Exit review mode when switching sources (ghost rows belong to unsorted only)
    if (ghostReview.active) exitReviewMode(true);
    // Reset batch bar inputs to avoid ghost values across source switches
    if (batchGenre) batchGenre.value = "";
    if (batchYear) batchYear.value = "";
    if (batchArtist) batchArtist.value = "";
    if (batchStatus) batchStatus.value = "";
    if (batchDest) batchDest.value = "";
    if (batchRating) batchRating.value = "";
    if (batchDone) batchDone.value = "";
    updateBatchBar();
    applyFilters();
    // Auto-select first row
    if (filteredTracks.length > 0) {
      selectRow(0);
    }
  }

  async function loadGenres() {
    try {
      const resp = await fetch("/api/genres");
      genres = await resp.json();
    } catch (e) {
      genres = [];
    }
    populateBatchGenre();
  }

  // -- Populate key filter from library data ------------------
  function camelotOrder(k) {
    // Sort keys in Camelot wheel order: 1A, 1B, 2A, 2B, ..., 12A, 12B
    const m = k.match(/^(\d+)([ABdm])/i);
    if (!m) return 999;
    const num = parseInt(m[1], 10);
    const letter = m[2].toUpperCase();
    // A/d = minor, B/m = major; sort A before B within same number
    const sub = letter === "A" || letter === "D" ? 0 : 1;
    return num * 2 + sub;
  }

  function populateKeyFilter() {
    const keys = new Set();
    for (const t of allTracks) {
      const k = (t.key || "").trim();
      if (k) keys.add(k);
    }
    const sorted = Array.from(keys).sort(function (a, b) {
      return camelotOrder(a) - camelotOrder(b);
    });
    filterKey.innerHTML = '<option value="">Key: All</option>';
    for (const k of sorted) {
      const o = document.createElement("option");
      o.value = k;
      o.textContent = k;
      filterKey.appendChild(o);
    }
  }

  // -- Filter visibility --------------------------------------
  function updateFilterVisibility() {
    const isLib = currentSource === "library";
    const isUnsorted = currentSource === "unsorted";
    // Unsorted-only filters (hidden on library & processed)
    filterStatus.style.display = isUnsorted ? "" : "none";
    filterDone.style.display = isUnsorted ? "" : "none";
    // Library-only filters
    filterBpm.style.display = isLib ? "" : "none";
    filterKey.style.display = isLib ? "" : "none";
    filterRating.style.display = isLib || isUnsorted ? "" : "none";
  }

  // -- Filtering & sorting ------------------------------------
  function applyFilters() {
    const q = searchInput.value.toLowerCase().trim();
    const sf = filterStatus.value;
    const df = filterDone.value;
    const bf = filterBpm.value;
    const kf = filterKey.value;
    const rf = filterRating.value;
    const isLib = currentSource === "library";

    filteredTracks = allTracks.filter(function (t) {
      // Text search
      if (q) {
        const hay = [
          t.artist,
          t.title,
          t.version_info,
          t.genre,
          t.tag_genre_original,
          t.key,
          t.external_source,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!hay.includes(q)) return false;
      }
      // Unsorted-specific filters
      if (!isLib) {
        if (sf) {
          if (sf === "undecided") {
            if (t.status && t.status !== "") return false;
          } else {
            if (t.status !== sf) return false;
          }
        }
        if (df) {
          if (t.done !== df) return false;
        }
      }
      // Library-specific filters
      if (isLib) {
        // BPM range
        if (bf) {
          const bpm = parseFloat(t.bpm) || 0;
          if (bf === "0-100" && !(bpm > 0 && bpm < 100)) return false;
          if (bf === "100-120" && !(bpm >= 100 && bpm < 120)) return false;
          if (bf === "120-130" && !(bpm >= 120 && bpm < 130)) return false;
          if (bf === "130-140" && !(bpm >= 130 && bpm < 140)) return false;
          if (bf === "140-160" && !(bpm >= 140 && bpm < 160)) return false;
          if (bf === "160+" && !(bpm >= 160)) return false;
        }
        // Key
        if (kf) {
          if ((t.key || "").trim() !== kf) return false;
        }
        // Rating (library)
        if (rf) {
          const rat = parseFloat(t.rating) || 0;
          if (rf === "unrated") {
            if (rat > 0) return false;
          } else {
            const minRat = parseFloat(rf);
            if (rf === "5") {
              if (Math.round(rat) !== 5) return false;
            } else {
              if (rat < minRat) return false;
            }
          }
        }
      }
      // Rating filter (unsorted — outside isLib block)
      if (rf && !isLib) {
        const rat = parseFloat(t.rating) || 0;
        if (rf === "unrated") {
          if (rat > 0) return false;
        } else {
          const minRat = parseFloat(rf);
          if (rf === "5") {
            if (Math.round(rat) !== 5) return false;
          } else {
            if (rat < minRat) return false;
          }
        }
      }
      return true;
    });

    // Sort
    if (sortKey && sortKey !== "_index") {
      filteredTracks.sort(function (a, b) {
        const va = (a[sortKey] || "").toString();
        const vb = (b[sortKey] || "").toString();
        const na = parseFloat(va),
          nb = parseFloat(vb);
        if (!isNaN(na) && !isNaN(nb)) return (na - nb) * sortDir;
        return (
          va.localeCompare(vb, undefined, { sensitivity: "base" }) * sortDir
        );
      });
    }

    renderTable();
    trackCount.textContent = filteredTracks.length + " / " + allTracks.length;
    var showEmpty = filteredTracks.length === 0;
    emptyState.style.display = showEmpty ? "" : "none";
    document.getElementById("tracks-table").style.display = showEmpty
      ? "none"
      : "";
    // Update empty state message based on error vs no data
    if (showEmpty) {
      var emptyP = emptyState.querySelector("p");
      var emptyHint = emptyState.querySelector(".dim");
      if (_loadError) {
        emptyP.textContent =
          "\u26a0\ufe0f Connection error — server not responding";
        emptyHint.innerHTML =
          "Start the server: <code>djlib review</code> or <code>python -m djlib.review.server</code>";
      } else if (allTracks.length === 0) {
        emptyP.textContent = "No tracks found.";
        emptyHint.innerHTML =
          "Run <code>djlib scan</code> then <code>djlib enrich-online</code> to populate unsorted.csv";
      } else {
        emptyP.textContent = "No tracks match current filters.";
        emptyHint.textContent = "Try clearing search or filters.";
      }
    }
    updateStats();
  }

  // -- Stats bar ----------------------------------------------
  function updateStats() {
    if (currentSource === "unsorted") {
      var acc = 0,
        rej = 0,
        rev = 0,
        und = 0;
      for (var i = 0; i < allTracks.length; i++) {
        var t = allTracks[i];
        if (t.status === "accept") acc++;
        else if (t.status === "reject") rej++;
        else if (t.status === "review") rev++;
        else und++;
      }
      statsBar.innerHTML =
        '<span class="stat-accept">' +
        acc +
        " acc</span> \u00b7 " +
        '<span class="stat-reject">' +
        rej +
        " rej</span> \u00b7 " +
        '<span class="stat-review">' +
        rev +
        " rev</span> \u00b7 " +
        '<span class="stat-undecided">' +
        und +
        " todo</span>";
    } else if (currentSource === "library") {
      // Library stats: source breakdown, rated, BPM range
      var srcBoth = 0,
        srcRb = 0,
        srcTr = 0,
        rated = 0;
      var bpmMin = Infinity,
        bpmMax = 0,
        bpmCount = 0;
      for (var i = 0; i < allTracks.length; i++) {
        var t = allTracks[i];
        var src = (t.external_source || "").toLowerCase();
        if (src.indexOf("rekordbox") >= 0 && src.indexOf("traktor") >= 0)
          srcBoth++;
        else if (src.indexOf("rekordbox") >= 0) srcRb++;
        else if (src.indexOf("traktor") >= 0) srcTr++;
        var rat = parseFloat(t.rating) || 0;
        if (rat > 0) rated++;
        var bpm = parseFloat(t.bpm) || 0;
        if (bpm > 0) {
          if (bpm < bpmMin) bpmMin = bpm;
          if (bpm > bpmMax) bpmMax = bpm;
          bpmCount++;
        }
      }
      var parts = [];
      parts.push('<span class="badge-source src-both">RB+TR</span> ' + srcBoth);
      parts.push('<span class="badge-source src-rekordbox">RB</span> ' + srcRb);
      parts.push('<span class="badge-source src-traktor">TR</span> ' + srcTr);
      parts.push(
        '\u00b7 <span class="star-on">\u2605</span> ' + rated + " rated",
      );
      if (bpmCount > 0) {
        parts.push(
          "\u00b7 BPM " + Math.round(bpmMin) + "\u2013" + Math.round(bpmMax),
        );
      }
      statsBar.innerHTML =
        '<span class="stat-lib-sources">' + parts.join(" ") + "</span>";
    } else if (currentSource === "processed") {
      // Processed stats: total, in library, rated, dest breakdown
      var inLib = 0,
        prRated = 0,
        prLib = 0,
        prArch = 0,
        prRej = 0;
      for (var i = 0; i < allTracks.length; i++) {
        var t = allTracks[i];
        if (t.in_dj_software === "yes") inLib++;
        var rat = parseFloat(t.rating) || 0;
        if (rat > 0) prRated++;
        var dest = (t.destination || "").toLowerCase();
        if (dest === "library") prLib++;
        else if (dest === "archive") prArch++;
        else if (dest === "rejected") prRej++;
      }
      var prParts = [];
      prParts.push(
        '<span class="stat-processed-total">' +
          allTracks.length +
          " processed</span>",
      );
      prParts.push(
        '<span class="badge-dest dest-library">' + prLib + " library</span>",
      );
      if (prArch)
        prParts.push(
          '<span class="badge-dest dest-archive">' + prArch + " archive</span>",
        );
      if (prRej)
        prParts.push(
          '<span class="badge-dest dest-rejected">' +
            prRej +
            " rejected</span>",
        );
      prParts.push(
        '\u00b7 <span class="badge-in-lib">' + inLib + "</span> in DJ software",
      );
      prParts.push(
        '\u00b7 <span class="star-on">\u2605</span> ' + prRated + " rated",
      );
      statsBar.innerHTML = prParts.join(" ");
    } else {
      statsBar.innerHTML = "";
    }
  }

  // -- Table rendering ----------------------------------------
  // ── Ghost-row navigation helper ────────────────────────────────────────────
  // Returns the i-th data row (skipping ghost rows) from tableBody.
  // Use instead of tableBody.children[i] everywhere navigation touches the DOM,
  // so that interspersed ghost rows don't shift the index-to-row mapping.
  function getDataRow(index) {
    if (index < 0) return null;
    var count = 0;
    for (var i = 0; i < tableBody.children.length; i++) {
      var child = tableBody.children[i];
      if (child.classList.contains("ghost-row")) continue;
      if (count === index) return child;
      count++;
    }
    return null;
  }

  // ── Ghost-row enrichment fields allowed per source ─────────────────────────
  var GHOST_FIELDS = ["genre", "year", "artist", "title", "version_info"];

  // Source abbreviation badges
  var SOURCE_ABBR = {
    "ai_classifier": "AI",
    "ai_identify": "AI",
    "musicbrainz": "MB",
    "beatport": "BP",
    "soundcloud": "SC",
    "lastfm": "LF",
    "manual": "M",
  };

  function ghostSourceBadge(source) {
    if (!source) return "";
    var key = source.split(":")[0];
    var label = SOURCE_ABBR[key] || key.substring(0, 3).toUpperCase();
    return '<span class="ghost-src-badge src-' + key + '">' + label + '</span>';
  }

  function ghostDefaultTicked(field, proposal) {
    var conf = proposal.confidence || 0;
    var wasEmpty = !proposal.was || proposal.was === "";
    // Never auto-tick an overwrite (was non-empty and proposal differs)
    if (!wasEmpty && proposal.value !== proposal.was) return false;
    return conf >= ghostReview.autoThreshold;
  }

  // Build (or rebuild) ghost row ticked map for one track's proposals.
  function ghostInitTicked(tid, proposals) {
    var entry = {};
    for (var field in proposals) {
      if (!Object.prototype.hasOwnProperty.call(proposals, field)) continue;
      if (field === "_ts") continue;
      entry[field] = ghostDefaultTicked(field, proposals[field]);
    }
    ghostReview.ticked[tid] = entry;
  }

  function ghostCountTicked() {
    var n = 0;
    for (var tid in ghostReview.ticked) {
      if (!Object.prototype.hasOwnProperty.call(ghostReview.ticked, tid)) continue;
      var fields = ghostReview.ticked[tid];
      for (var f in fields) {
        if (fields[f]) n++;
      }
    }
    return n;
  }

  function updateReviewToolbar() {
    if (!ghostReview.active) {
      reviewToolbar.classList.add("hidden");
      return;
    }
    reviewToolbar.classList.remove("hidden");

    var label = ghostReview.locked
      ? "ENRICHING " + ghostReview.done + "/" + ghostReview.total + "…"
      : ghostReview.done + "/" + ghostReview.total + " done";
    reviewProgress.textContent = label;

    var ticked = ghostCountTicked();
    reviewTickedCount.textContent = ticked + " ticked";
    reviewApplyBtn.disabled = ticked === 0;
    reviewApplyBtn.textContent = "Apply " + ticked;

    // Update bulk column toggle button states
    var bulkBtns = reviewToolbar.querySelectorAll(".review-bulk-btn");
    bulkBtns.forEach(function (btn) {
      var field = btn.dataset.field;
      var allOn = true, anyOn = false;
      for (var tid in ghostReview.ticked) {
        if (!Object.prototype.hasOwnProperty.call(ghostReview.ticked, tid)) continue;
        if (!(field in ghostReview.ticked[tid])) { allOn = false; continue; }
        if (ghostReview.ticked[tid][field]) anyOn = true;
        else allOn = false;
      }
      btn.classList.toggle("bulk-all-on", allOn && anyOn);
      btn.classList.toggle("bulk-some-on", !allOn && anyOn);
    });

    // Apply ghost row filter visibility
    applyGhostFilter();
  }

  function applyGhostFilter() {
    var filter = ghostReview.filter;
    var ghostRows = tableBody.querySelectorAll("tr.ghost-row");
    ghostRows.forEach(function (gr) {
      var tid = gr.dataset.ghostFor;
      if (filter === "all") {
        gr.style.display = "";
        return;
      }
      var proposals = ghostReview.proposals[tid] || {};
      if (filter === "conflicts") {
        var hasConflict = false;
        for (var f in proposals) {
          if (f === "_ts") continue;
          var p = proposals[f];
          if (p.was && p.was !== "" && p.value !== p.was) { hasConflict = true; break; }
        }
        gr.style.display = hasConflict ? "" : "none";
        return;
      }
      if (filter === "low") {
        var hasLow = false;
        for (var f2 in proposals) {
          if (f2 === "_ts") continue;
          if ((proposals[f2].confidence || 0) < 0.7) { hasLow = true; break; }
        }
        gr.style.display = hasLow ? "" : "none";
        return;
      }
    });
  }

  // Render or refresh the ghost row for one track.
  function renderGhostRow(track, proposals) {
    var tid = track.track_id || track.file_hash || "";
    if (!tid) return;

    // Remove existing ghost row for this track if any
    var existing = tableBody.querySelector('tr.ghost-row[data-ghost-for="' + tid + '"]');
    if (existing) existing.remove();

    var cols = COLUMNS[currentSource] || COLUMNS.unsorted;

    var tr = document.createElement("tr");
    tr.classList.add("ghost-row");
    tr.dataset.ghostFor = tid;

    cols.forEach(function (col, colIdx) {
      var td = document.createElement("td");
      td.className = col.cls || "";
      td.classList.add("ghost-cell");

      if (colIdx === 1) {
        // Index column: show confidence badge + "↳"
        var proposal0 = null;
        var maxConf = 0;
        for (var f in proposals) {
          if (f === "_ts") continue;
          var c = proposals[f] && proposals[f].confidence || 0;
          if (c > maxConf) { maxConf = c; proposal0 = proposals[f]; }
        }
        var confPct = Math.round(maxConf * 100);
        var confCls = maxConf >= 0.85 ? "ghost-conf-high" : maxConf >= 0.6 ? "ghost-conf-mid" : "ghost-conf-low";
        td.innerHTML = '<span class="ghost-lead">↳</span> <span class="ghost-conf ' + confCls + '">' + confPct + '%</span>';
        tr.appendChild(td);
        return;
      }

      var field = col.key;
      if (!GHOST_FIELDS.includes(field)) {
        // Not enrichable: empty cell
        td.innerHTML = "";
        if (field === "done" || field === "_select") {
          // Apply-row button in last action column
          if (field === "done") {
            var applyBtn = document.createElement("button");
            applyBtn.className = "ghost-apply-row-btn";
            applyBtn.title = "Apply this row";
            applyBtn.textContent = "✓";
            applyBtn.dataset.tid = tid;
            applyBtn.addEventListener("click", function () {
              applyGhostRow(tid);
            });
            td.appendChild(applyBtn);
          }
        }
        tr.appendChild(td);
        return;
      }

      var proposal = proposals[field];
      if (!proposal) {
        td.innerHTML = '<span class="ghost-empty">—</span>';
        tr.appendChild(td);
        return;
      }

      var ticked = ghostReview.ticked[tid] && ghostReview.ticked[tid][field];
      var isOverwrite = proposal.was && proposal.was !== "" && proposal.value !== proposal.was;
      var isSame = proposal.value === proposal.was;

      td.classList.toggle("ghost-accept", !!ticked);
      td.classList.toggle("ghost-reject", !ticked);
      td.classList.toggle("ghost-overwrite", isOverwrite && !!ticked);
      td.classList.toggle("ghost-same", isSame);

      var toggleSpan = '<span class="ghost-toggle" role="button" tabindex="0" data-tid="' + tid + '" data-field="' + field + '">' + (ticked ? "✓" : "✗") + '</span>';
      var valSpan = '<span class="ghost-value">' + escapeHtml(proposal.value || "") + '</span>';
      var srcSpan = ghostSourceBadge(proposal.source);
      var wasSpan = isOverwrite ? '<span class="ghost-was">(was: ' + escapeHtml(proposal.was) + ')</span>' : (isSame ? '<span class="ghost-was dim">=</span>' : "");

      td.innerHTML = toggleSpan + " " + valSpan + " " + srcSpan + " " + wasSpan;
      td.querySelector(".ghost-toggle").addEventListener("click", function (e) {
        e.stopPropagation();
        toggleGhostCell(tid, field);
      });
      td.querySelector(".ghost-toggle").addEventListener("keydown", function (e) {
        if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleGhostCell(tid, field); }
      });

      tr.appendChild(td);
    });

    // Find the data row for this track and insert ghost row after it
    var dataRow = tableBody.querySelector('tr[data-tid="' + tid + '"]');
    if (!dataRow) {
      // fallback: find by dataset.idx matching filteredTracks index
      for (var i = 0; i < filteredTracks.length; i++) {
        if ((filteredTracks[i].track_id || filteredTracks[i].file_hash) === tid) {
          dataRow = getDataRow(i);
          break;
        }
      }
    }
    if (dataRow) {
      dataRow.after(tr);
    } else {
      tableBody.appendChild(tr);
    }
  }

  function toggleGhostCell(tid, field) {
    if (!ghostReview.ticked[tid]) ghostReview.ticked[tid] = {};
    ghostReview.ticked[tid][field] = !ghostReview.ticked[tid][field];
    // Refresh just this ghost row
    var track = null;
    for (var i = 0; i < filteredTracks.length; i++) {
      if ((filteredTracks[i].track_id || filteredTracks[i].file_hash) === tid) {
        track = filteredTracks[i];
        break;
      }
    }
    if (track) renderGhostRow(track, ghostReview.proposals[tid] || {});
    updateReviewToolbar();
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Apply a single ghost row (all ticked fields for that track)
  function applyGhostRow(tid) {
    var ticked = ghostReview.ticked[tid] || {};
    applyGhostApplications([{ track_id: tid, fields: ticked }]);
  }

  // Send accepted fields to backend and clean up ghost row(s)
  function applyGhostApplications(applications) {
    if (!applications.length) return;
    fetch("/api/apply-enrichment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ applications: applications }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { showToast("Apply error: " + data.error, ""); return; }
        applications.forEach(function (app) {
          var tid = app.track_id;
          // Update in-memory track data with accepted values
          var proposals = ghostReview.proposals[tid] || {};
          var track = null;
          for (var i = 0; i < allTracks.length; i++) {
            if ((allTracks[i].track_id || allTracks[i].file_hash) === tid) { track = allTracks[i]; break; }
          }
          if (track) {
            for (var field in app.fields) {
              if (app.fields[field] && proposals[field]) {
                track[field] = proposals[field].value;
              }
            }
          }
          // Remove ghost row from DOM
          var gr = tableBody.querySelector('tr.ghost-row[data-ghost-for="' + tid + '"]');
          if (gr) gr.remove();
          // Remove from review state
          delete ghostReview.ticked[tid];
          delete ghostReview.proposals[tid];
        });
        // Refresh table rows for applied tracks so values show up
        var changedTids = new Set(applications.map(function (a) { return a.track_id; }));
        reRenderDataRows(changedTids);
        updateReviewToolbar();
        showToast("Applied " + data.applied + " track(s)", "");
        if (Object.keys(ghostReview.proposals).length === 0) {
          exitReviewMode(false);
        }
      })
      .catch(function () { showToast("Apply request failed", ""); });
  }

  // Re-render specific data rows in-place (fade-in effect)
  function reRenderDataRows(tids) {
    for (var i = 0; i < filteredTracks.length; i++) {
      var t = filteredTracks[i];
      var tid = t.track_id || t.file_hash || "";
      if (!tids.has(tid)) continue;
      var dataRow = getDataRow(i);
      if (!dataRow) continue;
      dataRow.classList.add("ghost-applied");
      setTimeout((function (dr) {
        return function () { dr.classList.remove("ghost-applied"); };
      })(dataRow), 1200);
    }
  }

  function applyAllTicked() {
    var applications = [];
    for (var tid in ghostReview.ticked) {
      if (!Object.prototype.hasOwnProperty.call(ghostReview.ticked, tid)) continue;
      var fields = ghostReview.ticked[tid];
      var hasAny = Object.values(fields).some(Boolean);
      if (hasAny) applications.push({ track_id: tid, fields: fields });
    }
    applyGhostApplications(applications);
  }

  function startBatchEnrich(selectedTids) {
    if (!selectedTids || !selectedTids.length) {
      showToast("No tracks selected for batch enrich", "");
      return;
    }
    ghostReview.active = true;
    ghostReview.locked = true;
    ghostReview.total = selectedTids.length;
    ghostReview.done = 0;
    ghostReview.jobId = null;
    ghostReview.proposals = {};
    ghostReview.ticked = {};

    // Insert placeholder ghost rows immediately
    selectedTids.forEach(function (tid) {
      for (var i = 0; i < filteredTracks.length; i++) {
        if ((filteredTracks[i].track_id || filteredTracks[i].file_hash) === tid) {
          renderGhostRow(filteredTracks[i], { _loading: true });
          break;
        }
      }
    });

    updateReviewToolbar();
    document.body.classList.add("review-locked");

    fetch("/api/enrich-batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        track_ids: selectedTids,
        fields: ["genre", "year", "artist", "title", "version_info"],
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          showToast("Batch enrich error: " + data.error, "");
          exitReviewMode(true);
          return;
        }
        ghostReview.jobId = data.job_id;
        ghostReview.total = data.total;
        pollBatchEnrich();
      })
      .catch(function () {
        showToast("Batch enrich request failed", "");
        exitReviewMode(true);
      });
  }

  function pollBatchEnrich() {
    if (!ghostReview.jobId) return;
    ghostReview.pollTimer = setTimeout(function () {
      fetch("/api/enrich-status?job_id=" + ghostReview.jobId)
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { exitReviewMode(true); return; }

          ghostReview.done = data.done;

          // Hydrate new results into ghost rows
          var newResults = data.new_results || {};
          for (var tid in newResults) {
            if (!Object.prototype.hasOwnProperty.call(newResults, tid)) continue;
            // Fetch full proposal from sidecar on next tick (results are partial)
          }

          // Re-fetch sidecar delta to get full field proposals
          if (Object.keys(newResults).length > 0) {
            hydrateFromSidecar(Object.keys(newResults));
          }

          updateReviewToolbar();

          if (data.state === "running") {
            pollBatchEnrich();
          } else {
            ghostReview.locked = false;
            document.body.classList.remove("review-locked");
            updateReviewToolbar();
            showToast("Enrichment complete — " + ghostReview.done + " tracks", "");
          }
        })
        .catch(function () {
          exitReviewMode(true);
        });
    }, 1500);
  }

  function hydrateFromSidecar(tids) {
    fetch("/api/pending-suggestions")
      .then(function (r) { return r.json(); })
      .then(function (sidecar) {
        tids.forEach(function (tid) {
          var proposals = sidecar[tid];
          if (!proposals) return;
          ghostReview.proposals[tid] = proposals;
          ghostInitTicked(tid, proposals);
          for (var i = 0; i < filteredTracks.length; i++) {
            if ((filteredTracks[i].track_id || filteredTracks[i].file_hash) === tid) {
              renderGhostRow(filteredTracks[i], proposals);
              break;
            }
          }
        });
        updateReviewToolbar();
      });
  }

  function hydrateAllFromSidecar() {
    fetch("/api/pending-suggestions")
      .then(function (r) { return r.json(); })
      .then(function (sidecar) {
        var tids = Object.keys(sidecar);
        if (!tids.length) return;
        ghostReview.active = true;
        ghostReview.locked = false;
        ghostReview.done = tids.length;
        ghostReview.total = tids.length;
        tids.forEach(function (tid) {
          var proposals = sidecar[tid];
          ghostReview.proposals[tid] = proposals;
          ghostInitTicked(tid, proposals);
        });
        // Ghost rows will be rendered after table is built (deferred in renderTable)
        updateReviewToolbar();
      });
  }

  function exitReviewMode(cancelJob) {
    if (cancelJob && ghostReview.jobId) {
      fetch("/api/enrich-cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: ghostReview.jobId }),
      });
    }
    if (ghostReview.pollTimer) {
      clearTimeout(ghostReview.pollTimer);
      ghostReview.pollTimer = null;
    }
    ghostReview.active = false;
    ghostReview.locked = false;
    ghostReview.jobId = null;
    ghostReview.ticked = {};
    ghostReview.proposals = {};

    document.body.classList.remove("review-locked");

    // Remove all ghost rows from DOM
    tableBody.querySelectorAll("tr.ghost-row").forEach(function (gr) { gr.remove(); });
    updateReviewToolbar();
  }

  function renderTable() {
    const cols = COLUMNS[currentSource] || COLUMNS.unsorted;

    // Header
    tableHead.innerHTML = "";
    const hr = document.createElement("tr");
    for (const col of cols) {
      const th = document.createElement("th");
      if (col.key === "_select") {
        th.classList.add("col-select");
        th.dataset.key = col.key;
        if (col.width) th.style.width = col.width;
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.id = "select-all-cb";
        cb.title = "Select / deselect all";
        cb.addEventListener("change", function () {
          if (cb.checked) {
            for (let j = 0; j < filteredTracks.length; j++) {
              selectedSet.add(trackId(filteredTracks[j]));
            }
          } else {
            selectedSet.clear();
          }
          // Sync DOM (rows + per-row checkboxes)
          for (let j = 0; j < tableBody.children.length; j++) {
            const tr = tableBody.children[j];
            tr.classList.toggle("selected", cb.checked);
            const rowCb = tr.querySelector('.col-select input[type="checkbox"]');
            if (rowCb) rowCb.checked = cb.checked;
          }
          updateBatchBar();
        });
        th.appendChild(cb);
      } else {
        th.textContent = col.label;
        if (col.width) th.style.width = col.width;
        th.dataset.key = col.key;
        if (sortKey === col.key) {
          th.classList.add("sorted");
          th.textContent += sortDir === 1 ? " \u25B2" : " \u25BC";
        }
        th.addEventListener("click", function () {
          handleSort(col.key);
        });
      }
      hr.appendChild(th);
    }
    tableHead.appendChild(hr);

    // Body
    tableBody.innerHTML = "";
    const frag = document.createDocumentFragment();

    for (let i = 0; i < filteredTracks.length; i++) {
      const track = filteredTracks[i];
      const tr = document.createElement("tr");
      tr.dataset.idx = i;

      // Row state classes
      const trackTid = trackId(track);
      if (trackTid) tr.dataset.tid = trackTid; // used by ghost-row querySelector
      if (i === currentIndex) tr.classList.add("active");
      if (selectedSet.has(trackTid)) tr.classList.add("selected");
      if (track.status === "accept") tr.classList.add("status-accept");
      else if (track.status === "reject") tr.classList.add("status-reject");
      if (track.done === "TRUE") tr.classList.add("is-done");

      for (const col of cols) {
        const td = document.createElement("td");
        if (col.cls) td.classList.add(col.cls);

        if (col.key === "_select") {
          td.classList.add("col-select");
          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = selectedSet.has(trackTid);
          cb.addEventListener(
            "change",
            (function (tid, cb, tr) {
              return function (e) {
                e.stopPropagation();
                if (cb.checked) {
                  selectedSet.add(tid);
                  tr.classList.add("selected");
                } else {
                  selectedSet.delete(tid);
                  tr.classList.remove("selected");
                }
                updateBatchBar();
                updateSelectAllState();
              };
            })(trackTid, cb, tr),
          );
          td.appendChild(cb);
        } else if (col.key === "_index") {
          td.textContent = i + 1;
          td.classList.add("col-index");
        } else if (col.type === "checkbox") {
          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = track[col.key] === "TRUE";
          cb.addEventListener(
            "change",
            (function (track, col, cb, tr) {
              return function (e) {
                e.stopPropagation();
                track[col.key] = cb.checked ? "TRUE" : "FALSE";
                saveTrackField(track, col.key, track[col.key]);
                tr.classList.toggle("is-done", cb.checked);
              };
            })(track, col, cb, tr),
          );
          td.appendChild(cb);
        } else if (col.type === "genre-select") {
          const sel = buildGenreSelect(track[col.key]);
          sel.addEventListener(
            "change",
            (function (track, col, sel) {
              return function (e) {
                e.stopPropagation();
                track[col.key] = sel.value;
                saveTrackField(track, col.key, sel.value);
              };
            })(track, col, sel),
          );
          sel.addEventListener("mousedown", function (e) {
            e.stopPropagation();
          });
          td.appendChild(sel);
        } else if (col.type === "dest-select") {
          const sel = buildDestSelect(track[col.key]);
          sel.addEventListener(
            "change",
            (function (track, col, sel) {
              return function (e) {
                e.stopPropagation();
                track[col.key] = sel.value;
                saveTrackField(track, col.key, sel.value);
              };
            })(track, col, sel),
          );
          sel.addEventListener("mousedown", function (e) {
            e.stopPropagation();
          });
          td.appendChild(sel);
        } else if (col.type === "editable") {
          td.textContent = track[col.key] || "";
          td.title = track[col.key] || "";
          td.classList.add("cell-editable");
          td.addEventListener(
            "dblclick",
            (function (td, track, col) {
              return function (e) {
                e.stopPropagation();
                startInlineEdit(td, track, col.key);
              };
            })(td, track, col),
          );
        } else if (col.type === "status-display") {
          td.textContent = track[col.key] || "\u2014";
          td.classList.add("col-status");
          if (track[col.key]) td.classList.add(track[col.key]);
        } else if (col.type === "rating") {
          td.classList.add("col-rating");
          td.innerHTML = ratingToStars(track[col.key]);
          if (isEditableSource()) {
            td.classList.add("col-rating-editable");
            td.addEventListener(
              "click",
              (function (td, track, colKey) {
                return function (e) {
                  e.stopPropagation();
                  handleRatingClick(td, track, colKey, e);
                };
              })(td, track, col.key),
            );
          }
        } else if (col.type === "color-dot") {
          td.innerHTML = colorDotHtml(track[col.key]);
          td.style.textAlign = "center";
        } else if (col.type === "source-badge") {
          td.innerHTML = sourceBadgeHtml(track[col.key]);
        } else if (col.type === "dest-badge") {
          td.innerHTML = destBadgeHtml(track[col.key]);
        } else if (col.type === "in-dj-badge") {
          td.innerHTML = inDjBadgeHtml(track[col.key]);
        } else if (col.type === "in-library") {
          if (isInLibrary(track)) {
            td.innerHTML = '<span class="badge-in-lib">LIB</span>';
            td.title = "Already in library (artist + title match)";
          }
        } else {
          const raw = track[col.key] || "";
          td.textContent = col.fmt ? col.fmt(raw) : raw;
          td.title = raw;
        }

        tr.appendChild(td);
      }

      // Row events
      tr.addEventListener(
        "click",
        (function (i) {
          return function (e) {
            if (e.shiftKey) {
              extendSelection(i);
            } else {
              // Move cursor only — preserve batch selection (#3)
              selectRow(i);
            }
          };
        })(i),
      );
      tr.addEventListener(
        "dblclick",
        (function (i) {
          return function () {
            selectRow(i);
            playTrack(i);
          };
        })(i),
      );
      tr.addEventListener(
        "contextmenu",
        (function (i, track) {
          return function (e) {
            selectRow(i);
            showContextMenu(e, track);
          };
        })(i, track),
      );

      frag.appendChild(tr);
    }

    tableBody.appendChild(frag);

    // Re-render ghost rows for any proposals already in review state
    if (ghostReview.active && Object.keys(ghostReview.proposals).length > 0) {
      for (var gtid in ghostReview.proposals) {
        if (!Object.prototype.hasOwnProperty.call(ghostReview.proposals, gtid)) continue;
        for (var gi = 0; gi < filteredTracks.length; gi++) {
          if ((filteredTracks[gi].track_id || filteredTracks[gi].file_hash) === gtid) {
            renderGhostRow(filteredTracks[gi], ghostReview.proposals[gtid]);
            break;
          }
        }
      }
      updateReviewToolbar();
    }
  }

  /**
   * Find best-matching genre label from the genres list.
   * Handles case/spacing mismatches: "afrohouse" → "Afro House".
   */
  function matchGenreLabel(raw) {
    if (!raw) return raw;
    // Exact match first
    for (var i = 0; i < genres.length; i++) {
      if (genres[i] === raw) return genres[i];
    }
    // Case-insensitive match
    var lower = raw.toLowerCase();
    for (var i = 0; i < genres.length; i++) {
      if (genres[i].toLowerCase() === lower) return genres[i];
    }
    // Normalized match (strip spaces/hyphens)
    var norm = lower.replace(/[\s\-]+/g, "");
    for (var i = 0; i < genres.length; i++) {
      if (genres[i].toLowerCase().replace(/[\s\-]+/g, "") === norm)
        return genres[i];
    }
    return raw;
  }

  function buildGenreSelect(currentValue) {
    const sel = document.createElement("select");
    sel.classList.add("inline-select");

    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "\u2014";
    sel.appendChild(empty);

    // Normalize currentValue to match a dropdown option
    var matched = matchGenreLabel(currentValue);

    for (const g of genres) {
      const o = document.createElement("option");
      o.value = g;
      o.textContent = g;
      if (g === matched) o.selected = true;
      sel.appendChild(o);
    }
    return sel;
  }
  const DEST_OPTIONS = ["", "library", "reject", "archive", "mixes"];

  function buildDestSelect(currentValue) {
    const sel = document.createElement("select");
    sel.classList.add("inline-select");
    for (const d of DEST_OPTIONS) {
      const o = document.createElement("option");
      o.value = d;
      o.textContent = d || "\u2014";
      if (d === currentValue) o.selected = true;
      sel.appendChild(o);
    }
    return sel;
  }

  // -- Inline editing (double-click on artist/title/version/year) --
  function startInlineEdit(td, track, key) {
    if (td.querySelector("input")) return; // already editing
    const oldVal = track[key] || "";
    const input = document.createElement("input");
    input.type = "text";
    input.value = oldVal;
    input.className = "inline-edit";
    td.textContent = "";
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
        showToast(key + " updated", "");
      }
    }

    input.addEventListener("blur", commit);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        input.blur();
      }
      if (e.key === "Escape") {
        input.value = oldVal;
        input.blur();
      }
      e.stopPropagation(); // Prevent keyboard shortcuts while editing
    });
  }

  // -- Batch bar helpers --------------------------------------
  function updateBatchBar() {
    const n = selectedSet.size;
    if (n === 0) {
      batchBar.classList.add("hidden");
      return;
    }
    batchBar.classList.remove("hidden");
    batchCount.textContent = n + " selected";
    batchApplyCount.textContent = n;
  }

  function updateSelectAllState() {
    const cb = document.getElementById("select-all-cb");
    if (!cb) return;
    const n = selectedSet.size;
    const total = filteredTracks.length;
    if (n === 0) {
      cb.checked = false;
      cb.indeterminate = false;
    } else if (n >= total) {
      cb.checked = true;
      cb.indeterminate = false;
    } else {
      cb.checked = false;
      cb.indeterminate = true;
    }
  }

  function populateBatchGenre() {
    batchGenre.innerHTML = '<option value="">Genre…</option>';
    for (const g of genres) {
      const opt = document.createElement("option");
      opt.value = g;
      opt.textContent = g;
      batchGenre.appendChild(opt);
    }
  }

  function applyBatchFields() {
    if (!isEditableSource()) return;
    const fields = {};
    const genreVal = batchGenre.value.trim();
    const yearVal = batchYear.value.trim();
    const artistVal = batchArtist.value.trim();
    const statusVal = batchStatus.value;
    const destVal = batchDest.value;
    const ratingVal = batchRating.value;
    const doneVal = batchDone.value;

    // Year validation: 4 digits in plausible DJ-music range
    if (yearVal) {
      const yearNum = parseInt(yearVal, 10);
      if (!/^\d{4}$/.test(yearVal) || yearNum < 1900 || yearNum > 2099) {
        showToast("Invalid year — use 1900–2099", "reject");
        batchYear.focus();
        batchYear.select();
        return;
      }
    }

    if (genreVal) fields.genre = genreVal;
    if (yearVal) fields.year = yearVal;
    if (artistVal) fields.artist = artistVal;
    if (statusVal) fields.status = statusVal;
    if (destVal) fields.destination = destVal;
    if (ratingVal !== "") fields.rating = ratingVal;  // "0" is valid for clearing
    if (doneVal) fields.done = doneVal;

    if (selectedSet.size === 0) {
      showToast("Nothing selected", "reject");
      return;
    }
    if (Object.keys(fields).length === 0) {
      showToast("No fields to apply", "");
      return;
    }

    // Build {tid -> track} map and track_ids list from current selection
    const targets = [];
    for (let i = 0; i < filteredTracks.length; i++) {
      const tid = trackId(filteredTracks[i]);
      if (selectedSet.has(tid)) {
        targets.push({ tid: tid, track: filteredTracks[i] });
      }
    }
    if (targets.length === 0) {
      showToast("Nothing selected", "reject");
      return;
    }
    const track_ids = targets.map(function (t) { return t.tid; });

    // Snapshot prior values for rollback
    const rollback = [];
    for (const { track } of targets) {
      const prev = {};
      for (const k of Object.keys(fields)) prev[k] = track[k] || "";
      rollback.push({ track, prev });
    }

    // Optimistic update in memory + push single-batch undo
    pushBatchUndo(rollback, fields);
    for (const { track } of targets) {
      for (const [k, v] of Object.entries(fields)) {
        track[k] = v;
      }
    }

    const n = track_ids.length;
    batchApply.disabled = true;
    fetch("/api/tracks/batch-update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_ids, fields, source: currentSource }),
    })
      .then(function (r) { return r.json().then(function (d) { return { status: r.status, body: d }; }); })
      .then(function (resp) {
        if (resp.status >= 200 && resp.status < 300 && resp.body.ok) {
          showToast("Updated " + resp.body.updated + " tracks", "accept");
          if (resp.body.dropped_fields && resp.body.dropped_fields.length) {
            setTimeout(function () {
              showToast("Skipped (not in CSV): " + resp.body.dropped_fields.join(", "), "reject");
            }, 1300);
          }
          applyFilters();
        } else {
          // Rollback in-memory
          for (const { track, prev } of rollback) {
            for (const [k, v] of Object.entries(prev)) track[k] = v;
          }
          undoStack.pop(); // drop the batch-undo entry we never committed
          applyFilters();
          showToast("Batch update failed — reverted", "reject");
        }
      })
      .catch(function () {
        for (const { track, prev } of rollback) {
          for (const [k, v] of Object.entries(prev)) track[k] = v;
        }
        undoStack.pop();
        applyFilters();
        showToast("Batch update error — reverted", "reject");
      })
      .finally(function () {
        batchApply.disabled = false;
      });

    batchGenre.value = "";
    batchYear.value = "";
    batchArtist.value = "";
    batchStatus.value = "";
    batchDest.value = "";
    batchRating.value = "";
    batchDone.value = "";
    // Selection preserved (#4)
    showToast("Applying to " + n + " tracks…", "");
  }

  // -- Batch selection ----------------------------------------
  function clearSelection() {
    selectedSet.clear();
    // Walk all rendered rows — set may have held stale ids after a re-render
    for (let j = 0; j < tableBody.children.length; j++) {
      const tr = tableBody.children[j];
      if (tr.classList.contains("ghost-row")) continue;
      tr.classList.remove("selected");
      const rowCb = tr.querySelector('.col-select input[type="checkbox"]');
      if (rowCb) rowCb.checked = false;
    }
    updateBatchBar();
    updateSelectAllState();
  }

  function extendSelection(toIndex) {
    if (selectionAnchor < 0)
      selectionAnchor = currentIndex >= 0 ? currentIndex : 0;
    clearSelection();
    const lo = Math.min(selectionAnchor, toIndex);
    const hi = Math.max(selectionAnchor, toIndex);
    for (let i = lo; i <= hi; i++) {
      if (i >= 0 && i < filteredTracks.length) {
        selectedSet.add(trackId(filteredTracks[i]));
      }
      var selTr = getDataRow(i);
      if (selTr) {
        selTr.classList.add("selected");
        const rowCb = selTr.querySelector('.col-select input[type="checkbox"]');
        if (rowCb) rowCb.checked = true;
      }
    }
    // Move cursor to toIndex
    const prev = currentIndex;
    currentIndex = toIndex;
    var prevRow = getDataRow(prev);
    if (prevRow) prevRow.classList.remove("active");
    var toRow = getDataRow(toIndex);
    if (toRow) {
      toRow.classList.add("active");
      toRow.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    // Update player info
    const track = filteredTracks[toIndex];
    if (track) {
      nowArtist.textContent = track.artist || "Unknown Artist";
      const ver = track.version_info ? " (" + track.version_info + ")" : "";
      nowTitle.textContent = (track.title || "Unknown Title") + ver;
      updateFilenameDisplay(track);
      updateGenreSources(track);
    }
    updateBatchBar();
    updateSelectAllState();
  }

  // -- Row selection ------------------------------------------
  function selectRow(index) {
    if (index < 0 || index >= filteredTracks.length) return;

    const prev = currentIndex;
    currentIndex = index;
    selectionAnchor = index;
    const track = filteredTracks[index];

    // Update visual state (swap classes instead of full re-render)
    var prevDataRow = getDataRow(prev);
    if (prevDataRow) prevDataRow.classList.remove("active");
    var newDataRow = getDataRow(index);
    if (newDataRow) {
      newDataRow.classList.add("active");
      newDataRow.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    // Player info
    nowArtist.textContent = track.artist || "Unknown Artist";
    const ver = track.version_info ? " (" + track.version_info + ")" : "";
    nowTitle.textContent = (track.title || "Unknown Title") + ver;

    // Filename in footer
    updateFilenameDisplay(track);

    // Genre sources
    updateGenreSources(track);

    // Preload next track audio
    preloadNextTrack();
  }

  function updateGenreSources(track) {
    const parts = [];
    if (track.genres_musicbrainz)
      parts.push(
        '<span class="gs gs-mb">MB: ' +
          escHtml(track.genres_musicbrainz) +
          "</span>",
      );
    if (track.genres_lastfm)
      parts.push(
        '<span class="gs gs-lfm">Last.fm: ' +
          escHtml(track.genres_lastfm) +
          "</span>",
      );
    if (track.genres_soundcloud)
      parts.push(
        '<span class="gs gs-sc">SC: ' +
          escHtml(track.genres_soundcloud) +
          "</span>",
      );
    if (track.genres_beatport)
      parts.push(
        '<span class="gs gs-bp">BP: ' +
          escHtml(track.genres_beatport) +
          "</span>",
      );
    if (track.genre_suggest)
      parts.push(
        '<span class="gs gs-suggest clickable" data-genre="' +
          escHtml(track.genre_suggest).replace(/"/g, "&quot;") +
          '">\u2192 ' +
          escHtml(track.genre_suggest) +
          "</span>",
      );
    // AI batch classify results (from library review or batch scripts)
    if (track.ai_genre) {
      var aiLabel = 'AI: ' + escHtml(track.ai_genre);
      if (track.ai_confidence) aiLabel += ' (' + Math.round(track.ai_confidence * 100) + '%)';
      parts.push(
        '<span class="gs gs-ai clickable" data-genre="' +
          escHtml(track.ai_genre).replace(/"/g, '&quot;') +
          '" data-ai-artist="' + escHtml(track.ai_artist || '').replace(/"/g, '&quot;') +
          '" data-ai-title="' + escHtml(track.ai_title || '').replace(/"/g, '&quot;') +
          '" data-ai-version="' + escHtml(track.ai_version || '').replace(/"/g, '&quot;') +
          '" title="' + escHtml(track.ai_reasoning || '').replace(/"/g, '&quot;') +
          '">' + aiLabel + '</span>',
      );
    }
    genreSources.innerHTML = parts.join("");

    // Make genre suggestion clickable to apply it
    genreSources
      .querySelectorAll(".gs-suggest.clickable")
      .forEach(function (el) {
        el.addEventListener("click", function () {
          applyGenreSuggestionFromBadge(el);
        });
      });
    // Make AI batch suggestion clickable to apply all AI fields
    genreSources
      .querySelectorAll(".gs-ai.clickable")
      .forEach(function (el) {
        el.addEventListener("click", function () {
          applyAiBatchSuggestion(el);
        });
      });
  }

  function applyGenreSuggestionFromBadge(el) {
    const g = el.dataset.genre;
    if (!g || !isEditableSource() || currentIndex < 0) return;
    const t = filteredTracks[currentIndex];
    t.genre = g;
    saveTrackField(t, "genre", g);
    showToast("Genre: " + g, "");
    const cols = COLUMNS[currentSource] || COLUMNS.unsorted;
    const genreColIdx = cols.findIndex(function (c) {
      return c.key === "genre";
    });
    var curRow = getDataRow(currentIndex);
    if (genreColIdx >= 0 && curRow) {
      const cell = curRow.children[genreColIdx];
      const sel = cell.querySelector("select");
      if (sel) sel.value = g;
    }
  }

  function applyAiBatchSuggestion(el) {
    if (!isEditableSource() || currentIndex < 0) return;
    var t = filteredTracks[currentIndex];
    var genre = el.dataset.genre;
    var artist = el.dataset.aiArtist;
    var title = el.dataset.aiTitle;
    var version = el.dataset.aiVersion;
    var fields = [];
    if (genre && genre !== t.genre) {
      t.genre = genre;
      saveTrackField(t, 'genre', genre);
      fields.push('genre');
    }
    if (artist && artist !== t.artist) {
      t.artist = artist;
      saveTrackField(t, 'artist', artist);
      fields.push('artist');
    }
    if (title && title !== t.title) {
      t.title = title;
      saveTrackField(t, 'title', title);
      fields.push('title');
    }
    if (version && version !== t.version_info) {
      t.version_info = version;
      saveTrackField(t, 'version_info', version);
      fields.push('version');
    }
    if (fields.length) {
      showToast('AI applied: ' + fields.join(', '), '');
      // Refresh row cells
      var cols = COLUMNS[currentSource] || COLUMNS.unsorted;
      var row = getDataRow(currentIndex);
      if (row) {
        cols.forEach(function(col, ci) {
          if (col.key === 'genre') {
            var sel = row.children[ci].querySelector('select');
            if (sel) sel.value = genre || '';
          } else if (['artist', 'title', 'version_info'].indexOf(col.key) >= 0) {
            row.children[ci].textContent = t[col.key] || '';
            row.children[ci].title = t[col.key] || '';
          }
        });
      }
    } else {
      showToast('AI: no changes needed', '');
    }
  }

  function applyGenreSuggestion() {
    if (currentIndex < 0 || !isEditableSource()) return;
    const track = filteredTracks[currentIndex];
    const g = track.genre_suggest;
    if (!g) {
      showToast("No genre suggestion", "");
      return;
    }
    track.genre = g;
    saveTrackField(track, "genre", g);
    showToast("Genre: " + g, "");
    const cols = COLUMNS[currentSource] || COLUMNS.unsorted;
    const genreColIdx = cols.findIndex(function (c) {
      return c.key === "genre";
    });
    if (genreColIdx >= 0 && tableBody.children[currentIndex]) {
      const cell = tableBody.children[currentIndex].children[genreColIdx];
      const sel = cell.querySelector("select");
      if (sel) sel.value = g;
    }
  }

  function escHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  // -- Filename display in player footer ----------------------
  function getBasename(path) {
    if (!path) return "";
    // Handle both forward and backslash separators
    const parts = path.replace(/\\/g, "/").split("/");
    return parts[parts.length - 1] || "";
  }

  function updateFilenameDisplay(track) {
    const path = audioPath(track);
    const basename = getBasename(path);
    nowFilename.textContent = basename;
    nowFilename.title = path || "No file path";
  }

  // Click on filename → copy to clipboard
  nowFilename.addEventListener("click", function () {
    const text = nowFilename.textContent;
    if (text) {
      navigator.clipboard.writeText(text).then(function () {
        showToast("Copied: " + text, "");
      });
    }
  });

  // -- Context menu -------------------------------------------
  let contextTrack = null;

  function showContextMenu(e, track) {
    e.preventDefault();
    contextTrack = track;

    const path = audioPath(track);
    const hasPath = !!path;
    const isUnsorted = currentSource === "unsorted";

    // Enable/disable actions based on context
    contextMenu.querySelectorAll("button").forEach(function (btn) {
      const action = btn.dataset.action;
      if (action === "show-finder" || action === "copy-filename") {
        btn.disabled = !hasPath;
      }
      if (action === "enrich-track" || action === "swap-artist-title") {
        btn.disabled = !isUnsorted;
      }
      if (action === "scrape-url") {
        btn.disabled = !isUnsorted;
      }
    });

    // Position menu
    const menuW = 240;
    const menuH = 330;
    let x = e.clientX;
    let y = e.clientY;
    if (x + menuW > window.innerWidth) x = window.innerWidth - menuW - 8;
    if (y + menuH > window.innerHeight) y = window.innerHeight - menuH - 8;
    contextMenu.style.left = x + "px";
    contextMenu.style.top = y + "px";
    contextMenu.classList.remove("hidden");
  }

  function hideContextMenu() {
    contextMenu.classList.add("hidden");
    contextTrack = null;
  }

  // Close on click outside or Escape
  document.addEventListener("click", function (e) {
    if (!contextMenu.contains(e.target)) hideContextMenu();
  });
  document.addEventListener(
    "keydown",
    function (e) {
      if (e.code === "Escape" && !contextMenu.classList.contains("hidden")) {
        hideContextMenu();
        e.stopPropagation();
      }
    },
    true,
  );

  // Handle context menu actions
  contextMenu.addEventListener("click", function (e) {
    const btn = e.target.closest("button");
    if (!btn || btn.disabled || !contextTrack) return;

    const action = btn.dataset.action;
    const artist = (contextTrack.artist || "").trim();
    const title = (contextTrack.title || "").trim();
    const version = (contextTrack.version_info || "").trim();
    const path = audioPath(contextTrack);
    const baseQuery =
      artist && title
        ? artist + " - " + title
        : artist || title || getBasename(path);
    const query = version ? baseQuery + " " + version : baseQuery;

    switch (action) {
      case "search-google":
        window.open(
          "https://www.google.com/search?q=" + encodeURIComponent(query),
          "_blank",
        );
        break;
      case "search-beatport":
        window.open(
          "https://www.beatport.com/search?q=" + encodeURIComponent(query),
          "_blank",
        );
        break;
      case "search-soundcloud":
        window.open(
          "https://soundcloud.com/search/sounds?q=" + encodeURIComponent(query),
          "_blank",
        );
        break;
      case "show-finder":
        if (path) {
          fetch("/api/reveal", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: path }),
          })
            .then(function (r) {
              return r.json();
            })
            .then(function (data) {
              if (data.error) showToast("Finder: " + data.error, "");
            })
            .catch(function () {
              showToast("Failed to reveal in Finder", "");
            });
        }
        break;
      case "copy-filename":
        if (path) {
          navigator.clipboard.writeText(getBasename(path)).then(function () {
            showToast("Copied filename", "");
          });
        }
        break;
      case "copy-artist-title":
        if (query) {
          navigator.clipboard.writeText(query).then(function () {
            showToast("Copied: " + query, "");
          });
        }
        break;
      case "ai-suggest-genre":
        requestAiGenreSuggest(contextTrack);
        break;
      case "identify-track":
        requestAiIdentify(contextTrack);
        break;
      case "ai-classify":
        requestAiClassify(contextTrack);
        break;
      case "ai-chat":
        openAiChat(contextTrack);
        break;
      case "enrich-track":
        requestEnrichTrack(contextTrack);
        break;
      case "swap-artist-title":
        requestSwapArtistTitle(contextTrack);
        break;
      case "scrape-url":
        openUrlInputDialog(contextTrack);
        break;
    }
    hideContextMenu();
  });

  // -- AI Genre Suggest ---------------------------------------

  function requestAiGenreSuggest(track) {
    if (!track || aiPending) return;
    if (!aiAvailable) {
      showToast(
        "AI not configured (add openai_api_key to config.local.yml)",
        "",
      );
      return;
    }

    aiPending = true;
    aiBannerTrack = track;

    // Show loading state
    aiBanner.classList.remove("hidden");
    aiBanner.classList.add("ai-loading");
    aiBannerGenre.textContent = "Analyzing…";
    aiBannerConfidence.textContent = "";
    aiBannerReasoning.textContent = "";
    aiBannerAccept.style.display = "none";
    aiBannerDismiss.style.display = "inline-block";

    // Extract folder name from path
    var path = audioPath(track);
    var folder = "";
    if (path) {
      var parts = path.replace(/\\/g, "/").split("/");
      // Get parent folder name (e.g., "Afro House" from ".../Afro House/file.wav")
      if (parts.length >= 2) folder = parts[parts.length - 2];
    }

    var body = {
      track_id: trackId(track),
      context: {
        artist: track.artist || "",
        title: track.title || "",
        version: track.version_info || "",
        bpm: track.bpm || "",
        key: track.key_camelot || track.key || "",
        duration: track.duration_suggest || "",
        folder: folder,
        genres_musicbrainz: track.genres_musicbrainz || "",
        genres_lastfm: track.genres_lastfm || "",
        genres_soundcloud: track.genres_soundcloud || "",
        genres_beatport: track.genres_beatport || "",
        genre_suggest: track.genre_suggest || "",
      },
    };

    fetch("/api/suggest-genre", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        aiPending = false;
        aiBanner.classList.remove("ai-loading");

        if (data.error) {
          showToast("AI error: " + data.error, "");
          hideAiBanner();
          return;
        }

        aiBannerGenre.textContent = data.genre || "Unknown";
        var conf = data.confidence
          ? Math.round(data.confidence * 100) + "%"
          : "";
        aiBannerConfidence.textContent = conf;
        aiBannerReasoning.textContent = data.reasoning || "";
        aiBannerReasoning.title = data.reasoning || "";
        if (data.warning) {
          aiBannerReasoning.textContent += " ⚠ " + data.warning;
        }
        aiBannerAccept.style.display = "inline-block";
        aiBannerAccept.dataset.genre = data.genre || "";
      })
      .catch(function (err) {
        aiPending = false;
        aiBanner.classList.remove("ai-loading");
        showToast("AI request failed", "");
        hideAiBanner();
      });
  }

  function hideAiBanner() {
    aiBanner.classList.add("hidden");
    aiBanner.classList.remove("ai-loading");
    aiBannerTrack = null;
  }

  // -- Re-enrich (context menu) ---------------------------------

  function requestEnrichTrack(track) {
    if (!track || enrichPending) return;
    if (currentSource !== "unsorted") {
      showToast("Enrich only works on Unsorted tab", "");
      return;
    }

    enrichPending = true;
    enrichBannerTrack = track;

    // Show loading state
    enrichBanner.classList.remove("hidden");
    enrichBanner.classList.add("enrich-loading");
    enrichBannerGenre.textContent = "Enriching…";
    enrichBannerConf.textContent = "";
    enrichBannerSources.textContent = "";
    enrichBannerAccept.style.display = "none";
    enrichBannerSwap.style.display = "none";
    enrichBannerDismiss.style.display = "inline-block";

    fetch("/api/enrich-track", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: trackId(track) }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        enrichPending = false;
        enrichBanner.classList.remove("enrich-loading");

        if (data.error) {
          showToast("Enrich error: " + data.error, "");
          hideEnrichBanner();
          return;
        }

        if (!data.genre) {
          enrichBannerGenre.textContent = "No results found";
          enrichBannerConf.textContent = "";
          enrichBannerSources.textContent =
            "Try editing artist/title and re-enriching";
        } else {
          var displayGenre = data.genre_full || data.genre;
          if (data.year) {
            displayGenre += " (" + data.year + ")";
          }
          enrichBannerGenre.textContent = displayGenre;
          var conf = data.confidence
            ? Math.round(data.confidence * 100) + "%"
            : "";
          enrichBannerConf.textContent = conf;
          // Show source details
          var srcParts = [];
          if (data.source_details) {
            for (var src in data.source_details) {
              srcParts.push(src + ": " + data.source_details[src]);
            }
          }
          enrichBannerSources.textContent = srcParts.join(" | ");
          enrichBannerSources.title = srcParts.join("\n");
          enrichBannerAccept.style.display = "inline-block";
          enrichBannerAccept.dataset.genre = data.genre_full || data.genre;
          enrichLastData = data;
        }

        // Show swap suggestion if detected
        if (data.swap_suggestion && data.swap_suggestion.swapped) {
          enrichBannerSwap.style.display = "inline-block";
          enrichBannerSwap.title =
            data.swap_suggestion.reason || "Artist and title may be swapped";
        }
      })
      .catch(function (err) {
        enrichPending = false;
        enrichBanner.classList.remove("enrich-loading");
        showToast("Enrich request failed", "");
        hideEnrichBanner();
      });
  }

  function hideEnrichBanner() {
    enrichBanner.classList.add("hidden");
    enrichBanner.classList.remove("enrich-loading");
    enrichBannerTrack = null;
    enrichLastData = null;
  }

  // Accept enrich result
  // Store last enrich response data for Accept to use
  var enrichLastData = null;

  enrichBannerAccept.addEventListener("click", function () {
    var genre = enrichBannerAccept.dataset.genre;
    if (!genre || !enrichBannerTrack) return;

    // Save MAIN genre (single canonical name) to genre column (dropdown-compatible)
    var mainGenre = matchGenreLabel(
      (enrichLastData && enrichLastData.genre) || genre,
    );
    enrichBannerTrack.genre = mainGenre;
    saveTrackField(enrichBannerTrack, "genre", mainGenre);

    // Save genre_full (main + subs) to genre_suggest (text field, not dropdown)
    var genreFull =
      (enrichLastData && (enrichLastData.genre_full || enrichLastData.genre)) ||
      genre;
    enrichBannerTrack.genre_suggest = genreFull;
    saveTrackField(enrichBannerTrack, "genre_suggest", genreFull);

    // Save per-source genre tags (SC, BP, Last.fm, MB) to CSV
    if (enrichLastData && enrichLastData.source_genres) {
      var sg = enrichLastData.source_genres;
      for (var col in sg) {
        enrichBannerTrack[col] = sg[col];
        saveTrackField(enrichBannerTrack, col, sg[col]);
      }
    }

    // Save meta_source
    if (enrichLastData && enrichLastData.meta_source) {
      enrichBannerTrack.meta_source = enrichLastData.meta_source;
      saveTrackField(
        enrichBannerTrack,
        "meta_source",
        enrichLastData.meta_source,
      );
    }

    // Save year if returned by enrich
    if (enrichLastData && enrichLastData.year) {
      enrichBannerTrack.year = enrichLastData.year;
      saveTrackField(enrichBannerTrack, "year", enrichLastData.year);
    }

    // Update dropdown in table if visible
    if (currentSource === "unsorted") {
      var idx = filteredTracks.indexOf(enrichBannerTrack);
      if (idx >= 0) {
        var cols = COLUMNS.unsorted;
        var genreColIdx = cols.findIndex(function (c) {
          return c.key === "genre";
        });
        var idxRow = getDataRow(idx);
        if (genreColIdx >= 0 && idxRow) {
          var cell = idxRow.children[genreColIdx];
          var sel = cell.querySelector("select");
          if (sel) sel.value = mainGenre;
        }
        // Update year cell if visible
        var yearColIdx = cols.findIndex(function (c) {
          return c.key === "year";
        });
        if (enrichLastData && enrichLastData.year && yearColIdx >= 0 && idxRow) {
          var yearCell = idxRow.children[yearColIdx];
          if (yearCell) yearCell.textContent = enrichLastData.year;
        }
      }
    }

    // Refresh genre sources panel (SC, BP, etc. at bottom)
    updateGenreSources(enrichBannerTrack);

    var toastMsg = "Genre: " + mainGenre;
    if (enrichLastData && enrichLastData.year) {
      toastMsg += " | Year: " + enrichLastData.year;
    }
    showToast(toastMsg, "");
    hideEnrichBanner();
  });

  // Dismiss enrich banner
  enrichBannerDismiss.addEventListener("click", function () {
    hideEnrichBanner();
  });

  // Swap button in enrich banner
  enrichBannerSwap.addEventListener("click", function () {
    if (!enrichBannerTrack) return;
    requestSwapArtistTitle(enrichBannerTrack);
    hideEnrichBanner();
  });

  function requestSwapArtistTitle(track) {
    if (!track) return;
    if (currentSource !== "unsorted") {
      showToast("Swap only works on Unsorted tab", "");
      return;
    }

    fetch("/api/swap-artist-title", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: trackId(track) }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.error) {
          showToast("Swap error: " + data.error, "");
          return;
        }

        // Update in-memory track data
        track.artist = data.artist;
        track.title = data.title;
        if (data.version_info !== undefined) {
          track.version_info = data.version_info;
        }

        // Re-render table to reflect swap
        renderTable();
        var idx = filteredTracks.indexOf(track);
        if (idx >= 0) selectRow(idx);

        var ver = data.version_info ? " (" + data.version_info + ")" : "";
        showToast("\u21c4 " + data.artist + " \u2014 " + data.title + ver, "");
      })
      .catch(function () {
        showToast("Swap request failed", "");
      });
  }

  // -- URL Scrape -----------------------------------------------

  function openUrlInputDialog(track) {
    if (!track) return;
    if (currentSource !== "unsorted") {
      showToast("URL scrape only works on Unsorted tab", "");
      return;
    }
    scrapeTrack = track;
    urlInputField.value = "";
    urlInputOverlay.classList.remove("hidden");
    setTimeout(function () {
      urlInputField.focus();
    }, 50);
  }

  function closeUrlInputDialog() {
    urlInputOverlay.classList.add("hidden");
    urlInputField.value = "";
  }

  urlInputCancel.addEventListener("click", function () {
    closeUrlInputDialog();
  });

  urlInputOverlay.addEventListener("click", function (e) {
    if (e.target === urlInputOverlay) {
      closeUrlInputDialog();
    }
  });

  urlInputField.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      urlInputSubmit.click();
    } else if (e.key === "Escape") {
      closeUrlInputDialog();
    }
  });

  urlInputSubmit.addEventListener("click", function () {
    var url = (urlInputField.value || "").trim();
    if (!url) {
      showToast("Paste a URL first", "");
      return;
    }
    if (!/^https?:\/\//i.test(url)) {
      showToast("Invalid URL (must start with http/https)", "");
      return;
    }
    closeUrlInputDialog();
    requestUrlScrape(scrapeTrack, url);
  });

  function requestUrlScrape(track, url) {
    if (!track || scrapePending) return;

    scrapePending = true;
    scrapeTrack = track;

    // Show loading state in banner
    urlScrapeBanner.classList.remove("hidden");
    urlScrapeBanner.classList.add("url-scrape-loading");
    urlScrapeBannerArtist.textContent = "Scraping…";
    urlScrapeBannerTitle.textContent = "";
    urlScrapeBannerVersion.textContent = "";
    urlScrapeBannerGenre.textContent = "";
    urlScrapeBannerYear.textContent = "";
    urlScrapeBannerSource.textContent = "";
    urlScrapeBannerAccept.style.display = "none";
    urlScrapeBannerDismiss.style.display = "inline-block";

    fetch("/api/scrape-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: trackId(track), url: url }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        scrapePending = false;
        urlScrapeBanner.classList.remove("url-scrape-loading");

        if (data.error) {
          showToast("Scrape error: " + data.error, "");
          hideUrlScrapeBanner();
          return;
        }

        var result = data.result || data;
        var hasData =
          result.artist || result.title || result.version || result.genre;
        if (!hasData) {
          showToast("No metadata found at URL", "");
          hideUrlScrapeBanner();
          return;
        }

        urlScrapeBannerArtist.textContent = result.artist || "?";
        urlScrapeBannerTitle.textContent = result.title || "?";
        urlScrapeBannerVersion.textContent = result.version
          ? "(" + result.version + ")"
          : "";
        urlScrapeBannerGenre.textContent = result.genre
          ? "[" + result.genre + "]"
          : "";
        urlScrapeBannerYear.textContent = result.year
          ? "(" + result.year + ")"
          : "";
        urlScrapeBannerSource.textContent = result.source || "";

        // Store data for Accept
        urlScrapeBannerAccept.dataset.artist = result.artist || "";
        urlScrapeBannerAccept.dataset.title = result.title || "";
        urlScrapeBannerAccept.dataset.version = result.version || "";
        urlScrapeBannerAccept.dataset.genre = result.genre || "";
        urlScrapeBannerAccept.dataset.year = result.year || "";
        urlScrapeBannerAccept.dataset.url = url;
        urlScrapeBannerAccept.style.display = "inline-block";
      })
      .catch(function () {
        scrapePending = false;
        urlScrapeBanner.classList.remove("url-scrape-loading");
        showToast("Scrape request failed", "");
        hideUrlScrapeBanner();
      });
  }

  function hideUrlScrapeBanner() {
    urlScrapeBanner.classList.add("hidden");
    urlScrapeBanner.classList.remove("url-scrape-loading");
    scrapeTrack = null;
  }

  // Accept URL scrape result
  urlScrapeBannerAccept.addEventListener("click", function () {
    if (!scrapeTrack) return;

    var newArtist = urlScrapeBannerAccept.dataset.artist || "";
    var newTitle = urlScrapeBannerAccept.dataset.title || "";
    var newVersion = urlScrapeBannerAccept.dataset.version || "";
    var newGenre = urlScrapeBannerAccept.dataset.genre || "";
    var newYear = urlScrapeBannerAccept.dataset.year || "";
    var newUrl = urlScrapeBannerAccept.dataset.url || "";

    if (newArtist) {
      scrapeTrack.artist = newArtist;
      saveTrackField(scrapeTrack, "artist", newArtist);
    }
    if (newTitle) {
      scrapeTrack.title = newTitle;
      saveTrackField(scrapeTrack, "title", newTitle);
    }
    if (newVersion !== undefined) {
      scrapeTrack.version_info = newVersion;
      saveTrackField(scrapeTrack, "version_info", newVersion);
    }
    if (newGenre) {
      scrapeTrack.genre_suggest = newGenre;
      saveTrackField(scrapeTrack, "genre_suggest", newGenre);
    }
    if (newYear) {
      scrapeTrack.year = newYear;
      saveTrackField(scrapeTrack, "year", newYear);
    }
    if (newUrl) {
      scrapeTrack.source_url = newUrl;
      saveTrackField(scrapeTrack, "source_url", newUrl);
    }

    // Re-render table to reflect changes
    renderTable();
    var idx = filteredTracks.indexOf(scrapeTrack);
    if (idx >= 0) selectRow(idx);

    var ver = newVersion ? " (" + newVersion + ")" : "";
    var yr = newYear ? " [" + newYear + "]" : "";
    showToast(
      "\ud83d\udcce " + newArtist + " \u2014 " + newTitle + ver + yr,
      "",
    );
    hideUrlScrapeBanner();
  });

  // Dismiss URL scrape banner
  urlScrapeBannerDismiss.addEventListener("click", function () {
    hideUrlScrapeBanner();
  });

  // -- AI Track Identify ----------------------------------------

  function requestAiIdentify(track) {
    if (!track || identifyPending) return;
    if (!aiAvailable) {
      showToast(
        "AI not configured (add openai_api_key to config.local.yml)",
        "",
      );
      return;
    }

    identifyPending = true;
    identifyBannerTrack = track;

    // Show loading state
    identifyBanner.classList.remove("hidden");
    identifyBanner.classList.add("identify-loading");
    identifyBannerArtist.textContent = "Identifying…";
    identifyBannerTitle.textContent = "";
    identifyBannerVersion.textContent = "";
    identifyBannerYear.textContent = "";
    identifyBannerConfidence.textContent = "";
    identifyBannerReasoning.textContent = "";
    identifyBannerAccept.style.display = "none";
    identifyBannerDismiss.style.display = "inline-block";

    fetch("/api/identify-track", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: trackId(track) }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        identifyPending = false;
        identifyBanner.classList.remove("identify-loading");

        if (data.error) {
          showToast("Identify error: " + data.error, "");
          hideIdentifyBanner();
          return;
        }

        identifyBannerArtist.textContent = data.artist || "?";
        identifyBannerTitle.textContent = data.title || "?";
        identifyBannerVersion.textContent = data.version
          ? "(" + data.version + ")"
          : "";
        identifyBannerYear.textContent = data.year ? "[" + data.year + "]" : "";
        var conf = data.confidence
          ? Math.round(data.confidence * 100) + "%"
          : "";
        identifyBannerConfidence.textContent = conf;
        identifyBannerReasoning.textContent = data.reasoning || "";
        identifyBannerReasoning.title = data.reasoning || "";

        // Store data for Accept
        identifyBannerAccept.dataset.artist = data.artist || "";
        identifyBannerAccept.dataset.title = data.title || "";
        identifyBannerAccept.dataset.version = data.version || "";
        identifyBannerAccept.dataset.year = data.year || "";
        identifyBannerAccept.style.display = "inline-block";
      })
      .catch(function (err) {
        identifyPending = false;
        identifyBanner.classList.remove("identify-loading");
        showToast("Identify request failed", "");
        hideIdentifyBanner();
      });
  }

  function hideIdentifyBanner() {
    identifyBanner.classList.add("hidden");
    identifyBanner.classList.remove("identify-loading");
    identifyBannerTrack = null;
  }

  // Accept identify result
  identifyBannerAccept.addEventListener("click", function () {
    if (!identifyBannerTrack) return;

    var newArtist = identifyBannerAccept.dataset.artist || "";
    var newTitle = identifyBannerAccept.dataset.title || "";
    var newVersion = identifyBannerAccept.dataset.version || "";
    var newYear = identifyBannerAccept.dataset.year || "";

    // Save fields that have values
    if (newArtist) {
      identifyBannerTrack.artist = newArtist;
      saveTrackField(identifyBannerTrack, "artist", newArtist);
    }
    if (newTitle) {
      identifyBannerTrack.title = newTitle;
      saveTrackField(identifyBannerTrack, "title", newTitle);
    }
    if (newVersion !== undefined) {
      identifyBannerTrack.version_info = newVersion;
      saveTrackField(identifyBannerTrack, "version_info", newVersion);
    }
    if (newYear) {
      identifyBannerTrack.year = newYear;
      saveTrackField(identifyBannerTrack, "year", newYear);
    }

    // Re-render table to reflect changes
    renderTable();
    var idx = filteredTracks.indexOf(identifyBannerTrack);
    if (idx >= 0) selectRow(idx);

    var ver = newVersion ? " (" + newVersion + ")" : "";
    var yr = newYear ? " [" + newYear + "]" : "";
    showToast("ID: " + newArtist + " \u2014 " + newTitle + ver + yr, "");
    hideIdentifyBanner();
  });

  // Dismiss identify banner
  identifyBannerDismiss.addEventListener("click", function () {
    hideIdentifyBanner();
  });

  // -- Unified AI Classify (naming + genre in one call) -------

  function requestAiClassify(track) {
    if (!track || classifyPending) return;
    if (!aiAvailable) {
      showToast(
        "AI not configured (add openai_api_key to config.local.yml)",
        "",
      );
      return;
    }

    classifyPending = true;
    classifyBannerTrack = track;

    // Show loading state
    classifyBanner.classList.remove("hidden");
    classifyBanner.classList.add("classify-loading");
    classifyBannerArtist.textContent = "Classifying…";
    classifyBannerTitle.textContent = "";
    classifyBannerVersion.textContent = "";
    classifyBannerGenre.textContent = "";
    classifyBannerConfidence.textContent = "";
    classifyBannerReasoning.textContent = "";
    classifyBannerAccept.style.display = "none";
    classifyBannerDismiss.style.display = "inline-block";

    fetch("/api/ai-classify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: trackId(track), source: currentSource }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        classifyPending = false;
        classifyBanner.classList.remove("classify-loading");

        if (data.error) {
          showToast("Classify error: " + data.error, "");
          hideClassifyBanner();
          return;
        }

        classifyBannerArtist.textContent = data.artist || "?";
        classifyBannerTitle.textContent = data.title || "?";

        // Version is an array — display as (Token1) (Token2)
        var versionArr = data.version || [];
        if (typeof versionArr === "string") versionArr = versionArr ? [versionArr] : [];
        var versionDisplay = versionArr
          .map(function (v) {
            return "(" + v + ")";
          })
          .join(" ");
        classifyBannerVersion.textContent = versionDisplay;

        classifyBannerGenre.textContent = data.genre || "";
        var conf = data.confidence
          ? Math.round(data.confidence * 100) + "%"
          : "";
        classifyBannerConfidence.textContent = conf;
        classifyBannerReasoning.textContent = data.reasoning || "";
        classifyBannerReasoning.title = data.reasoning || "";

        if (data.genre_warning) {
          classifyBannerReasoning.textContent += " ⚠ " + data.genre_warning;
        }

        // Store data for Accept
        classifyBannerAccept.dataset.artist = data.artist || "";
        classifyBannerAccept.dataset.title = data.title || "";
        // Store version as comma-separated for CSV (each token separate)
        classifyBannerAccept.dataset.version = versionArr.join(", ");
        classifyBannerAccept.dataset.genre = data.genre || "";
        classifyBannerAccept.style.display = "inline-block";
      })
      .catch(function (err) {
        classifyPending = false;
        classifyBanner.classList.remove("classify-loading");
        showToast("Classify request failed", "");
        hideClassifyBanner();
      });
  }

  function hideClassifyBanner() {
    classifyBanner.classList.add("hidden");
    classifyBanner.classList.remove("classify-loading");
    classifyBannerTrack = null;
  }

  // Accept classify result — saves all fields at once
  classifyBannerAccept.addEventListener("click", function () {
    if (!classifyBannerTrack) return;

    var newArtist = classifyBannerAccept.dataset.artist || "";
    var newTitle = classifyBannerAccept.dataset.title || "";
    var newVersion = classifyBannerAccept.dataset.version || "";
    var newGenre = classifyBannerAccept.dataset.genre || "";

    if (newArtist) {
      classifyBannerTrack.artist = newArtist;
      saveTrackField(classifyBannerTrack, "artist", newArtist);
    }
    if (newTitle) {
      classifyBannerTrack.title = newTitle;
      saveTrackField(classifyBannerTrack, "title", newTitle);
    }
    if (newVersion !== undefined) {
      classifyBannerTrack.version_info = newVersion;
      saveTrackField(classifyBannerTrack, "version_info", newVersion);
    }
    if (newGenre) {
      classifyBannerTrack.genre = newGenre;
      saveTrackField(classifyBannerTrack, "genre", newGenre);
    }

    renderTable();
    var idx = filteredTracks.indexOf(classifyBannerTrack);
    if (idx >= 0) selectRow(idx);

    var ver = newVersion ? " " + newVersion.split(", ").map(function(v) { return "(" + v + ")"; }).join(" ") : "";
    showToast(
      newArtist + " \u2014 " + newTitle + ver + " [" + newGenre + "]",
      "",
    );
    hideClassifyBanner();
  });

  // Dismiss classify banner
  classifyBannerDismiss.addEventListener("click", function () {
    hideClassifyBanner();
  });

  // -- AI Chat ------------------------------------------------

  function openAiChat(track) {
    if (!track) return;
    if (!aiAvailable) {
      showToast(
        "AI not configured (add openai_api_key to config.local.yml)",
        "",
      );
      return;
    }

    // If opening for a different track, reset session
    if (chatTrack && trackId(chatTrack) !== trackId(track)) {
      resetChatSession(trackId(chatTrack));
    }

    chatTrack = track;
    var artist = track.artist || "";
    var title = track.title || "";
    var filename = track.filepath
      ? getBasename(track.filepath)
      : track.filename || "";
    var label = artist && title ? artist + " — " + title : filename;
    aiChatTitle.textContent = "💬 " + label;
    aiChatTitle.title = label;
    aiChatPanel.classList.remove("hidden");
    aiChatPanel.classList.remove("minimized");
    aiChatInput.focus();

    // Show quick prompts when no messages yet
    if (aiChatMessages.children.length === 0) {
      var hint = document.createElement("div");
      hint.className = "chat-msg chat-msg-hint";
      hint.textContent =
        "Ask me anything about this track — correct artist/title, challenge genre, identify mashups...";
      aiChatMessages.appendChild(hint);
      aiChatPrompts.classList.remove("hidden");
    }
  }

  function closeAiChat() {
    aiChatPanel.classList.add("hidden");
    // Don't clear chatTrack so reopening keeps history
  }

  function resetChatSession(tid) {
    fetch("/api/ai-chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: tid, reset: true }),
    }).catch(function () {});
    aiChatMessages.innerHTML = "";
    aiChatPrompts.classList.remove("hidden");
  }

  function sendChatMessage() {
    if (chatPending || !chatTrack) return;
    var msg = aiChatInput.value.trim();
    if (!msg) return;

    aiChatInput.value = "";
    chatPending = true;
    aiChatSend.disabled = true;

    // Append user message bubble
    appendChatBubble("user", msg);

    // Hide quick prompts after first message
    aiChatPrompts.classList.add("hidden");

    // Show loading indicator
    var loadingEl = document.createElement("div");
    loadingEl.className = "chat-msg-loading";
    loadingEl.innerHTML =
      '<span class="chat-dots"><span></span><span></span><span></span></span>';
    aiChatMessages.appendChild(loadingEl);
    aiChatMessages.scrollTop = aiChatMessages.scrollHeight;

    fetch("/api/ai-chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: trackId(chatTrack), message: msg }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        chatPending = false;
        aiChatSend.disabled = false;
        loadingEl.remove();

        if (data.error) {
          appendChatBubble("ai", "Error: " + data.error);
          return;
        }

        appendChatBubble(
          "ai",
          data.reply,
          data.suggestion,
          data.web_search,
          data.sources,
        );
      })
      .catch(function () {
        chatPending = false;
        aiChatSend.disabled = false;
        loadingEl.remove();
        appendChatBubble("ai", "Request failed. Please try again.");
      });
  }

  function appendChatBubble(role, text, suggestion, webSearch, sources) {
    var bubble = document.createElement("div");
    bubble.className = "chat-msg chat-msg-" + (role === "user" ? "user" : "ai");

    // Web search badge
    if (role === "ai" && webSearch) {
      var badge = document.createElement("span");
      badge.className = "chat-web-search-badge";
      badge.textContent = "🔍 web search";
      bubble.appendChild(badge);
    }

    var textNode = document.createTextNode(text);
    bubble.appendChild(textNode);

    if (role === "ai" && suggestion && typeof suggestion === "object") {
      var block = buildSuggestionBlock(suggestion);
      bubble.appendChild(block);
    }

    // Source citations
    if (role === "ai" && sources && sources.length > 0) {
      var srcBlock = document.createElement("div");
      srcBlock.className = "chat-sources";
      var srcLabel = document.createElement("span");
      srcLabel.className = "chat-sources-label";
      srcLabel.textContent = "Sources:";
      srcBlock.appendChild(srcLabel);
      sources.forEach(function (src) {
        var link = document.createElement("a");
        link.href = src.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.className = "chat-source-link";
        link.textContent = src.title || new URL(src.url).hostname;
        link.title = src.url;
        srcBlock.appendChild(link);
      });
      bubble.appendChild(srcBlock);
    }

    aiChatMessages.appendChild(bubble);
    aiChatMessages.scrollTop = aiChatMessages.scrollHeight;
  }

  function buildSuggestionBlock(suggestion) {
    var block = document.createElement("div");
    block.className = "chat-suggestion-block";

    var fieldLabels = {
      artist: "Artist",
      title: "Title",
      version_info: "Version",
      year: "Year",
      genre: "Genre",
    };

    var fieldKeys = Object.keys(suggestion);
    fieldKeys.forEach(function (key) {
      if (!fieldLabels[key]) return;
      var val = suggestion[key];
      if (val === undefined || val === null || val === "") return;

      var row = document.createElement("div");
      row.className = "suggestion-row";

      var fieldSpan = document.createElement("span");
      fieldSpan.className = "suggestion-field";
      fieldSpan.textContent = fieldLabels[key];

      // Values container with optional current (diff) display
      var valuesDiv = document.createElement("div");
      valuesDiv.className = "suggestion-values";

      // Show current value as strikethrough if different from suggested
      var currentVal = chatTrack ? chatTrack[key] || "" : "";
      if (currentVal && currentVal !== val) {
        var currentSpan = document.createElement("span");
        currentSpan.className = "suggestion-current";
        currentSpan.textContent = currentVal;
        currentSpan.title = "Current: " + currentVal;
        valuesDiv.appendChild(currentSpan);
      }

      var valSpan = document.createElement("span");
      valSpan.className = "suggestion-value";
      valSpan.textContent = val;
      valSpan.title = val;
      valuesDiv.appendChild(valSpan);

      var applyBtn = document.createElement("button");
      applyBtn.className = "suggestion-apply-btn";
      applyBtn.textContent = "Apply";
      applyBtn.addEventListener("click", function () {
        if (applyBtn.classList.contains("applied")) return;
        applyChatField(key, val);
        applyBtn.textContent = "✓";
        applyBtn.classList.add("applied");
      });

      row.appendChild(fieldSpan);
      row.appendChild(valuesDiv);
      row.appendChild(applyBtn);
      block.appendChild(row);
    });

    // Apply All button if more than one field
    if (
      fieldKeys.filter(function (k) {
        return fieldLabels[k] && suggestion[k];
      }).length > 1
    ) {
      var allRow = document.createElement("div");
      allRow.className = "suggestion-apply-all";
      var allBtn = document.createElement("button");
      allBtn.textContent = "Apply All";
      allBtn.addEventListener("click", function () {
        fieldKeys.forEach(function (key) {
          if (!fieldLabels[key] || !suggestion[key]) return;
          applyChatField(key, suggestion[key]);
        });
        block.querySelectorAll(".suggestion-apply-btn").forEach(function (btn) {
          btn.textContent = "✓";
          btn.classList.add("applied");
        });
        allBtn.textContent = "✓ Applied";
        allBtn.disabled = true;
      });
      allRow.appendChild(allBtn);
      block.appendChild(allRow);
    }

    return block;
  }

  function applyChatField(key, value) {
    if (!chatTrack) return;
    chatTrack[key] = value;
    saveTrackField(chatTrack, key, value);
    renderTable();
    var idx = filteredTracks.indexOf(chatTrack);
    if (idx >= 0) selectRow(idx);
  }

  // Chat panel event listeners
  aiChatSend.addEventListener("click", sendChatMessage);
  aiChatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage();
    }
  });
  aiChatClose.addEventListener("click", closeAiChat);

  // Minimize / restore
  aiChatMinimize.addEventListener("click", function () {
    aiChatPanel.classList.toggle("minimized");
  });

  // Quick prompt buttons
  aiChatPrompts.addEventListener("click", function (e) {
    var btn = e.target.closest(".ai-chat-prompt-btn");
    if (!btn) return;
    var prompt = btn.dataset.prompt;
    if (!prompt) return;
    aiChatInput.value = prompt;
    sendChatMessage();
    // Hide quick prompts after first use
    aiChatPrompts.classList.add("hidden");
  });

  // Drag to reposition
  (function initChatDrag() {
    var dragging = false;
    var offsetX = 0,
      offsetY = 0;

    aiChatDragHandle.addEventListener("mousedown", function (e) {
      // Don't drag if clicking buttons
      if (e.target.tagName === "BUTTON") return;
      dragging = true;
      var rect = aiChatPanel.getBoundingClientRect();
      offsetX = e.clientX - rect.left;
      offsetY = e.clientY - rect.top;
      // Switch from bottom/right positioning to top/left
      aiChatPanel.style.left = rect.left + "px";
      aiChatPanel.style.top = rect.top + "px";
      aiChatPanel.style.right = "auto";
      aiChatPanel.style.bottom = "auto";
      e.preventDefault();
    });

    document.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      var x = Math.max(
        0,
        Math.min(e.clientX - offsetX, window.innerWidth - 100),
      );
      var y = Math.max(
        0,
        Math.min(e.clientY - offsetY, window.innerHeight - 60),
      );
      aiChatPanel.style.left = x + "px";
      aiChatPanel.style.top = y + "px";
    });

    document.addEventListener("mouseup", function () {
      dragging = false;
    });
  })();

  // Accept AI suggestion
  aiBannerAccept.addEventListener("click", function () {
    var genre = aiBannerAccept.dataset.genre;
    if (!genre || !aiBannerTrack) return;

    // Apply genre to track
    aiBannerTrack.genre = genre;
    saveTrackField(aiBannerTrack, "genre", genre);
    showToast("Genre: " + genre, "");

    // Update dropdown in table if visible
    if (currentSource === "unsorted") {
      var idx = filteredTracks.indexOf(aiBannerTrack);
      if (idx >= 0) {
        var cols = COLUMNS.unsorted;
        var genreColIdx = cols.findIndex(function (c) {
          return c.key === "genre";
        });
        var aiRow = getDataRow(idx);
        if (genreColIdx >= 0 && aiRow) {
          var cell = aiRow.children[genreColIdx];
          var sel = cell.querySelector("select");
          if (sel) sel.value = genre;
        }
      }
    }

    hideAiBanner();
  });

  // Dismiss AI suggestion
  aiBannerDismiss.addEventListener("click", function () {
    hideAiBanner();
  });

  // -- Audio playback -----------------------------------------
  let playingIndex = -1;

  function playTrack(index) {
    if (index < 0 || index >= filteredTracks.length) return;
    const track = filteredTracks[index];
    const path = audioPath(track);
    if (!path) {
      showToast("No audio file path", "");
      return;
    }

    selectRow(index);
    playingIndex = index;
    audio.src = "/api/audio?path=" + encodeURIComponent(path);
    audio.play().catch(function (e) {
      console.warn("Playback failed:", e);
      showToast(
        "Playback failed \u2014 file may not exist or format unsupported",
        "",
      );
    });

    // Load waveform
    loadWaveform(path);
  }

  function togglePlayPause() {
    if (!audio.paused) {
      audio.pause();
    } else if (audio.src && audio.currentTime > 0) {
      audio.play().catch(function () {});
    } else if (currentIndex >= 0) {
      playTrack(currentIndex);
    }
  }

  function stopPlayback() {
    audio.pause();
    audio.currentTime = 0;
    playingIndex = -1;
    nowIndicator.classList.remove("visible");
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
    _preloadTimer = setTimeout(function () {
      const nextIdx = currentIndex + 1;
      if (nextIdx >= filteredTracks.length) return;
      const path = audioPath(filteredTracks[nextIdx]);
      if (!path) return;
      const preloadAudio = new Audio();
      preloadAudio.preload = "auto";
      preloadAudio.src = "/api/audio?path=" + encodeURIComponent(path);
      preloadAudio.addEventListener(
        "canplaythrough",
        function () {
          preloadAudio.src = "";
        },
        { once: true },
      );
    }, 300);
  }

  // -- Waveform -----------------------------------------------
  var _audioCtx = null;

  function getAudioContext() {
    if (!_audioCtx)
      _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    return _audioCtx;
  }

  function loadWaveform(path) {
    const url = "/api/audio?path=" + encodeURIComponent(path);
    fetch(url)
      .then(function (r) {
        return r.arrayBuffer();
      })
      .then(function (buf) {
        const ctx = getAudioContext();
        const clone = buf.slice(0);
        ctx.decodeAudioData(
          clone,
          function (audioBuffer) {
            drawWaveform(audioBuffer);
          },
          function (err) {
            console.warn("Waveform decode error:", err);
          },
        );
      })
      .catch(function (e) {
        console.warn("Waveform fetch error:", e);
      });
  }

  function drawWaveform(audioBuffer) {
    const canvas = waveformCanvas;
    const ctx = canvas.getContext("2d");
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

    ctx.strokeStyle = "rgba(124, 108, 255, 0.5)";
    ctx.lineWidth = 1;
    ctx.beginPath();

    for (let x = 0; x < w; x++) {
      let min = 1.0,
        max = -1.0;
      const start = x * step;
      for (let j = 0; j < step && start + j < data.length; j++) {
        const val = data[start + j];
        if (val < min) min = val;
        if (val > max) max = val;
      }
      const yLow = mid + min * mid;
      const yHigh = mid + max * mid;
      ctx.moveTo(x + 0.5, yLow);
      ctx.lineTo(x + 0.5, yHigh);
    }
    ctx.stroke();
  }

  function resizeWaveform() {
    if (audio.src && playingIndex >= 0) {
      const path = audioPath(filteredTracks[playingIndex]);
      if (path) loadWaveform(path);
    }
  }

  window.addEventListener("resize", resizeWaveform);

  // -- Audio events -------------------------------------------
  audio.addEventListener("play", function () {
    nowIndicator.classList.add("visible");
  });

  audio.addEventListener("pause", function () {
    nowIndicator.classList.remove("visible");
  });

  audio.addEventListener("timeupdate", function () {
    if (!audio.duration) return;
    const pct = (audio.currentTime / audio.duration) * 100;
    playerProgress.style.width = pct + "%";
    playerTime.textContent =
      fmtTime(audio.currentTime) + " / " + fmtTime(audio.duration);
  });

  audio.addEventListener("ended", function () {
    nowIndicator.classList.remove("visible");
    if (currentIndex < filteredTracks.length - 1) {
      selectRow(currentIndex + 1);
      playTrack(currentIndex);
    }
  });

  // Progress bar interaction
  progressContainer.addEventListener("click", function (e) {
    if (!audio.duration) return;
    const rect = progressContainer.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    audio.currentTime = pct * audio.duration;
  });

  progressContainer.addEventListener("mousemove", function (e) {
    const rect = progressContainer.getBoundingClientRect();
    const pct = ((e.clientX - rect.left) / rect.width) * 100;
    progressHover.style.width = Math.min(100, Math.max(0, pct)) + "%";
  });

  progressContainer.addEventListener("mouseleave", function () {
    progressHover.style.width = "0%";
  });

  // -- Auto-play toggle ---------------------------------------
  autoPlayCheckbox.addEventListener("change", function () {
    autoPlay = autoPlayCheckbox.checked;
  });

  // -- Save (debounced, merging multiple field changes) -------
  function saveTrackField(track, key, value) {
    if (currentSource !== "unsorted" && currentSource !== "library-review" && currentSource !== "library-fix") return;
    const id = trackId(track);
    if (!id) return;

    if (!pendingSaves[id]) {
      pendingSaves[id] = { timer: null, fields: {} };
    }
    pendingSaves[id].fields[key] = value;

    clearTimeout(pendingSaves[id].timer);
    pendingSaves[id].timer = setTimeout(function () {
      const fields = pendingSaves[id].fields;
      delete pendingSaves[id];
      fetch("/api/tracks/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_id: id, fields: fields, source: currentSource }),
      }).catch(function (e) {
        console.error("Save failed:", e);
      });
    }, SAVE_DEBOUNCE_MS);
  }

  // -- Undo ---------------------------------------------------
  function pushUndo(track, fields) {
    const entry = { type: "single", trackId: trackId(track), prev: {} };
    for (const k of Object.keys(fields)) {
      entry.prev[k] = track[k] || "";
    }
    undoStack.push(entry);
    if (undoStack.length > MAX_UNDO) undoStack.shift();
  }

  function pushBatchUndo(rollback, fields) {
    // rollback: [{track, prev}, ...]; fields: keys that were changed
    const items = rollback.map(function (r) {
      return { trackId: trackId(r.track), prev: { ...r.prev } };
    });
    const entry = { type: "batch", items: items, fieldKeys: Object.keys(fields) };
    undoStack.push(entry);
    if (undoStack.length > MAX_UNDO) undoStack.shift();
  }

  function performUndo() {
    if (undoStack.length === 0) {
      showToast("Nothing to undo", "");
      return;
    }
    const entry = undoStack.pop();

    if (entry.type === "batch") {
      // Restore in-memory + push one batch-update to server
      const track_ids = [];
      const fieldsByTid = {};
      for (const item of entry.items) {
        const track = allTracks.find(function (t) { return trackId(t) === item.trackId; });
        if (!track) continue;
        for (const [k, v] of Object.entries(item.prev)) track[k] = v;
        track_ids.push(item.trackId);
        fieldsByTid[item.trackId] = item.prev;
      }
      // Group by identical prev-field-set isn't worth it — server endpoint sets the SAME fields
      // across all track_ids. We need one request per distinct prev-field-set.
      // Simpler: fall back to per-track updates here (rare path, undo is small).
      for (const item of entry.items) {
        fetch("/api/tracks/update", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ track_id: item.trackId, fields: item.prev, source: currentSource }),
        }).catch(function (e) { console.error("Undo save failed:", e); });
      }
      applyFilters();
      showToast("Undo batch: " + entry.fieldKeys.join(", ") + " ×" + entry.items.length, "");
      return;
    }

    // Legacy single-track undo
    const track = allTracks.find(function (t) {
      return trackId(t) === entry.trackId;
    });
    if (!track) {
      showToast("Undo: track not found", "");
      return;
    }
    for (const [k, v] of Object.entries(entry.prev)) {
      track[k] = v;
      const id = trackId(track);
      fetch("/api/tracks/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_id: id, fields: { [k]: v }, source: currentSource }),
      }).catch(function (e) {
        console.error("Undo save failed:", e);
      });
    }

    applyFilters();

    const idx = filteredTracks.indexOf(track);
    if (idx >= 0) {
      selectRow(idx);
    }

    showToast("Undo: " + Object.keys(entry.prev).join(", "), "");
  }

  // -- Status / actions ---------------------------------------
  function setStatus(status) {
    if (!isEditableSource()) return;

    const targets = [];
    if (selectedSet.size > 0) {
      // Map track_ids to current indices in filteredTracks
      for (let i = 0; i < filteredTracks.length; i++) {
        if (selectedSet.has(trackId(filteredTracks[i]))) {
          targets.push({ idx: i, track: filteredTracks[i] });
        }
      }
    } else if (currentIndex >= 0 && currentIndex < filteredTracks.length) {
      targets.push({ idx: currentIndex, track: filteredTracks[currentIndex] });
    }

    if (targets.length === 0) return;

    for (const { idx, track } of targets) {
      const undoFields = { status: status };
      if (AUTO_DEST[status]) undoFields.destination = AUTO_DEST[status];
      pushUndo(track, undoFields);

      track.status = status;
      saveTrackField(track, "status", status);

      if (AUTO_DEST[status]) {
        track.destination = AUTO_DEST[status];
        saveTrackField(track, "destination", AUTO_DEST[status]);
      }

      const row = getDataRow(idx);
      if (row) {
        row.classList.remove("status-accept", "status-reject");
        if (status === "accept") row.classList.add("status-accept");
        else if (status === "reject") row.classList.add("status-reject");

        const cols = COLUMNS[currentSource];
        const statusColIdx = cols.findIndex(function (c) {
          return c.key === "status";
        });
        if (statusColIdx >= 0 && row.children[statusColIdx]) {
          const td = row.children[statusColIdx];
          td.textContent = status || "\u2014";
          td.className = "col-status" + (status ? " " + status : "");
        }

        if (AUTO_DEST[status]) {
          const destColIdx = cols.findIndex(function (c) {
            return c.key === "destination";
          });
          if (destColIdx >= 0 && row.children[destColIdx]) {
            const sel = row.children[destColIdx].querySelector("select");
            if (sel) sel.value = AUTO_DEST[status];
          }
        }
      }
    }

    const label =
      targets.length > 1
        ? status.toUpperCase() + " \u00d7" + targets.length
        : status.toUpperCase();
    showToast(label, status);
    updateStats();

    clearSelection();

    if (targets.length === 1 && currentIndex < filteredTracks.length - 1) {
      setTimeout(function () {
        navigateRow(1, false);
      }, 150);
    }
  }

  function toggleDone() {
    if (currentIndex < 0 || !isEditableSource()) return;
    const track = filteredTracks[currentIndex];
    const newVal = track.done === "TRUE" ? "FALSE" : "TRUE";
    track.done = newVal;
    saveTrackField(track, "done", newVal);
    showToast(newVal === "TRUE" ? "Done \u2713" : "Not done", "");

    const row = getDataRow(currentIndex);
    if (row) {
      row.classList.toggle("is-done", newVal === "TRUE");
      const cols = COLUMNS[currentSource];
      const doneColIdx = cols.findIndex(function (c) {
        return c.key === "done";
      });
      if (doneColIdx >= 0 && row.children[doneColIdx]) {
        const cb = row.children[doneColIdx].querySelector(
          'input[type="checkbox"]',
        );
        if (cb) cb.checked = newVal === "TRUE";
      }
    }
  }

  // -- Jump to next undecided ---------------------------------
  function jumpNextUndecided() {
    if (filteredTracks.length === 0) return;
    const start = currentIndex + 1;
    for (let offset = 0; offset < filteredTracks.length; offset++) {
      const idx = (start + offset) % filteredTracks.length;
      const t = filteredTracks[idx];
      if (!t.status || t.status === "") {
        selectRow(idx);
        if (autoPlay) playTrack(idx);
        return;
      }
    }
    showToast("All tracks decided", "");
  }

  // -- Sort ---------------------------------------------------
  function handleSort(key) {
    if (key === "_index" || key === "_in_library") return;
    if (sortKey === key) {
      sortDir *= -1;
    } else {
      sortKey = key;
      sortDir = 1;
    }
    applyFilters();
    if (currentIndex >= 0 && currentIndex < filteredTracks.length) {
      selectRow(currentIndex);
    }
  }

  // -- Keyboard -----------------------------------------------
  document.addEventListener("keydown", function (e) {
    // Ctrl/Cmd+K: toggle AI Chat (works even from input fields)
    if ((e.ctrlKey || e.metaKey) && e.code === "KeyK") {
      e.preventDefault();
      if (aiChatPanel.classList.contains("hidden")) {
        var track =
          currentIndex >= 0 && currentIndex < filteredTracks.length
            ? filteredTracks[currentIndex]
            : null;
        if (track && currentSource === "unsorted") {
          openAiChat(track);
        }
      } else {
        closeAiChat();
      }
      return;
    }

    const tag = e.target.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") {
      if (e.code === "Escape") {
        e.target.blur();
        e.preventDefault();
      }
      return;
    }

    switch (e.code) {
      case "Space":
        e.preventDefault();
        togglePlayPause();
        break;

      case "ArrowDown":
        e.preventDefault();
        navigateRow(1, e.shiftKey);
        break;

      case "ArrowUp":
        e.preventDefault();
        navigateRow(-1, e.shiftKey);
        break;

      case "Enter":
        e.preventDefault();
        if (currentIndex >= 0) playTrack(currentIndex);
        break;

      case "Escape":
        stopPlayback();
        break;

      case "KeyA":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          setStatus("accept");
        }
        break;

      case "KeyR":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          setStatus("reject");
        }
        break;

      case "KeyV":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          setStatus("review");
        }
        break;

      case "KeyG":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          applyGenreSuggestion();
        }
        break;

      case "KeyE":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          if (e.shiftKey) {
            // Shift+E: batch enrich selected tracks
            if (currentSource === "unsorted" && !ghostReview.locked) {
              var batchTids = [];
              if (selectedSet.size > 0) {
                filteredTracks.forEach(function (t) {
                  var tid2 = t.track_id || t.file_hash || "";
                  if (selectedSet.has(tid2)) batchTids.push(tid2);
                });
              } else if (currentIndex >= 0 && currentIndex < filteredTracks.length) {
                var t2 = filteredTracks[currentIndex];
                batchTids.push(t2.track_id || t2.file_hash || "");
              }
              startBatchEnrich(batchTids);
            }
          } else {
            // e: single-track enrich (existing behaviour)
            if (currentIndex >= 0 && currentIndex < filteredTracks.length) {
              if (!ghostReview.locked) requestEnrichTrack(filteredTracks[currentIndex]);
            }
          }
        }
        break;

      case "KeyN":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          jumpNextUndecided();
        }
        break;

      case "KeyD":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          toggleDone();
        }
        break;

      case "Digit1":
      case "Digit2":
      case "Digit3":
      case "Digit4":
      case "Digit5":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          setRatingForCurrent(parseInt(e.code.charAt(5)));
        }
        break;

      case "Digit0":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          setRatingForCurrent(0);
        }
        break;

      case "KeyZ":
        if (e.ctrlKey || e.metaKey) {
          e.preventDefault();
          performUndo();
        }
        break;

      case "PageDown":
        e.preventDefault();
        navigateRow(10, e.shiftKey);
        break;

      case "PageUp":
        e.preventDefault();
        navigateRow(-10, e.shiftKey);
        break;

      case "Home":
        e.preventDefault();
        if (filteredTracks.length > 0) selectRow(0);
        break;

      case "End":
        e.preventDefault();
        if (filteredTracks.length > 0) selectRow(filteredTracks.length - 1);
        break;
    }
  });

  // -- UI event listeners -------------------------------------
  sourceSelect.addEventListener("change", function () {
    currentIndex = -1;
    sortKey = null;
    sortDir = 1;
    selectedSet.clear();
    selectionAnchor = -1;
    undoStack = [];
    // Reset all filters
    filterStatus.value = "";
    filterDone.value = "";
    filterBpm.value = "";
    filterKey.value = "";
    filterRating.value = "";
    updateFilterVisibility();
    loadTracks(sourceSelect.value).then(function () {
      if (sourceSelect.value === "library") {
        populateKeyFilter();
      }
    });
  });

  searchInput.addEventListener("input", function () {
    applyFilters();
    if (filteredTracks.length > 0 && currentIndex < 0) selectRow(0);
  });

  filterStatus.addEventListener("change", function () {
    applyFilters();
    if (filteredTracks.length > 0) selectRow(0);
  });

  filterDone.addEventListener("change", function () {
    applyFilters();
    if (filteredTracks.length > 0) selectRow(0);
  });

  filterBpm.addEventListener("change", function () {
    applyFilters();
    if (filteredTracks.length > 0) selectRow(0);
  });

  filterKey.addEventListener("change", function () {
    applyFilters();
    if (filteredTracks.length > 0) selectRow(0);
  });

  filterRating.addEventListener("change", function () {
    applyFilters();
    if (filteredTracks.length > 0) selectRow(0);
  });

  // -- Batch bar events ---------------------------------------
  batchClear.addEventListener("click", function () {
    clearSelection();
  });

  batchApply.addEventListener("click", function () {
    applyBatchFields();
  });

  batchYear.addEventListener("keydown", function (e) {
    if (e.key === "Enter") applyBatchFields();
  });

  batchArtist.addEventListener("keydown", function (e) {
    if (e.key === "Enter") applyBatchFields();
  });

  batchGenre.addEventListener("keydown", function (e) {
    if (e.key === "Enter") applyBatchFields();
  });

  // Status -> Dest mirror (matches AUTO_DEST behavior in setStatus)
  batchStatus.addEventListener("change", function () {
    if (AUTO_DEST[batchStatus.value]) {
      batchDest.value = AUTO_DEST[batchStatus.value];
    }
  });

  // -- Init ---------------------------------------------------
  // Check AI availability (non-blocking)
  fetch("/api/ai-status")
    .then(function (r) {
      return r.json();
    })
    .then(function (d) {
      aiAvailable = !!d.available;
      if (ctxAiSuggest && !aiAvailable) {
        ctxAiSuggest.style.display = "none";
      }
      var ctxAiChat = document.getElementById("ctx-ai-chat");
      if (ctxAiChat && !aiAvailable) {
        ctxAiChat.style.display = "none";
      }
    })
    .catch(function () {
      aiAvailable = false;
    });

  // ── Review toolbar event wiring ────────────────────────────────────────────

  // Apply all ticked
  reviewApplyBtn.addEventListener("click", function () {
    if (!ghostReview.locked) applyAllTicked();
  });

  // Cancel review mode
  reviewCancelBtn.addEventListener("click", function () {
    var hasTicked = ghostCountTicked() > 0;
    if (hasTicked && !confirm("Cancel review? Unapplied proposals will be discarded.")) return;
    exitReviewMode(true);
  });

  // Auto-threshold slider
  if (reviewAutoThresholdInput) {
    reviewAutoThresholdInput.value = ghostReview.autoThreshold;
    reviewAutoThresholdInput.addEventListener("change", function () {
      ghostReview.autoThreshold = parseFloat(this.value) || 0.85;
      localStorage.setItem("ghost-auto-accept", String(ghostReview.autoThreshold));
      // Re-init ticked defaults for all proposals
      for (var tid in ghostReview.proposals) {
        if (Object.prototype.hasOwnProperty.call(ghostReview.proposals, tid)) {
          ghostInitTicked(tid, ghostReview.proposals[tid]);
        }
      }
      // Re-render all ghost rows
      for (var tid2 in ghostReview.proposals) {
        if (!Object.prototype.hasOwnProperty.call(ghostReview.proposals, tid2)) continue;
        for (var i = 0; i < filteredTracks.length; i++) {
          if ((filteredTracks[i].track_id || filteredTracks[i].file_hash) === tid2) {
            renderGhostRow(filteredTracks[i], ghostReview.proposals[tid2]);
            break;
          }
        }
      }
      updateReviewToolbar();
    });
  }

  // Bulk column toggle buttons
  reviewToolbar.querySelectorAll(".review-bulk-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var field = this.dataset.field;
      // Check current state: if any ticked, untick all; else tick all
      var anyTicked = false;
      for (var tid in ghostReview.ticked) {
        if (ghostReview.ticked[tid] && ghostReview.ticked[tid][field]) { anyTicked = true; break; }
      }
      var newState = !anyTicked;
      for (var tid2 in ghostReview.proposals) {
        if (!Object.prototype.hasOwnProperty.call(ghostReview.proposals, tid2)) continue;
        if (field in ghostReview.proposals[tid2]) {
          if (!ghostReview.ticked[tid2]) ghostReview.ticked[tid2] = {};
          ghostReview.ticked[tid2][field] = newState;
        }
      }
      // Re-render all ghost rows
      for (var tid3 in ghostReview.proposals) {
        if (!Object.prototype.hasOwnProperty.call(ghostReview.proposals, tid3)) continue;
        for (var i = 0; i < filteredTracks.length; i++) {
          if ((filteredTracks[i].track_id || filteredTracks[i].file_hash) === tid3) {
            renderGhostRow(filteredTracks[i], ghostReview.proposals[tid3]);
            break;
          }
        }
      }
      updateReviewToolbar();
    });
  });

  // Filter buttons
  reviewToolbar.querySelectorAll(".review-filter-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      reviewToolbar.querySelectorAll(".review-filter-btn").forEach(function (b) {
        b.classList.remove("active");
      });
      this.classList.add("active");
      ghostReview.filter = this.dataset.filter;
      applyGhostFilter();
    });
  });

  // ── On load: hydrate ghost rows from sidecar (surviving page refresh) ──────
  Promise.all([loadGenres(), loadLibraryIndex()]).then(function () {
    loadTracks("unsorted").then(function () {
      // After table is rendered, show any pending proposals from sidecar
      if (currentSource === "unsorted") hydrateAllFromSidecar();
    });
  });
})();
