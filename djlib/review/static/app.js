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
  const EXPORT_DISPOSITIONS = new Set(["library", "reject", "mixes"]);

  // Batch selection
  let selectedSet = new Set();
  let selectionAnchor = -1;

  // Undo stack (max 50)
  const MAX_UNDO = 50;
  let undoStack = [];

  // Version comparison state
  let versionGroups = {};    // group_id → { group_id, members[] }
  let trackGroupId = {};     // track_id → group_id
  let expandedGroups = new Set();

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
  const filterDisposition = document.getElementById("filter-disposition");
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
  const toast = document.getElementById("toast");
  const contextMenu = document.getElementById("context-menu");
  const reviewModeLabel = document.getElementById("review-mode-label");
  const enrichProgressWrap = document.getElementById("enrich-progress-wrap");
  const enrichProgressBar = document.getElementById("enrich-progress-bar");
  const enrichStepLabel = document.getElementById("enrich-step-label");
  const aiBanner = document.getElementById("ai-banner");
  const aiBannerGenre = document.getElementById("ai-banner-genre");
  const aiBannerConfidence = document.getElementById("ai-banner-confidence");
  const aiBannerReasoning = document.getElementById("ai-banner-reasoning");
  const aiBannerAccept = document.getElementById("ai-banner-accept");
  const aiBannerDismiss = document.getElementById("ai-banner-dismiss");
  const ctxAiSuggest = document.getElementById("ctx-ai-suggest");
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
  const batchGroup = document.getElementById("batch-group");
  const batchDisposition = document.getElementById("batch-disposition");
  const batchRating = document.getElementById("batch-rating");
  const batchPlaylist = document.getElementById("batch-playlist");
  const batchApply = document.getElementById("batch-apply");
  const batchApplyCount = document.getElementById("batch-apply-count");

  // AI availability (checked once on load)
  let aiAvailable = false;
  let aiPending = false; // prevents double-clicks
  let aiBannerTrack = null; // track the banner is showing for
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
      { key: "near_duplicate_of", label: "~Dup", width: "40px", type: "near-dup" },
      {
        key: "file_path",
        label: "Folder",
        width: "110px",
        cls: "col-folder",
        fmt: function (fp) {
          if (!fp) return "—";
          var parts = fp.replace(/\\/g, "/").split("/");
          var dirs = parts.slice(0, -1).filter(Boolean);
          if (!dirs.length) return "—";
          return dirs.slice(-2).join("/");
        },
      },
      { key: "genre", label: "Genre", width: "11%", type: "genre-select" },
      { key: "occasion_tags", label: "Group", width: "90px", type: "editable", cls: "col-group" },
      { key: "playlists", label: "Playlists", width: "130px", type: "playlist-multi" },
      { key: "disposition", label: "Disp", width: "88px", type: "disposition-select" },
      {
        key: "year",
        label: "Year",
        width: "48px",
        type: "editable",
        cls: "col-bpm",
      },
      { key: "bpm", label: "BPM", width: "46px", cls: "col-bpm" },
      { key: "duration_seconds", label: "Time", width: "55px", cls: "col-bpm", fmt: fmtDuration },
      { key: "key_camelot", label: "Key", width: "40px", cls: "col-key" },
      { key: "audio_quality", label: "Quality", width: "70px", type: "quality-badge" },
      { key: "color", label: "Clr", width: "26px", type: "color-dot" },
      { key: "rating", label: "Rating", width: "72px", type: "rating" },
    ],
    library: [
      { key: "_index", label: "#", width: "36px" },
      { key: "artist", label: "Artist", width: "18%" },
      { key: "title", label: "Title", width: "22%" },
      { key: "bpm", label: "BPM", width: "50px", cls: "col-bpm" },
      { key: "key", label: "Key", width: "44px", cls: "col-key" },
      {
        key: "duration_seconds",
        label: "Time",
        width: "55px",
        cls: "col-bpm",
        fmt: fmtDuration,
      },
      { key: "audio_quality", label: "Quality", width: "70px", type: "quality-badge" },
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
      { key: "playlists", label: "Playlists", width: "130px", type: "playlist-multi" },
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
      { key: "disposition", label: "Disp", width: "88px", type: "disposition-select" },
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
      { key: "disposition", label: "Disp", width: "88px", type: "disposition-select" },
    ],
  };

  // -- Helpers ------------------------------------------------
  function fmtTime(sec) {
    if (!sec || !isFinite(sec) || isNaN(sec)) return "0:00";
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

  // -- Audio quality badge ------------------------------------
  function qualityBadgeHtml(val) {
    if (!val) return "";
    var v = val.trim().toUpperCase();
    var cls;
    if (v === "FLAC" || v === "WAV" || v === "AIFF") {
      cls = "qbadge-lossless";
    } else if (/^MP3 3[2-9]\d|^AAC 3[2-9]\d/.test(v)) {
      cls = "qbadge-high";
    } else if (/^MP3 2[56]\d|^AAC 2[56]\d/.test(v)) {
      cls = "qbadge-mid";
    } else {
      cls = "qbadge-low";
    }
    return '<span class="quality-badge ' + cls + '">' + escHtml(val) + "</span>";
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

  // -- Version comparison ----------------------------------------

  async function loadVersionGroups() {
    versionGroups = {};
    trackGroupId = {};
    if (currentSource !== "unsorted" && currentSource !== "library") return;
    try {
      var r = await fetch("/api/version-groups?source=" + currentSource);
      var data = await r.json();
      (data.groups || []).forEach(function (g) {
        versionGroups[g.group_id] = g;
        g.members.forEach(function (m) {
          var tid = m.track_id || m.file_hash || "";
          if (tid) trackGroupId[tid] = g.group_id;
        });
      });
    } catch (e) {
      // non-fatal — version badges just won't show
    }
  }

  function toggleVersionGroup(gid) {
    if (expandedGroups.has(gid)) {
      expandedGroups.delete(gid);
    } else {
      expandedGroups.add(gid);
    }
    renderVersionChildRows();
  }

  function renderVersionChildRows() {
    // Remove previous child rows and clear expanded markers.
    tableBody.querySelectorAll("tr.version-child").forEach(function (r) { r.remove(); });
    tableBody.querySelectorAll("tr.version-expanded").forEach(function (r) {
      r.classList.remove("version-expanded");
    });
    if (!expandedGroups.size) return;

    // Build tid→filteredTracks-index map once (O(N), single pass, no DOM touch).
    // We'll use data-idx to find TRs — avoids scanning all tr[data-tid] in the DOM.
    var tidToIdx = {};
    for (var fi = 0; fi < filteredTracks.length; fi++) {
      var ftid = trackId(filteredTracks[fi]);
      if (ftid) tidToIdx[ftid] = fi;
    }

    var mainCols = COLUMNS[currentSource] || COLUMNS.unsorted;
    var totalCols = mainCols.length + (isEditableSource() ? 1 : 0);

    // Iterate only over expanded groups (O(k), k ≪ N) instead of all DOM rows.
    expandedGroups.forEach(function (gid) {
      var group = versionGroups[gid];
      if (!group) return;

      // Find the first group member that appears in the current filtered/sorted view.
      var parentTid = null;
      var parentIdx = Infinity;
      group.members.forEach(function (m) {
        var mTid = m.track_id || m.file_hash || "";
        var idx = tidToIdx[mTid];
        if (idx !== undefined && idx < parentIdx) {
          parentIdx = idx;
          parentTid = mTid;
        }
      });
      if (parentTid === null) return; // group not visible in current view

      var parentTr = tableBody.querySelector("tr[data-idx='" + parentIdx + "']");
      if (!parentTr) return;
      parentTr.classList.add("version-expanded");

      // Detect mixed artists (covers alert) using simple lowercase+alnum slug.
      var artistKeys = new Set();
      group.members.forEach(function (m) {
        var a = (m.artist || "").toLowerCase().replace(/[^a-z0-9]/g, "");
        if (a) artistKeys.add(a);
      });
      var hasMixedArtists = artistKeys.size > 1;

      var afterRow = parentTr;
      group.members.forEach(function (member) {
        var mTid = member.track_id || member.file_hash || "";
        if (mTid === parentTid) return;
        var childTr = buildVersionChildRow(member, gid, totalCols, hasMixedArtists);
        afterRow.insertAdjacentElement("afterend", childTr);
        afterRow = childTr;
      });
    });
  }

  function buildVersionChildRow(track, gid, totalCols, hasMixedArtists) {
    var tr = document.createElement("tr");
    tr.className = "version-child";
    var tid = track.track_id || track.file_hash || "";
    if (tid) tr.dataset.tid = tid;
    var disp = (track.disposition || "").toLowerCase();
    if (disp) tr.classList.add("disp-" + disp);

    var vInfo = track._version_info || track.version_info || "";
    var dur = fmtDuration(track.duration_seconds);
    var bpm = track.bpm || "";
    var key = track.key_camelot || track.key || "";
    var fp = track.file_path || "";
    var ext = fp ? fp.split(".").pop().toUpperCase() : "";
    var src = track._source || track.source || "";
    var nearDup = track.near_duplicate_of || "";
    var rating = track.rating || "";

    // Cell 1 — version info + badges (spans ~25% of columns)
    var span1 = Math.max(1, Math.round(totalCols * 0.22));
    var td1 = document.createElement("td");
    td1.colSpan = span1;
    td1.className = "version-info-cell";

    var viSpan = document.createElement("span");
    viSpan.className = "version-info-label";
    viSpan.textContent = vInfo || "Original";
    td1.appendChild(viSpan);

    if (hasMixedArtists) {
      var alertBadge = document.createElement("span");
      alertBadge.className = "badge-covers-alert";
      alertBadge.textContent = "COVER?";
      alertBadge.title = "Artists differ in this group — may be a cover, not a version";
      td1.appendChild(alertBadge);
    }

    if (nearDup) {
      var ndBadge = document.createElement("span");
      ndBadge.className = "badge-near-dup";
      ndBadge.textContent = "~DUP";
      ndBadge.title = "Near-duplicate flagged";
      ndBadge.style.marginLeft = "4px";
      td1.appendChild(ndBadge);
    }
    tr.appendChild(td1);

    // Cell 2 — dimmed artist — title (~35%)
    var span2 = Math.max(1, Math.round(totalCols * 0.32));
    var td2 = document.createElement("td");
    td2.colSpan = span2;
    td2.className = "col-artist col-title";
    td2.style.fontSize = "11px";
    td2.textContent = (track.artist || "") + " — " + (track.title || "");
    tr.appendChild(td2);

    // Cell 3 — metadata summary (remaining)
    var span3 = Math.max(1, totalCols - span1 - span2);
    var td3 = document.createElement("td");
    td3.colSpan = span3;
    td3.className = "col-bpm";
    var parts = [];
    if (dur) parts.push(dur);
    if (bpm) parts.push(bpm + " BPM");
    if (key) parts.push(key);
    if (ext && ["AIFF", "WAV", "FLAC", "MP3", "M4A"].indexOf(ext) >= 0) parts.push(ext);
    if (src) parts.push("[" + src + "]");
    if (rating) parts.push(ratingToStars(rating));
    td3.innerHTML = parts.join("  <span style='opacity:0.3'>·</span>  ");
    tr.appendChild(td3);

    // P button — set as preferred
    var tdBtn = document.createElement("td");
    var pBtn = document.createElement("button");
    pBtn.textContent = "P";
    pBtn.title = "Set as preferred (5★), set others in group to 3★";
    pBtn.className = "btn-version-prefer";
    pBtn.style.cssText = "font-size:10px;padding:1px 5px;cursor:pointer;background:rgba(99,179,237,0.15);border:1px solid rgba(99,179,237,0.3);border-radius:3px;color:#63b3ed;";
    pBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      setVersionPreferred(tid, gid);
    });
    tdBtn.appendChild(pBtn);
    tr.appendChild(tdBtn);

    return tr;
  }

  function setVersionPreferred(preferredTid, gid) {
    var group = versionGroups[gid];
    if (!group) return;
    var peerIds = group.members
      .map(function (m) { return m.track_id || m.file_hash || ""; })
      .filter(function (tid) { return tid && tid !== preferredTid; });

    fetch("/api/tracks/version-group-rating", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        preferred_id: preferredTid,
        peer_ids: peerIds,
        source: currentSource,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          showToast("Preferred set — " + data.updated + " track(s) rated", "");
          // Update in-memory rating for tracks in the group
          group.members.forEach(function (m) {
            var mid = m.track_id || m.file_hash || "";
            m.rating = mid === preferredTid ? "5" : "3";
            var live = allTracks.find(function (t) { return (t.track_id || t.file_hash) === mid; });
            if (live) live.rating = m.rating;
          });
          renderVersionChildRows();
        } else {
          showToast("Rating update failed: " + (data.error || "unknown"), "");
        }
      })
      .catch(function () { showToast("Rating update failed", ""); });
  }

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
    expandedGroups.clear();
    // Exit review mode when switching sources (ghost rows belong to unsorted only)
    if (ghostReview.active) exitReviewMode(true);
    // Reset batch bar inputs to avoid ghost values across source switches
    if (batchGenre) batchGenre.value = "";
    if (batchYear) batchYear.value = "";
    if (batchArtist) batchArtist.value = "";
    if (batchGroup) batchGroup.value = "";
    if (batchDisposition) batchDisposition.value = "";
    if (batchRating) batchRating.value = "";
    if (batchPlaylist) batchPlaylist.value = "";
    updateBatchBar();
    applyFilters();
    loadVersionGroups().then(function () { renderVersionChildRows(); });
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
    filterDisposition.style.display = isUnsorted ? "" : "none";
    // Library-only filters
    filterBpm.style.display = isLib ? "" : "none";
    filterKey.style.display = isLib ? "" : "none";
    filterRating.style.display = isLib || isUnsorted ? "" : "none";
  }

  // -- Filtering & sorting ------------------------------------
  function applyFilters() {
    const q = searchInput.value.toLowerCase().trim();
    const sf = filterDisposition.value;
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
            if (t.disposition && t.disposition !== "") return false;
          } else {
            if (t.disposition !== sf) return false;
          }
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
        var va = (a[sortKey] || "").toString();
        var vb = (b[sortKey] || "").toString();
        const na = parseFloat(va),
          nb = parseFloat(vb);
        if (!isNaN(na) && !isNaN(nb)) return (na - nb) * sortDir;
        // Strip leading "The " for alphabetical sort (display unchanged)
        if (sortKey === "artist") {
          va = va.replace(/^the\s+/i, "");
          vb = vb.replace(/^the\s+/i, "");
        }
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
    updateCtaGroup();
    // Update current tab's track count badge
    var countEl = document.getElementById("tab-count-" + currentSource);
    if (countEl) countEl.textContent = allTracks.length || "";
  }

  // -- Stats bar ----------------------------------------------
  function updateStats() {
    if (currentSource === "unsorted") {
      var lib = 0, rej = 0, mix = 0, later = 0, und = 0;
      for (var i = 0; i < allTracks.length; i++) {
        var t = allTracks[i];
        var d = (t.disposition || "").toLowerCase();
        if (d === "library") lib++;
        else if (d === "reject") rej++;
        else if (d === "mixes") mix++;
        else if (d === "later") later++;
        else und++;
      }
      var parts = [];
      if (lib) parts.push('<span class="stat-lib">' + lib + " lib</span>");
      if (rej) parts.push('<span class="stat-reject">' + rej + " rej</span>");
      if (mix) parts.push('<span class="stat-mixes">' + mix + " mix</span>");
      if (later) parts.push('<span class="stat-later">' + later + " later</span>");
      parts.push('<span class="stat-undecided">' + und + " todo</span>");
      statsBar.innerHTML = parts.join(" \u00b7 ");
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
        prRej = 0;
      for (var i = 0; i < allTracks.length; i++) {
        var t = allTracks[i];
        if (t.in_dj_software === "yes") inLib++;
        var rat = parseFloat(t.rating) || 0;
        if (rat > 0) prRated++;
        var dest = (t.destination || "").toLowerCase();
        if (dest === "library") prLib++;
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
      ? ghostReview.done + "/" + ghostReview.total
      : ghostReview.done + "/" + ghostReview.total + " done";
    reviewProgress.textContent = label;

    if (ghostReview.locked) {
      reviewModeLabel.textContent = "ENRICHING";
      reviewModeLabel.classList.add("enriching");
      enrichProgressWrap.classList.remove("hidden");
      var pct = ghostReview.total > 0
        ? ((ghostReview.done + (ghostReview.subProgress || 0)) / ghostReview.total) * 100
        : 0;
      enrichProgressBar.style.width = Math.min(100, pct) + "%";
      enrichStepLabel.textContent = ghostReview.currentStep || "";
    } else {
      reviewModeLabel.textContent = "REVIEW";
      reviewModeLabel.classList.remove("enriching");
      enrichProgressWrap.classList.add("hidden");
      enrichStepLabel.textContent = "";
    }

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
        td.innerHTML = '<div class="ghost-inner"><span class="ghost-lead">↳</span> <span class="ghost-conf ' + confCls + '">' + confPct + '%</span></div>';
        tr.appendChild(td);
        return;
      }

      var field = col.key;
      if (!GHOST_FIELDS.includes(field)) {
        td.innerHTML = "";
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

      td.innerHTML = '<div class="ghost-inner">' + toggleSpan + " " + valSpan + " " + srcSpan + " " + wasSpan + '</div>';
      td.querySelector(".ghost-toggle").addEventListener("click", function (e) {
        e.stopPropagation();
        toggleGhostCell(tid, field);
      });
      td.querySelector(".ghost-toggle").addEventListener("keydown", function (e) {
        if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleGhostCell(tid, field); }
      });

      tr.appendChild(td);
    });

    // Add col-kebab cell with "Accept N" button (mirrors data rows, fixes column alignment)
    if (isEditableSource()) {
      var tickedMap = ghostReview.ticked[tid] || {};
      var nTicked = Object.keys(tickedMap).filter(function (f) { return tickedMap[f]; }).length;
      var kebabTd = document.createElement("td");
      kebabTd.className = "col-kebab ghost-cell";
      var acceptBtn = document.createElement("button");
      acceptBtn.className = "ghost-apply-row-btn";
      acceptBtn.dataset.tid = tid;
      acceptBtn.textContent = "Accept " + nTicked;
      acceptBtn.disabled = nTicked === 0;
      acceptBtn.title = nTicked === 0 ? "No fields selected" : "Apply " + nTicked + " field" + (nTicked === 1 ? "" : "s") + " for this track";
      acceptBtn.addEventListener("click", function () { applyGhostRow(tid); });
      kebabTd.appendChild(acceptBtn);
      tr.appendChild(kebabTd);
    }

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

  var _applyInFlight = false;

  // Send accepted fields to backend and clean up ghost row(s)
  function applyGhostApplications(applications) {
    if (!applications.length) return;
    if (_applyInFlight) return;
    _applyInFlight = true;
    fetch("/api/apply-enrichment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ applications: applications }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        _applyInFlight = false;
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
        // Refresh table rows: update cell values in-place, then flash animation
        var changedTids = new Set(applications.map(function (a) { return a.track_id; }));
        reRenderDataRows(changedTids);
        updateReviewToolbar();
        showToast("Applied " + data.applied + " track(s)", "");
        if (Object.keys(ghostReview.proposals).length === 0) {
          exitReviewMode(false);
        }
      })
      .catch(function () { _applyInFlight = false; showToast("Apply request failed", ""); });
  }

  // Re-render specific data rows in-place: update cell values + flash animation
  function reRenderDataRows(tids) {
    var cols = COLUMNS[currentSource] || COLUMNS.unsorted;
    for (var i = 0; i < filteredTracks.length; i++) {
      var t = filteredTracks[i];
      var tid = t.track_id || t.file_hash || "";
      if (!tids.has(tid)) continue;
      var dataRow = getDataRow(i);
      if (!dataRow) continue;
      // Update cell values in-place without rebuilding the row (preserves event listeners)
      var tds = dataRow.querySelectorAll("td");
      for (var ci = 0; ci < cols.length && ci < tds.length; ci++) {
        var col = cols[ci];
        var td = tds[ci];
        if (col.type === "editable") {
          td.textContent = t[col.key] || "";
          td.title = t[col.key] || "";
        } else if (col.type === "genre-select" || col.type === "dest-select") {
          var sel = td.querySelector("select");
          if (sel) sel.value = t[col.key] || "";
        }
      }
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
          ghostReview.subProgress = data.sub_progress || 0;
          ghostReview.currentStep = data.current_step || "";
          ghostReview.currentTrack = data.current_track || "";

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
        // Table is already rendered at this point (hydration is async) — insert ghost rows now
        tids.forEach(function (tid) {
          for (var i = 0; i < filteredTracks.length; i++) {
            if ((filteredTracks[i].track_id || filteredTracks[i].file_hash) === tid) {
              renderGhostRow(filteredTracks[i], ghostReview.proposals[tid]);
              break;
            }
          }
        });
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

  // ── Column resize ────────────────────────────────────────────────────────
  var _colWidths = {};  // {source:key → px string} — loaded from localStorage

  function _colWidthKey(source, colKey) { return "colW:" + source + ":" + colKey; }

  function _loadColWidths(source, cols) {
    for (const col of cols) {
      var stored = localStorage.getItem(_colWidthKey(source, col.key));
      if (stored) _colWidths[source + ":" + col.key] = stored;
    }
  }

  function attachColResizers(source, cols) {
    _loadColWidths(source, cols);
    const ths = tableHead.querySelectorAll("th");
    ths.forEach(function (th) {
      const key = th.dataset.key;
      if (!key || key === "_select") return;  // skip checkbox col
      // Apply saved width
      var saved = _colWidths[source + ":" + key];
      if (saved) th.style.width = saved;
      // Skip if already has a resizer
      if (th.querySelector(".col-resizer")) return;
      var handle = document.createElement("div");
      handle.className = "col-resizer";
      handle.addEventListener("mousedown", function (e) {
        e.preventDefault();
        e.stopPropagation();
        handle.classList.add("resizing");
        var startX = e.clientX;
        var startW = th.offsetWidth;
        var _didResize = false;
        function onUp() {
          handle.classList.remove("resizing");
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          var finalW = th.style.width;
          _colWidths[source + ":" + key] = finalW;
          localStorage.setItem(_colWidthKey(source, key), finalW);
          // Block the th click (sort) that fires after mouseup
          if (_didResize) {
            th.addEventListener("click", function blockSort(ev) {
              ev.stopImmediatePropagation();
              th.removeEventListener("click", blockSort);
            }, true);
          }
        }
        function onMove(ev) {
          _didResize = true;
          var newW = Math.max(30, startW + ev.clientX - startX);
          th.style.width = newW + "px";
        }
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });
      th.appendChild(handle);
    });
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
    // Kebab column header (editable sources only)
    if (isEditableSource()) {
      var kebabTh = document.createElement("th");
      kebabTh.className = "col-kebab";
      hr.appendChild(kebabTh);
    }
    tableHead.appendChild(hr);
    attachColResizers(currentSource, cols);

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
      var disp = (track.disposition || "").toLowerCase();
      if (disp) tr.classList.add("disp-" + disp);

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
          td.classList.add("col-index");
          var gid = trackGroupId[trackTid];
          var vGroup = gid ? versionGroups[gid] : null;
          if (vGroup && vGroup.members.length > 1) {
            var vBadge = document.createElement("span");
            vBadge.className = "badge-versions";
            vBadge.textContent = "V:" + vGroup.members.length;
            vBadge.title = "Click or press X to compare versions";
            vBadge.addEventListener(
              "click",
              (function (vGid) {
                return function (e) {
                  e.stopPropagation();
                  toggleVersionGroup(vGid);
                };
              })(gid),
            );
            td.appendChild(vBadge);
          } else {
            td.textContent = i + 1;
          }
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
        } else if (col.type === "disposition-select") {
          const sel = buildDispositionSelect(track[col.key]);
          sel.addEventListener(
            "change",
            (function (track, col, sel, tr) {
              return function (e) {
                e.stopPropagation();
                var prev = track[col.key];
                track[col.key] = sel.value;
                saveTrackField(track, col.key, sel.value);
                // Update row class
                if (prev) tr.classList.remove("disp-" + prev);
                if (sel.value) tr.classList.add("disp-" + sel.value);
                _applyDispositionSelectClass(sel);
                updateStats();
              };
            })(track, col, sel, tr),
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
        } else if (col.type === "quality-badge") {
          td.innerHTML = qualityBadgeHtml(track[col.key]);
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
        } else if (col.type === "near-dup") {
          var nearDupId = track[col.key];
          if (nearDupId) {
            var dupMatch = allTracks.find(function (t) { return trackId(t) === nearDupId; });
            var dupLabel = dupMatch
              ? (dupMatch.artist || "") + " — " + (dupMatch.title || "")
              : "in library";
            var dupBadge = document.createElement("span");
            dupBadge.className = "badge-near-dup";
            dupBadge.textContent = "~DUP";
            dupBadge.title = "Near-duplicate of: " + dupLabel;
            td.appendChild(dupBadge);
          }
        } else if (col.type === "playlist-multi") {
          renderPlaylistCell(td, track);
          td.classList.add("cell-playlist");
          td.addEventListener(
            "click",
            (function (td, track) {
              return function (e) {
                e.stopPropagation();
                openPlaylistPanel(td, track);
              };
            })(td, track),
          );
        } else {
          const raw = track[col.key] || "";
          td.textContent = col.fmt ? col.fmt(raw) : raw;
          td.title = raw;
        }

        // Missing metadata indicators (only on editable sources)
        if (isEditableSource()) {
          if ((col.key === "artist" || col.key === "title" || col.key === "year" || col.key === "genre") && !(track[col.key] || "").trim()) {
            var mis = document.createElement("span");
            mis.className = "t-miss";
            mis.textContent = "?";
            td.appendChild(mis);
          }
          if ((col.key === "bpm" || col.key === "key_camelot") && !(track[col.key] || "").trim()) {
            var mic = document.createElement("span");
            mic.className = "t-miss-crit";
            mic.textContent = "!";
            td.appendChild(mic);
          }
        }

        tr.appendChild(td);
      }

      // Kebab ⋮ column (editable sources only)
      if (isEditableSource()) {
        var kebabTd = document.createElement("td");
        kebabTd.className = "col-kebab";
        var kebabBtn = document.createElement("button");
        kebabBtn.className = "row-kebab";
        kebabBtn.textContent = "⋮";
        kebabBtn.title = "More actions";
        kebabTd.appendChild(kebabBtn);
        kebabBtn.addEventListener(
          "click",
          (function (track) {
            return function (e) {
              e.stopPropagation();
              showKebabMenu(e, track);
            };
          })(track)
        );
        tr.appendChild(kebabTd);
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
    renderVersionChildRows();

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
  const DISPOSITION_OPTIONS = ["", "library", "reject", "mixes", "later"];
  const DISPOSITION_LABELS = { "": "\u2014", library: "Library", reject: "Reject", mixes: "Mixes", later: "Later" };

  function _applyDispositionSelectClass(sel) {
    sel.className = "inline-select";
    if (sel.value) sel.classList.add("disp-sel-" + sel.value);
  }

  function buildDispositionSelect(currentValue) {
    const sel = document.createElement("select");
    for (const d of DISPOSITION_OPTIONS) {
      const o = document.createElement("option");
      o.value = d;
      o.textContent = DISPOSITION_LABELS[d] || d;
      if (d === currentValue) o.selected = true;
      sel.appendChild(o);
    }
    _applyDispositionSelectClass(sel);
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
    } else {
      batchBar.classList.remove("hidden");
      batchCount.textContent = n + " selected";
      batchApplyCount.textContent = n;
    }
    updateCtaGroup();
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
    const groupVal = batchGroup.value.trim();
    const dispositionVal = batchDisposition.value;
    const ratingVal = batchRating.value;

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

    const playlistVal = batchPlaylist ? batchPlaylist.value.trim() : "";

    if (genreVal) fields.genre = genreVal;
    if (yearVal) fields.year = yearVal;
    if (artistVal) fields.artist = artistVal;
    if (groupVal !== "") fields.occasion_tags = groupVal;
    if (dispositionVal) fields.disposition = dispositionVal;
    if (ratingVal !== "") fields.rating = ratingVal;  // "0" is valid for clearing
    // playlists handled separately below (per-track append, not overwrite)

    if (selectedSet.size === 0) {
      showToast("Nothing selected", "reject");
      return;
    }
    if (Object.keys(fields).length === 0 && !playlistVal) {
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

    // Playlist append — per-track (each track may have different existing playlists)
    if (playlistVal) {
      var plAdded = 0;
      for (var _pi = 0; _pi < targets.length; _pi++) {
        var _t = targets[_pi].track;
        var _current = (_t.playlists || "").split("|").map(function (s) { return s.trim(); }).filter(Boolean);
        if (!_current.includes(playlistVal)) {
          _current.push(playlistVal);
          var _newVal = _current.join("|");
          _t.playlists = _newVal;
          saveTrackField(_t, "playlists", _newVal);
          plAdded++;
        }
      }
      if (plAdded > 0) showToast("Added " + plAdded + " tracks to \"" + playlistVal + "\"");
      if (batchPlaylist) batchPlaylist.value = "";
    }

    // Optimistic update in memory + push single-batch undo
    if (Object.keys(fields).length > 0) pushBatchUndo(rollback, fields);
    for (const { track } of targets) {
      for (const [k, v] of Object.entries(fields)) {
        track[k] = v;
      }
    }

    const n = track_ids.length;
    if (Object.keys(fields).length === 0) { applyFilters(); return; }
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
    batchGroup.value = "";
    batchDisposition.value = "";
    batchRating.value = "";
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

    // Preload next track audio
    preloadNextTrack();
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

  // Shared menu action handler (used by context menu + kebab menu)
  function handleMenuAction(action, track) {
    if (!track) return;
    const artist = (track.artist || "").trim();
    const title = (track.title || "").trim();
    const version = (track.version_info || "").trim();
    const path = audioPath(track);
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
        requestAiGenreSuggest(track);
        break;
      case "ai-suggest-year":
        requestAiYearSuggest(track);
        break;
      case "identify-track":
        requestAiIdentify(track);
        break;
      case "ai-classify":
        requestAiClassify(track);
        break;
      case "ai-chat":
        openAiChat(track);
        break;
      case "enrich-track":
        if (currentSource === "unsorted" && !ghostReview.locked)
          startBatchEnrich([trackId(track)]);
        break;
      case "swap-artist-title":
        requestSwapArtistTitle(track);
        break;
      case "scrape-url":
        openUrlInputDialog(track);
        break;
    }
  }

  // Handle context menu actions
  contextMenu.addEventListener("click", function (e) {
    const btn = e.target.closest("button");
    if (!btn || btn.disabled || !contextTrack) return;
    handleMenuAction(btn.dataset.action, contextTrack);
    hideContextMenu();
  });

  // -- Kebab menu -----------------------------------------------
  const kebabMenu = document.getElementById("kebab-menu");
  let kebabTrack = null;

  function showKebabMenu(e, track) {
    kebabTrack = track;
    const path = audioPath(track);
    const hasPath = !!path;
    const isUnsorted = currentSource === "unsorted";

    kebabMenu.querySelectorAll("button.kebab-item").forEach(function (btn) {
      const action = btn.dataset.action;
      if (action === "show-finder" || action === "copy-filename") btn.disabled = !hasPath;
      if (action === "enrich-track" || action === "swap-artist-title" || action === "scrape-url") btn.disabled = !isUnsorted;
    });

    const menuW = 210;
    const menuH = 330;
    let x = e.clientX;
    let y = e.clientY;
    if (x + menuW > window.innerWidth) x = window.innerWidth - menuW - 8;
    if (y + menuH > window.innerHeight) y = window.innerHeight - menuH - 8;
    kebabMenu.style.left = x + "px";
    kebabMenu.style.top = y + "px";
    kebabMenu.classList.remove("hidden");
  }

  function hideKebabMenu() {
    kebabMenu.classList.add("hidden");
    kebabTrack = null;
  }

  document.addEventListener("click", function (e) {
    if (!kebabMenu.contains(e.target)) hideKebabMenu();
  });

  kebabMenu.addEventListener("click", function (e) {
    const btn = e.target.closest("button.kebab-item");
    if (!btn || btn.disabled || !kebabTrack) return;
    handleMenuAction(btn.dataset.action, kebabTrack);
    hideKebabMenu();
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

  // ── Year suggestion banner ──────────────────────────────────────────────────
  var yearBanner = document.getElementById("year-banner");
  var yearBannerYear = document.getElementById("year-banner-year");
  var yearBannerConf = document.getElementById("year-banner-confidence");
  var yearBannerReasoning = document.getElementById("year-banner-reasoning");
  var yearBannerAccept = document.getElementById("year-banner-accept");
  var yearBannerDismiss = document.getElementById("year-banner-dismiss");
  var yearBannerTrack = null;
  var yearPending = false;

  function hideYearBanner() {
    if (yearBanner) yearBanner.classList.add("hidden");
    yearBannerTrack = null;
  }

  if (yearBannerDismiss) yearBannerDismiss.addEventListener("click", hideYearBanner);

  if (yearBannerAccept) {
    yearBannerAccept.addEventListener("click", function () {
      var yr = yearBannerAccept.dataset.year;
      var track = yearBannerTrack;
      if (!yr || !track) return;
      track.year = yr;
      saveTrackField(track, "year", yr);
      showToast("Year set to " + yr);
      hideYearBanner();
      renderTable();
    });
  }

  function requestAiYearSuggest(track) {
    if (!track) return;
    if (yearPending) { showToast("Year suggestion already in progress…"); return; }

    yearPending = true;
    yearBannerTrack = track;
    yearBanner.classList.remove("hidden");
    yearBanner.classList.add("ai-loading");
    yearBannerYear.textContent = "Analyzing…";
    yearBannerConf.textContent = "";
    yearBannerReasoning.textContent = "";
    yearBannerAccept.style.display = "none";
    yearBannerDismiss.style.display = "inline-block";

    var body = {
      track_id: trackId(track),
      context: {
        artist:                track.artist || "",
        title:                 track.title || "",
        version:               track.version_info || "",
        genre:                 track.genre || track.genre_suggest || "",
        bpm:                   track.bpm || "",
        original_release_year: track.original_release_year || "",
        year_suggest:          track.year_suggest || "",
      },
    };

    fetch("/api/suggest-year", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        yearPending = false;
        yearBanner.classList.remove("ai-loading");
        if (data.error) {
          showToast("AI error: " + data.error);
          hideYearBanner();
          return;
        }
        yearBannerYear.textContent = data.year || "?";
        var conf = data.confidence ? Math.round(data.confidence * 100) + "%" : "";
        yearBannerConf.textContent = conf;
        yearBannerReasoning.textContent = data.reasoning || "";
        yearBannerReasoning.title = data.reasoning || "";
        yearBannerAccept.dataset.year = data.year || "";
        yearBannerAccept.style.display = data.year ? "inline-block" : "none";
      })
      .catch(function () {
        yearPending = false;
        yearBanner.classList.remove("ai-loading");
        showToast("Year suggestion failed");
        hideYearBanner();
      });
  }

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

  function updatePlayerBar(track) {
    if (!track) {
      nowArtist.textContent = "\u2014";
      nowTitle.textContent = "\u2014";
      return;
    }
    nowArtist.textContent = track.artist || "Unknown Artist";
    const ver = track.version_info ? " (" + track.version_info + ")" : "";
    nowTitle.textContent = (track.title || "Unknown Title") + ver;
  }

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
    updatePlayerBar(track);
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
    updatePlayerBar(null);
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

  // -- Disposition / actions ----------------------------------
  function setDisposition(disposition) {
    if (!isEditableSource()) return;

    const targets = [];
    if (selectedSet.size > 0) {
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
      pushUndo(track, { disposition: disposition });

      var prev = track.disposition || "";
      track.disposition = disposition;
      saveTrackField(track, "disposition", disposition);

      const row = getDataRow(idx);
      if (row) {
        if (prev) row.classList.remove("disp-" + prev);
        if (disposition) row.classList.add("disp-" + disposition);

        const cols = COLUMNS[currentSource];
        const dispColIdx = cols.findIndex(function (c) { return c.key === "disposition"; });
        if (dispColIdx >= 0 && row.children[dispColIdx]) {
          const sel = row.children[dispColIdx].querySelector("select");
          if (sel) { sel.value = disposition; _applyDispositionSelectClass(sel); }
        }
      }
    }

    const label = (DISPOSITION_LABELS[disposition] || disposition).toUpperCase();
    const toastClass = disposition === "reject" ? "reject" : disposition === "library" ? "accept" : "";
    showToast(targets.length > 1 ? label + " \u00d7" + targets.length : label, toastClass);
    updateStats();
    clearSelection();

    if (targets.length === 1 && currentIndex < filteredTracks.length - 1) {
      setTimeout(function () { navigateRow(1, false); }, 150);
    }
  }

  // -- Jump to next undecided ---------------------------------
  function jumpNextUndecided() {
    if (filteredTracks.length === 0) return;
    const start = currentIndex + 1;
    for (let offset = 0; offset < filteredTracks.length; offset++) {
      const idx = (start + offset) % filteredTracks.length;
      const t = filteredTracks[idx];
      if (!t.disposition || t.disposition === "" || t.disposition === "later") {
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

  // -- Playlists ------------------------------------------------
  var allPlaylistNames = [];
  var activePlaylistPanel = null; // {panel, track, selected}

  function loadPlaylistNames() {
    fetch("/api/playlists")
      .then(function (r) { return r.json(); })
      .then(function (names) { allPlaylistNames = names || []; })
      .catch(function () {});
  }

  function parsePlaylists(raw) {
    if (!raw) return [];
    return raw.split("|").map(function (s) { return s.trim(); }).filter(Boolean);
  }

  function renderPlaylistCell(td, track) {
    var playlists = parsePlaylists(track.playlists);
    td.innerHTML = "";
    var max = 2;
    playlists.slice(0, max).forEach(function (c) {
      var chip = document.createElement("span");
      chip.className = "playlist-chip";
      chip.textContent = c;
      td.appendChild(chip);
    });
    if (playlists.length > max) {
      var more = document.createElement("span");
      more.className = "playlist-chip playlist-chip-more";
      more.textContent = "+" + (playlists.length - max);
      td.appendChild(more);
    }
  }

  function openPlaylistPanel(anchorTd, track) {
    closePlaylistPanel(false);

    var source = currentSource;
    var selected = parsePlaylists(track.playlists).slice();
    var rect = anchorTd.getBoundingClientRect();

    var panel = document.createElement("div");
    panel.className = "playlist-panel";
    panel.style.top = (rect.bottom + window.scrollY) + "px";
    panel.style.left = rect.left + "px";

    function rebuild(filterVal) {
      panel.innerHTML = "";

      // Selected chips
      if (selected.length > 0) {
        var selectedWrap = document.createElement("div");
        selectedWrap.className = "playlist-panel-selected";
        selected.forEach(function (c) {
          var chip = document.createElement("span");
          chip.className = "playlist-chip playlist-chip-removable";
          chip.innerHTML = escHtml(c) + '<button class="playlist-chip-remove" title="Remove">×</button>';
          chip.querySelector(".playlist-chip-remove").addEventListener("click", function (e) {
            e.stopPropagation();
            selected = selected.filter(function (x) { return x !== c; });
            rebuild(input.value);
          });
          selectedWrap.appendChild(chip);
        });
        panel.appendChild(selectedWrap);
      }

      // Filter input
      var input = document.createElement("input");
      input.className = "playlist-panel-input";
      input.placeholder = "Filter or create…";
      input.value = filterVal || "";
      panel.appendChild(input);

      // Options list
      var list = document.createElement("div");
      list.className = "playlist-panel-list";
      var query = (filterVal || "").toLowerCase().trim();
      var filtered = allPlaylistNames.filter(function (n) {
        return n.toLowerCase().includes(query) && !selected.includes(n);
      });

      var highlighted = 0;

      function buildOptions() {
        list.innerHTML = "";
        filtered.forEach(function (name, i) {
          var opt = document.createElement("div");
          opt.className = "playlist-panel-option" + (i === highlighted ? " highlighted" : "");
          opt.textContent = name;
          opt.addEventListener("mousedown", function (e) {
            e.preventDefault();
            if (!selected.includes(name)) selected.push(name);
            rebuild(input.value);
          });
          list.appendChild(opt);
        });
        // Create option
        var exactMatch = allPlaylistNames.some(function (n) { return n.toLowerCase() === query; });
        if (query && !exactMatch && !selected.map(function(s){return s.toLowerCase();}).includes(query)) {
          var createOpt = document.createElement("div");
          createOpt.className = "playlist-panel-option playlist-panel-create" + (filtered.length === highlighted ? " highlighted" : "");
          createOpt.textContent = 'Create "' + query + '"';
          createOpt.addEventListener("mousedown", function (e) {
            e.preventDefault();
            var newName = input.value.trim();
            if (newName && !selected.includes(newName)) {
              selected.push(newName);
              if (!allPlaylistNames.includes(newName)) allPlaylistNames.push(newName);
            }
            rebuild("");
          });
          list.appendChild(createOpt);
        }
      }
      buildOptions();
      panel.appendChild(list);

      input.addEventListener("input", function () {
        highlighted = 0;
        query = input.value.toLowerCase().trim();
        filtered = allPlaylistNames.filter(function (n) {
          return n.toLowerCase().includes(query) && !selected.includes(n);
        });
        buildOptions();
      });

      input.addEventListener("keydown", function (e) {
        var totalOpts = list.querySelectorAll(".playlist-panel-option").length;
        if (e.key === "ArrowDown") {
          e.preventDefault();
          highlighted = Math.min(highlighted + 1, totalOpts - 1);
          buildOptions();
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          highlighted = Math.max(highlighted - 1, 0);
          buildOptions();
        } else if (e.key === "Enter") {
          e.preventDefault();
          var opts = list.querySelectorAll(".playlist-panel-option");
          if (opts[highlighted]) opts[highlighted].dispatchEvent(new MouseEvent("mousedown"));
        } else if (e.key === "Escape") {
          e.preventDefault();
          closePlaylistPanel(true);
        }
      });

      // Focus input after rebuild
      setTimeout(function () { input.focus(); }, 0);
    }

    rebuild("");
    document.body.appendChild(panel);
    activePlaylistPanel = { panel: panel, track: track, source: source, getSelected: function() { return selected; } };

    // Close on outside click
    setTimeout(function () {
      document.addEventListener("mousedown", _playlistPanelOutsideClick);
    }, 0);
  }

  function _playlistPanelOutsideClick(e) {
    if (activePlaylistPanel && !activePlaylistPanel.panel.contains(e.target)) {
      closePlaylistPanel(true);
    }
  }

  function closePlaylistPanel(save) {
    if (!activePlaylistPanel) return;
    var panel = activePlaylistPanel.panel;
    var track = activePlaylistPanel.track;
    var source = activePlaylistPanel.source;
    var selected = activePlaylistPanel.getSelected();
    document.removeEventListener("mousedown", _playlistPanelOutsideClick);
    panel.remove();
    activePlaylistPanel = null;

    if (save) {
      var prevPlaylists = parsePlaylists(track.playlists);
      var changed = JSON.stringify(prevPlaylists.slice().sort()) !== JSON.stringify(selected.slice().sort());
      if (changed) {
        track.playlists = selected.join("|");
        // Re-render the cell
        var row = tbody.querySelector("tr[data-tid='" + (track.track_id || "") + "']");
        if (row) {
          var td = row.querySelector(".cell-playlist");
          if (td) renderPlaylistCell(td, track);
        }
        fetch("/api/track/" + encodeURIComponent(track.track_id) + "/playlists", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ playlists: selected, source: source }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.ok) {
              showToast("Playlists saved", "");
              loadPlaylistNames();
            } else {
              showToast("Save failed: " + (data.error || ""), "");
            }
          })
          .catch(function () { showToast("Save failed", ""); });
      }
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

      case "KeyL":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          setDisposition("library");
        }
        break;

      case "KeyR":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          setDisposition("reject");
        }
        break;

      case "KeyM":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          setDisposition("mixes");
        }
        break;

      case "Period":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          setDisposition("later");
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
            if (batchTids.length > 0) startBatchEnrich(batchTids);
          }
        }
        break;

      case "KeyC":
        if (!e.ctrlKey && !e.metaKey && !e.altKey && (currentSource === "library" || currentSource === "unsorted")) {
          e.preventDefault();
          if (currentIndex >= 0 && currentIndex < filteredTracks.length) {
            var playlistTrack = filteredTracks[currentIndex];
            var playlistRow = tbody.querySelector("tr[data-idx='" + currentIndex + "']");
            var playlistTd = playlistRow ? playlistRow.querySelector(".cell-playlist") : null;
            if (playlistTd) openPlaylistPanel(playlistTd, playlistTrack);
          }
        }
        break;

      case "KeyN":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          jumpNextUndecided();
        }
        break;

      case "KeyX":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          if (currentIndex >= 0 && currentIndex < filteredTracks.length) {
            var xTrack = filteredTracks[currentIndex];
            var xTid = trackId(xTrack);
            var xGid = trackGroupId[xTid];
            if (xGid) toggleVersionGroup(xGid);
          }
        }
        break;

      case "KeyP":
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          if (currentIndex >= 0 && currentIndex < filteredTracks.length) {
            var pTrack = filteredTracks[currentIndex];
            var pTid = trackId(pTrack);
            var pGid = trackGroupId[pTid];
            if (pGid) setVersionPreferred(pTid, pGid);
          }
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
    filterDisposition.value = "";
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

  // -- Source tab click handlers ---------------------------------
  var sourceTabs = document.getElementById("source-tabs");
  if (sourceTabs) {
    sourceTabs.addEventListener("click", function (e) {
      var btn = e.target.closest(".tab-btn");
      if (!btn) return;
      var src = btn.dataset.source;

      // Artists tab: the IIFE's artistsTabBtn click listener fires next and
      // handles showArtistsPanel / hideArtistsPanel including active state.
      if (src === "artists") return;

      // Playlists tab — handled by the playlists IIFE below
      if (src === "playlists") return;

      // Regular source tabs — update visual, update hidden select, dispatch change
      sourceTabs.querySelectorAll(".tab-btn").forEach(function (b) {
        b.classList.toggle("active", b === btn);
      });
      sourceSelect.value = src;
      sourceSelect.dispatchEvent(new Event("change"));
    });
  }

  // -- CTA group renderer ----------------------------------------
  var ctaGroup = document.getElementById("cta-group");

  function updateCtaGroup() {
    if (!ctaGroup) return;
    ctaGroup.innerHTML = "";

    var selCount = selectedSet.size;

    if (currentSource === "unsorted") {
      var scanBtn = document.createElement("button");
      scanBtn.className = "cta secondary";
      scanBtn.textContent = "SCAN";
      scanBtn.title = "Scan unsorted folder for new audio files";
      scanBtn.addEventListener("click", startScan);
      ctaGroup.appendChild(scanBtn);

      var enrichCount = selCount > 0 ? selCount : allTracks.length;
      var enrichBtn = document.createElement("button");
      enrichBtn.className = "cta secondary";
      enrichBtn.innerHTML = "ENRICH" + (enrichCount ? ' <span class="cta-n">' + enrichCount + "</span>" : "");
      enrichBtn.title = "Enrich selected (or all) tracks via online APIs";
      enrichBtn.disabled = allTracks.length === 0;
      enrichBtn.addEventListener("click", function () {
        var tids = selCount > 0 ? Array.from(selectedSet) : allTracks.map(trackId);
        startBatchEnrich(tids);
      });
      ctaGroup.appendChild(enrichBtn);

      var exportCount = selCount > 0 ? selCount : filteredTracks.filter(function (t) { return (t.disposition || "") !== ""; }).length;
      var exportBtn = document.createElement("button");
      exportBtn.className = "cta primary";
      exportBtn.innerHTML = "EXPORT" + (exportCount ? ' <span class="cta-n">' + exportCount + "</span>" : "");
      exportBtn.title = "Move tracks to library and sync Rekordbox";
      exportBtn.disabled = exportCount === 0;
      exportBtn.addEventListener("click", (function (n) {
        return function () { startExport(n); };
      })(exportCount));
      ctaGroup.appendChild(exportBtn);

    } else if (currentSource === "library" || currentSource === "library-review" || currentSource === "library-fix") {
      var syncBtn = document.createElement("button");
      syncBtn.className = "cta secondary";
      syncBtn.textContent = "SYNC";
      syncBtn.title = "Sync library with Rekordbox/Traktor";
      syncBtn.addEventListener("click", function () {
        showToast("SYNC: use CLI djlib sync-dj-libraries", "");
      });
      ctaGroup.appendChild(syncBtn);

    } else if (currentSource === "playlists") {
      var plRefreshBtn = document.createElement("button");
      plRefreshBtn.className = "cta secondary";
      plRefreshBtn.textContent = "REFRESH";
      plRefreshBtn.addEventListener("click", function () {
        if (window._playlistsRefresh) window._playlistsRefresh();
      });
      ctaGroup.appendChild(plRefreshBtn);

      var rbOpen = window._playlistsRbOpen && window._playlistsRbOpen();
      var pushBtn = document.createElement("button");
      pushBtn.className = "cta primary";
      pushBtn.textContent = "PUSH";
      pushBtn.disabled = !!rbOpen;
      pushBtn.title = rbOpen
        ? "Close Rekordbox first, then push"
        : "Push djlib playlists to Rekordbox (Rekordbox must be closed)";
      pushBtn.addEventListener("click", startPushPlaylists);
      ctaGroup.appendChild(pushBtn);

      if (rbOpen) {
        var rbWarn = document.createElement("span");
        rbWarn.className = "pl-rb-open-cta-warn";
        rbWarn.textContent = "⚠ RB open";
        ctaGroup.appendChild(rbWarn);
      }
    }
  }

  // Recalculate CTAs whenever source changes or selection changes
  sourceSelect.addEventListener("change", function () {
    updateCtaGroup();
  });

  searchInput.addEventListener("input", function () {
    applyFilters();
    if (filteredTracks.length > 0 && currentIndex < 0) selectRow(0);
  });

  filterDisposition.addEventListener("change", function () {
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

  // ── Scan ─────────────────────────────────────────────────────────────────────
  var scanOverlay = document.getElementById("scan-overlay");
  var scanBar = document.getElementById("scan-bar");
  var scanStats = document.getElementById("scan-stats");
  var scanFileLabel = document.getElementById("scan-file-label");
  var scanCloseBtn = document.getElementById("scan-close-btn");
  var _scanPollTimer = null;

  function startScan() {
    // Clear any ghost timer from a previous scan whose modal was closed mid-poll
    if (_scanPollTimer) { clearTimeout(_scanPollTimer); _scanPollTimer = null; }
    fetch("/api/scan-start", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { showToast(d.error, ""); return; }
        scanOverlay.classList.remove("hidden");
        document.querySelector(".scan-card-title").textContent = "Scanning inbox…";
        scanBar.style.width = "0";
        scanStats.textContent = "Starting…";
        scanFileLabel.textContent = "";
        scanCloseBtn.classList.add("hidden");
        _pollScan();
      })
      .catch(function () { showToast("Could not start scan", ""); });
  }

  function _pollScan() {
    // Stop polling if modal was closed while a fetch was in-flight
    if (scanOverlay.classList.contains("hidden")) return;
    fetch("/api/scan-status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (scanOverlay.classList.contains("hidden")) return; // closed during fetch
        var pct = d.total > 0 ? Math.round((d.processed / d.total) * 100) : 0;
        scanBar.style.width = pct + "%";
        if (d.last_file) {
          var parts = d.last_file.replace(/\\/g, "/").split("/");
          scanFileLabel.textContent = parts[parts.length - 1] || d.last_file;
        }
        if (d.state === "running") {
          scanStats.textContent = d.processed + " / " + d.total + " files · " + (d.added || 0) + " new";
          _scanPollTimer = setTimeout(_pollScan, 800);
        } else if (d.state === "done") {
          scanBar.style.width = "100%";
          scanStats.textContent = "Done — " + (d.added || 0) + " new tracks · " + (d.errors || 0) + " errors";
          document.querySelector(".scan-card-title").textContent = "Scan complete";
          scanCloseBtn.classList.remove("hidden");
          if (currentSource === "unsorted") loadTracks("unsorted");
        } else if (d.state === "error") {
          scanStats.textContent = "Error: " + (d.message || "unknown");
          document.querySelector(".scan-card-title").textContent = "Scan failed";
          scanCloseBtn.classList.remove("hidden");
        } else {
          // idle — scan finished before first poll
          scanBar.style.width = "100%";
          scanStats.textContent = "Scan complete";
          scanCloseBtn.classList.remove("hidden");
        }
      })
      .catch(function () {
        if (!scanOverlay.classList.contains("hidden")) {
          _scanPollTimer = setTimeout(_pollScan, 1500);
        }
      });
  }

  if (scanCloseBtn) {
    scanCloseBtn.addEventListener("click", function () {
      if (_scanPollTimer) { clearTimeout(_scanPollTimer); _scanPollTimer = null; }
      scanOverlay.classList.add("hidden");
      document.querySelector(".scan-card-title").textContent = "Scanning inbox…";
    });
  }

  // ── Export ────────────────────────────────────────────────────────────────────
  var exportBanner = document.getElementById("export-banner");
  var exportBannerLabel = document.getElementById("export-banner-label");
  var exportBannerBar = document.getElementById("export-banner-bar");
  var exportBannerClose = document.getElementById("export-banner-close");
  var _exportPollTimer = null;

  var pushBanner = document.getElementById("push-banner");
  var pushBannerLabel = document.getElementById("push-banner-label");
  var pushBannerBar = document.getElementById("push-banner-bar");
  var pushBannerClose = document.getElementById("push-banner-close");
  var _pushPollTimer = null;

  function startExport(readyCount) {
    if (!confirm("Export " + readyCount + " track" + (readyCount !== 1 ? "s" : "") + " to library?\n\nFiles will be moved to their destination folders and Rekordbox will be updated.")) return;

    fetch("/api/export-start", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { showToast(d.error, ""); return; }
        exportBanner.classList.remove("hidden");
        exportBannerBar.classList.add("indeterminate");
        exportBannerLabel.textContent = "Exporting " + (d.total || readyCount) + " tracks…";
        exportBannerClose.classList.add("hidden");
        _pollExport();
      })
      .catch(function () { showToast("Could not start export", ""); });
  }

  function _pollExport() {
    fetch("/api/export-status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.state === "running") {
          _exportPollTimer = setTimeout(_pollExport, 1000);
        } else if (d.state === "done") {
          exportBannerBar.classList.remove("indeterminate");
          exportBannerBar.style.width = "100%";
          exportBannerLabel.textContent = d.message || ("Exported " + (d.moved || 0) + " tracks");
          exportBannerClose.classList.remove("hidden");
          if (currentSource === "unsorted") loadTracks("unsorted");
        } else if (d.state === "error") {
          exportBannerBar.classList.remove("indeterminate");
          exportBannerBar.style.width = "0";
          exportBannerLabel.textContent = "Export failed: " + (d.message || "unknown error");
          exportBannerClose.classList.remove("hidden");
        }
      })
      .catch(function () {
        _exportPollTimer = setTimeout(_pollExport, 1500);
      });
  }

  if (exportBannerClose) {
    exportBannerClose.addEventListener("click", function () {
      if (_exportPollTimer) { clearTimeout(_exportPollTimer); _exportPollTimer = null; }
      exportBanner.classList.add("hidden");
      exportBannerBar.style.width = "0";
      exportBannerBar.classList.remove("indeterminate");
    });
  }

  function startPushPlaylists() {
    if (!confirm("Push playlists to Rekordbox?\n\nRekordbox must be closed. Existing djlib-managed playlists will be rebuilt.")) return;
    fetch("/api/push-playlists", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { showToast(d.error, ""); return; }
        pushBanner.classList.remove("hidden");
        pushBannerBar.classList.add("indeterminate");
        pushBannerLabel.textContent = "Pushing playlists to Rekordbox…";
        pushBannerClose.classList.add("hidden");
        _pollPush();
      })
      .catch(function () { showToast("Could not start push", ""); });
  }

  function _pollPush() {
    fetch("/api/push-playlists-status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.state === "running") {
          _pushPollTimer = setTimeout(_pollPush, 1000);
        } else if (d.state === "done") {
          pushBannerBar.classList.remove("indeterminate");
          pushBannerBar.style.width = "100%";
          pushBannerLabel.textContent = d.message || "Push complete";
          pushBannerClose.classList.remove("hidden");
        } else if (d.state === "error") {
          pushBannerBar.classList.remove("indeterminate");
          pushBannerBar.style.width = "0";
          pushBannerLabel.textContent = "Push failed: " + (d.message || "unknown error");
          pushBannerClose.classList.remove("hidden");
        }
      })
      .catch(function () {
        _pushPollTimer = setTimeout(_pollPush, 1500);
      });
  }

  if (pushBannerClose) {
    pushBannerClose.addEventListener("click", function () {
      if (_pushPollTimer) { clearTimeout(_pushPollTimer); _pushPollTimer = null; }
      pushBanner.classList.add("hidden");
      pushBannerBar.style.width = "0";
      pushBannerBar.classList.remove("indeterminate");
    });
  }

  // ── Overflow menu (⋯ button) ─────────────────────────────────────────────
  var overflowWrap = document.getElementById("overflow-wrap");
  var overflowBtn  = document.getElementById("overflow-btn");
  var overflowMenu = document.getElementById("overflow-menu");

  if (overflowBtn && overflowMenu) {
    overflowBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      overflowMenu.classList.toggle("hidden");
    });
    document.addEventListener("click", function () {
      overflowMenu.classList.add("hidden");
    });
  }

  // ── Unapply last run ─────────────────────────────────────────────────────
  var unapplyBanner      = document.getElementById("unapply-banner");
  var unapplyBannerLabel = document.getElementById("unapply-banner-label");
  var unapplyBannerBar   = document.getElementById("unapply-banner-bar");
  var unapplyBannerClose = document.getElementById("unapply-banner-close");
  var _unapplyPollTimer  = null;

  var overflowUnapply = document.getElementById("overflow-unapply");
  if (overflowUnapply) {
    overflowUnapply.addEventListener("click", function () {
      overflowMenu.classList.add("hidden");
      if (!confirm("Move all tracks from the last apply run back to unsorted?\n\nFiles will be physically moved and removed from library.csv.")) return;
      fetch("/api/unapply-last-run", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.error) { showToast(d.error, ""); return; }
          unapplyBanner.classList.remove("hidden");
          unapplyBannerBar.classList.add("indeterminate");
          unapplyBannerLabel.textContent = "Unapplying last run…";
          unapplyBannerClose.classList.add("hidden");
          _pollUnapply();
        })
        .catch(function () { showToast("Could not start unapply", ""); });
    });
  }

  function _pollUnapply() {
    fetch("/api/unapply-status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.state === "running") {
          unapplyBannerLabel.textContent = d.message || "Unapplying…";
          _unapplyPollTimer = setTimeout(_pollUnapply, 800);
        } else if (d.state === "done") {
          unapplyBannerBar.classList.remove("indeterminate");
          unapplyBannerBar.style.width = "100%";
          unapplyBannerLabel.textContent = d.message || "Unapply complete";
          unapplyBannerClose.classList.remove("hidden");
          loadTracks(currentSource);
        } else {
          unapplyBannerBar.classList.remove("indeterminate");
          unapplyBannerBar.style.width = "0";
          unapplyBannerLabel.textContent = "Unapply failed: " + (d.message || "unknown error");
          unapplyBannerClose.classList.remove("hidden");
        }
      })
      .catch(function () {
        _unapplyPollTimer = setTimeout(_pollUnapply, 1500);
      });
  }

  if (unapplyBannerClose) {
    unapplyBannerClose.addEventListener("click", function () {
      if (_unapplyPollTimer) { clearTimeout(_unapplyPollTimer); _unapplyPollTimer = null; }
      unapplyBanner.classList.add("hidden");
      unapplyBannerBar.style.width = "0";
      unapplyBannerBar.classList.remove("indeterminate");
    });
  }

  // ── On load: hydrate ghost rows from sidecar (surviving page refresh) ──────
  loadPlaylistNames();

  Promise.all([loadGenres(), loadLibraryIndex()]).then(function () {
    loadTracks("unsorted").then(function () {
      // After table is rendered, show any pending proposals from sidecar
      if (currentSource === "unsorted") hydrateAllFromSidecar();
    });
  });

  // ── Artists normalization tab ────────────────────────────────────────────
  (function () {
    const artistsTabBtn = document.getElementById("artists-tab-btn");
    const artistsPanel = document.getElementById("artists-panel");
    const tableContainer = document.getElementById("table-container");
    const artistsRefreshBtn = document.getElementById("artists-refresh-btn");
    const artistsShowDismissed = document.getElementById("artists-show-dismissed");
    const artistsClusterCount = document.getElementById("artists-cluster-count");
    const artistsClusterList = document.getElementById("artists-cluster-list");

    let artistsActive = false;
    let focusedClusterIdx = -1;
    let currentClusters = [];

    function showArtistsPanel() {
      artistsActive = true;
      tableContainer.style.display = "none";
      artistsPanel.classList.remove("hidden");
      // Sync tab active state
      document.querySelectorAll("#source-tabs .tab-btn").forEach(function (b) { b.classList.remove("active"); });
      artistsTabBtn.classList.add("active");
      loadArtistClusters();
    }

    function hideArtistsPanel() {
      artistsActive = false;
      artistsPanel.classList.add("hidden");
      tableContainer.style.display = "";
      artistsTabBtn.classList.remove("active");
      // Re-activate the current source tab
      document.querySelectorAll("#source-tabs .tab-btn").forEach(function (b) {
        b.classList.toggle("active", b.dataset.source === currentSource);
      });
    }

    function confidenceTier(score) {
      if (score >= 90) return "high";
      if (score >= 70) return "med";
      return "low";
    }

    async function loadArtistClusters() {
      const showDismissed = artistsShowDismissed.checked ? "1" : "0";
      artistsClusterList.innerHTML = '<div class="artists-empty">Loading…</div>';
      try {
        const resp = await fetch("/api/artist-clusters?show_dismissed=" + showDismissed);
        currentClusters = await resp.json();
        renderClusters(currentClusters);
      } catch (e) {
        artistsClusterList.innerHTML = '<div class="artists-empty">Error loading clusters.</div>';
      }
    }

    function renderClusters(clusters) {
      focusedClusterIdx = -1;
      if (!clusters.length) {
        artistsClusterList.innerHTML = '<div class="artists-empty">No artist variants found — your library looks clean.</div>';
        artistsClusterCount.textContent = "";
        return;
      }
      artistsClusterCount.textContent = clusters.length + " cluster" + (clusters.length !== 1 ? "s" : "");
      artistsClusterList.innerHTML = "";
      clusters.forEach(function (cluster, idx) {
        const card = buildClusterCard(cluster, idx);
        artistsClusterList.appendChild(card);
      });
      if (clusters.length > 0) focusCluster(0);
    }

    function buildClusterCard(cluster, idx) {
      const card = document.createElement("div");
      card.className = "artist-cluster-card";
      card.dataset.idx = idx;

      const tier = confidenceTier(cluster.confidence);
      const methodLabel = cluster.method === "mbz" ? "MBZ" : "fuzzy";
      const summary = cluster.members.join(" · ");

      card.innerHTML =
        '<div class="artist-cluster-header">' +
          '<span class="artist-cluster-toggle">▶</span>' +
          '<span class="confidence-badge confidence-' + tier + '">' + Math.round(cluster.confidence) + '%</span>' +
          '<span class="method-badge' + (cluster.method === "mbz" ? " mbz" : "") + '">' + methodLabel + '</span>' +
          '<span class="cluster-members-summary">' + escHtml(summary) + '</span>' +
          '<span class="cluster-track-count">' + (cluster.track_count || 0) + ' tracks</span>' +
        '</div>' +
        '<div class="artist-cluster-body" style="display:none">' +
          '<div class="cluster-members-list">' +
            cluster.members.map(function (m) {
              return '<div class="cluster-member-row">' + escHtml(m) + '</div>';
            }).join("") +
          '</div>' +
          '<div class="cluster-canonical-row">' +
            '<span class="cluster-canonical-label">Canonical:</span>' +
            '<input class="cluster-canonical-input" type="text" value="' + escHtml(cluster.canonical || cluster.members[0]) + '" />' +
          '</div>' +
          '<div class="cluster-actions">' +
            '<button class="cluster-merge-btn">Merge &amp; tag</button>' +
            '<button class="cluster-skip-btn">Skip</button>' +
          '</div>' +
        '</div>';

      // Toggle expand on header click
      card.querySelector(".artist-cluster-header").addEventListener("click", function () {
        toggleCluster(card, idx);
      });

      card.querySelector(".cluster-merge-btn").addEventListener("click", function (e) {
        e.stopPropagation();
        mergeCluster(cluster, card, idx);
      });

      card.querySelector(".cluster-skip-btn").addEventListener("click", function (e) {
        e.stopPropagation();
        dismissCluster(cluster, idx);
      });

      return card;
    }

    function toggleCluster(card, idx) {
      const body = card.querySelector(".artist-cluster-body");
      const toggle = card.querySelector(".artist-cluster-toggle");
      const isOpen = body.style.display !== "none";
      body.style.display = isOpen ? "none" : "";
      toggle.textContent = isOpen ? "▶" : "▼";
      focusCluster(idx);
    }

    function focusCluster(idx) {
      document.querySelectorAll(".artist-cluster-card").forEach(function (c) {
        c.classList.remove("focused");
      });
      focusedClusterIdx = idx;
      const card = artistsClusterList.querySelector('[data-idx="' + idx + '"]');
      if (card) {
        card.classList.add("focused");
        card.scrollIntoView({ block: "nearest" });
      }
    }

    async function mergeCluster(cluster, card, idx) {
      const input = card.querySelector(".cluster-canonical-input");
      const canonical = (input ? input.value : "").trim();
      if (!canonical) { showToast("Enter a canonical name first"); return; }

      const mergeBtn = card.querySelector(".cluster-merge-btn");
      mergeBtn.disabled = true;
      mergeBtn.textContent = "Merging…";

      try {
        const resp = await fetch("/api/artist-clusters/merge", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ canonical: canonical, variants: cluster.members, apply_tags: true }),
        });
        const result = await resp.json();
        if (result.ok) {
          showToast("Merged: " + canonical + " (" + ((result.updated_unsorted || 0) + (result.updated_library || 0)) + " tracks)");
          loadArtistClusters();
        } else {
          showToast("Error: " + (result.error || "unknown"));
          mergeBtn.disabled = false;
          mergeBtn.textContent = "Merge & tag";
        }
      } catch (e) {
        showToast("Network error");
        mergeBtn.disabled = false;
        mergeBtn.textContent = "Merge & tag";
      }
    }

    async function dismissCluster(cluster, idx) {
      try {
        await fetch("/api/artist-clusters/dismiss", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ members: cluster.members }),
        });
        showToast("Skipped: " + cluster.members.join(" · "));
        loadArtistClusters();
      } catch (e) {
        showToast("Network error");
      }
    }

    // Keyboard shortcuts when artists panel is active
    document.addEventListener("keydown", function (e) {
      if (!artistsActive) return;
      if (e.target.tagName === "INPUT") return;

      const cards = Array.from(artistsClusterList.querySelectorAll(".artist-cluster-card"));
      if (!cards.length) return;

      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = e.key === "ArrowDown"
          ? Math.min(focusedClusterIdx + 1, cards.length - 1)
          : Math.max(focusedClusterIdx - 1, 0);
        focusCluster(next);
        return;
      }

      if (e.key === "Enter" && focusedClusterIdx >= 0) {
        e.preventDefault();
        toggleCluster(cards[focusedClusterIdx], focusedClusterIdx);
        return;
      }

      if ((e.key === "m" || e.key === "M") && focusedClusterIdx >= 0) {
        e.preventDefault();
        const card = cards[focusedClusterIdx];
        mergeCluster(currentClusters[focusedClusterIdx], card, focusedClusterIdx);
        return;
      }

      if ((e.key === "s" || e.key === "S") && focusedClusterIdx >= 0) {
        e.preventDefault();
        dismissCluster(currentClusters[focusedClusterIdx], focusedClusterIdx);
        return;
      }

      if (e.key === "Escape") {
        e.preventDefault();
        hideArtistsPanel();
        return;
      }
    });

    // Wire up buttons
    artistsTabBtn.addEventListener("click", function () {
      if (artistsActive) {
        hideArtistsPanel();
      } else {
        showArtistsPanel();
      }
    });

    // Hide artists panel when source select changes
    sourceSelect.addEventListener("change", function () {
      if (artistsActive) hideArtistsPanel();
    });

    artistsRefreshBtn.addEventListener("click", loadArtistClusters);
    artistsShowDismissed.addEventListener("change", loadArtistClusters);
  }());

  // ── Playlists diff panel ─────────────────────────────────────────────────
  (function () {
    var playlistsPanel = document.getElementById("playlists-panel");
    var plSidebarList = document.getElementById("pl-sidebar-list");
    var plNewBtn = document.getElementById("pl-new-btn");
    var plNewInput = document.getElementById("pl-new-input");
    var plTracks = document.getElementById("pl-tracks");
    var tableContainer = document.getElementById("table-container");

    var playlistsActive = false;
    var prevSource = null;
    var diffData = null;       // { playlists, rb_only_playlists, rb_open }
    var playlistNames = [];    // sorted list for sidebar nav
    var focusedPl = -1;
    var selectedPl = null;

    // Expose rb_open state for CTA group
    window._playlistsRbOpen = function () { return diffData && diffData.rb_open; };
    window._playlistsRefresh = function () {
      diffData = null;
      if (playlistsActive) loadPlaylistDiff();
    };

    function showPlaylistsPanel() {
      playlistsActive = true;
      prevSource = currentSource;
      currentSource = "playlists";
      tableContainer.style.display = "none";
      playlistsPanel.classList.remove("hidden");
      document.querySelectorAll("#source-tabs .tab-btn").forEach(function (b) {
        b.classList.toggle("active", b.dataset.source === "playlists");
      });
      updateCtaGroup();
      if (!diffData) loadPlaylistDiff();
    }

    function hidePlaylistsPanel() {
      playlistsActive = false;
      currentSource = prevSource || "unsorted";
      playlistsPanel.classList.add("hidden");
      tableContainer.style.display = "";
      document.querySelectorAll("#source-tabs .tab-btn").forEach(function (b) {
        b.classList.toggle("active", b.dataset.source === currentSource);
      });
      updateCtaGroup();
    }

    async function loadPlaylistDiff() {
      plSidebarList.innerHTML = '<div class="pl-loading">Loading…</div>';
      plTracks.innerHTML = "";
      try {
        var resp = await fetch("/api/playlists/diff");
        if (!resp.ok) {
          var err = await resp.json();
          plSidebarList.innerHTML = '<div class="pl-empty">' + escHtml(err.error || "Error loading playlists") + "</div>";
          if (err.rb_open) showToast("Rekordbox is open — close it and REFRESH", "");
          return;
        }
        diffData = await resp.json();
        updateCtaGroup(); // re-render PUSH state after rb_open known
        renderSidebar();
        if (playlistNames.length > 0) selectPlaylist(playlistNames[0], 0);
      } catch (e) {
        plSidebarList.innerHTML = '<div class="pl-empty">Network error loading playlists</div>';
      }
    }

    function renderSidebar() {
      var rbOnlySet = new Set(diffData.rb_only_playlists || []);
      playlistNames = Object.keys(diffData.playlists || {}).sort(function (a, b) {
        return a.localeCompare(b);
      });

      plSidebarList.innerHTML = "";
      if (!playlistNames.length) {
        plSidebarList.innerHTML = '<div class="pl-empty">No playlists found</div>';
        return;
      }

      playlistNames.forEach(function (name, idx) {
        var tracks = diffData.playlists[name] || [];
        var hasRbOnly = tracks.some(function (t) { return t.state === "rb_only" || t.state === "rb_only_unknown"; });
        var hasDjlibOnly = tracks.some(function (t) { return t.state === "djlib_only"; });

        var dotClass;
        if (rbOnlySet.has(name) || hasRbOnly) dotClass = "pl-dot-rb-only";
        else if (hasDjlibOnly) dotClass = "pl-dot-djlib-only";
        else dotClass = "pl-dot-synced";

        var item = document.createElement("div");
        item.className = "pl-sidebar-item";
        item.dataset.idx = idx;
        item.innerHTML =
          '<span class="pl-sidebar-dot ' + dotClass + '"></span>' +
          '<span class="pl-sidebar-name">' + escHtml(name) + "</span>" +
          '<span class="pl-sidebar-count">' + tracks.length + "</span>";

        item.addEventListener("click", function () { selectPlaylist(name, idx); });
        plSidebarList.appendChild(item);
      });

      focusedPl = 0;
      highlightSidebarItem(0);
    }

    function highlightSidebarItem(idx) {
      plSidebarList.querySelectorAll(".pl-sidebar-item").forEach(function (el, i) {
        el.classList.toggle("active", i === idx);
      });
      var el = plSidebarList.querySelector('[data-idx="' + idx + '"]');
      if (el) el.scrollIntoView({ block: "nearest" });
    }

    function selectPlaylist(name, idx) {
      selectedPl = name;
      if (idx === undefined || idx === null) idx = playlistNames.indexOf(name);
      focusedPl = idx;
      highlightSidebarItem(idx);
      renderPlaylistTracks(name);
    }

    function syncBadgeHtml(state) {
      var defs = {
        both:            { text: "synced",  cls: "sync-badge-both" },
        rb_only:         { text: "RB only", cls: "sync-badge-rb-only" },
        rb_only_unknown: { text: "RB ?",    cls: "sync-badge-rb-unknown" },
        djlib_only:      { text: "djlib",   cls: "sync-badge-djlib-only" },
      };
      var d = defs[state] || { text: state, cls: "" };
      return '<span class="sync-badge ' + d.cls + '">' + d.text + "</span>";
    }

    function starsHtml(rating) {
      var n = parseInt(rating, 10);
      if (!n || n < 1) return "";
      var filled = Math.min(n, 5);
      return '<span class="pl-rating">' + "★".repeat(filled) + "☆".repeat(5 - filled) + "</span>";
    }

    function renderPlaylistTracks(name) {
      var tracks = (diffData && diffData.playlists[name]) || [];

      if (!tracks.length) {
        plTracks.innerHTML = '<div class="pl-empty">No tracks in this playlist</div>';
        return;
      }

      var html = '<div class="pl-content-header">' +
        '<span class="pl-content-title">' + escHtml(name) + "</span>" +
        '<span class="pl-content-count">' + tracks.length + " tracks</span>";
      if (diffData && diffData.rb_open) {
        html += '<span class="pl-rb-open-warn">⚠ Rekordbox is open — close it before pushing</span>';
      }
      html += "</div>";

      html += '<table class="pl-track-table">' +
        '<thead><tr>' +
        '<th class="pl-th pl-th-state"></th>' +
        '<th class="pl-th pl-th-artist">Artist</th>' +
        '<th class="pl-th pl-th-title">Title</th>' +
        '<th class="pl-th pl-th-version">Version</th>' +
        '<th class="pl-th pl-th-genre">Genre</th>' +
        '<th class="pl-th pl-th-year">Year</th>' +
        '<th class="pl-th pl-th-bpm">BPM</th>' +
        '<th class="pl-th pl-th-key">Key</th>' +
        '<th class="pl-th pl-th-time">Time</th>' +
        '<th class="pl-th pl-th-rating">Rating</th>' +
        '<th class="pl-th pl-th-action"></th>' +
        '</tr></thead>' +
        '<tbody>';

      tracks.forEach(function (t) {
        var adoptBtn = (t.state === "rb_only" && t.track_id)
          ? '<button class="pl-adopt-btn" data-track-id="' + escHtml(t.track_id) +
            '" data-playlist="' + escHtml(name) + '">Adopt</button>'
          : "";
        html +=
          '<tr class="pl-track-row">' +
          '<td class="pl-td-state">' + syncBadgeHtml(t.state) + "</td>" +
          '<td class="pl-td-artist">' + escHtml(t.artist || "") + "</td>" +
          '<td class="pl-td-title">' + escHtml(t.title || "") + "</td>" +
          '<td class="pl-td-version">' + escHtml(t.version_info || "") + "</td>" +
          '<td class="pl-td-genre">' + escHtml(t.genre || "") + "</td>" +
          '<td class="pl-td-year">' + escHtml(t.year || "") + "</td>" +
          '<td class="pl-td-bpm">' + escHtml(t.bpm || "") + "</td>" +
          '<td class="pl-td-key">' + escHtml(t.key_camelot || "") + "</td>" +
          '<td class="pl-td-time">' + escHtml(t.duration || "") + "</td>" +
          '<td class="pl-td-rating">' + starsHtml(t.rating) + "</td>" +
          '<td class="pl-td-action">' + adoptBtn + "</td>" +
          "</tr>";
      });
      html += "</tbody></table>";

      plTracks.innerHTML = html;

      // Wire adopt buttons
      plTracks.querySelectorAll(".pl-adopt-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          adoptFromRb(btn.dataset.trackId, btn.dataset.playlist, btn);
        });
      });
    }

    async function adoptFromRb(trackId, playlist, btn) {
      btn.disabled = true;
      btn.textContent = "…";
      try {
        var resp = await fetch("/api/track/" + encodeURIComponent(trackId) + "/adopt-from-rb", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ playlist: playlist }),
        });
        var data = await resp.json();
        if (data.ok) {
          btn.textContent = "✓";
          btn.classList.add("pl-adopt-btn--done");
          showToast("Adopted into \"" + playlist + "\"");
          // Update local state so re-render shows "synced"
          if (diffData && diffData.playlists[playlist]) {
            var track = diffData.playlists[playlist].find(function (t) { return t.track_id === trackId; });
            if (track) track.state = "both";
          }
          renderSidebar();
          selectPlaylist(playlist);
        } else {
          showToast("Adopt failed: " + (data.error || "unknown"));
          btn.disabled = false;
          btn.textContent = "Adopt";
        }
      } catch (e) {
        showToast("Network error");
        btn.disabled = false;
        btn.textContent = "Adopt";
      }
    }

    // Keyboard shortcuts
    document.addEventListener("keydown", function (e) {
      // Global P — jump to Playlists tab (when not in an input and panel is closed)
      if (!playlistsActive && !e.ctrlKey && !e.metaKey && !e.altKey &&
          (e.key === "p" || e.key === "P") &&
          e.target.tagName !== "INPUT" && e.target.tagName !== "TEXTAREA" && e.target.tagName !== "SELECT") {
        e.preventDefault();
        if (!playlistsActive) showPlaylistsPanel();
        return;
      }

      if (!playlistsActive) return;
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        var next = e.key === "ArrowDown"
          ? Math.min(focusedPl + 1, playlistNames.length - 1)
          : Math.max(focusedPl - 1, 0);
        selectPlaylist(playlistNames[next], next);
        return;
      }

      if (e.key === "Escape") {
        e.preventDefault();
        hidePlaylistsPanel();
        return;
      }
    });

    // Wire the tab button (click is delegated through sourceTabs, which returns early
    // for "playlists" — so we wire directly here)
    var plTabBtn = document.querySelector('#source-tabs .tab-btn[data-source="playlists"]');
    if (plTabBtn) {
      plTabBtn.addEventListener("click", function () {
        if (playlistsActive) hidePlaylistsPanel();
        else showPlaylistsPanel();
      });
    }

    // Hide when another source is selected via the hidden select
    sourceSelect.addEventListener("change", function () {
      if (playlistsActive) hidePlaylistsPanel();
    });

    // ── New playlist ────────────────────────────────────────────────────────
    function confirmNewPlaylist() {
      var name = plNewInput.value.replace(/\|/g, "").trim();
      if (!name) { cancelNewPlaylist(); return; }
      if (playlistNames.includes(name)) {
        // Just select the existing one
        var idx = playlistNames.indexOf(name);
        selectPlaylist(name, idx);
        cancelNewPlaylist();
        return;
      }
      // Add to local state as a djlib-only empty playlist
      if (!diffData) diffData = { playlists: {}, rb_only_playlists: [], rb_open: false };
      diffData.playlists[name] = [];
      playlistNames.push(name);
      playlistNames.sort(function (a, b) { return a.localeCompare(b, undefined, { sensitivity: "base" }); });
      renderSidebar();
      var newIdx = playlistNames.indexOf(name);
      selectPlaylist(name, newIdx);
      cancelNewPlaylist();
      showToast("Playlist \"" + name + "\" created — assign tracks via batch or row edit");
    }

    function cancelNewPlaylist() {
      plNewInput.classList.add("hidden");
      plNewInput.value = "";
      plNewBtn.classList.remove("hidden");
    }

    if (plNewBtn) {
      plNewBtn.addEventListener("click", function () {
        plNewBtn.classList.add("hidden");
        plNewInput.classList.remove("hidden");
        plNewInput.focus();
      });
    }

    if (plNewInput) {
      plNewInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); confirmNewPlaylist(); }
        if (e.key === "Escape") { e.preventDefault(); cancelNewPlaylist(); }
      });
      plNewInput.addEventListener("blur", function () {
        // Small delay so click on confirm doesn't race
        setTimeout(cancelNewPlaylist, 150);
      });
    }
  }());
})();
