/**
 * Nazzify - Editorial Web-style Spotify Companion
 * Frontend Controller & Polling Engine
 */

(function () {
  'use strict';

  // ── Authentication & API Token ───────────────────────────────────────────
  const urlParams = new URLSearchParams(window.location.search);
  const TOKEN = urlParams.get('token') || '';

  function api(endpoint, method = 'GET', data = null) {
    const sep = endpoint.includes('?') ? '&' : '?';
    const url = `${endpoint}${sep}token=${encodeURIComponent(TOKEN)}`;
    const options = {
      method,
      headers: {
        'X-Auth-Token': TOKEN,
      },
    };
    if (data && method !== 'GET') {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(data);
    }
    return fetch(url, options).then(r => {
      if (!r.ok) throw new Error(`HTTP error ${r.status}`);
      return r.json();
    });
  }

  // ── Application State ────────────────────────────────────────────────────
  let lastState = null;
  let activeTab = 'now-playing';
  let dismissedClipUrl = '';
  let pendingClipUrl = '';
  let localSelections = new Set();
  let collectionLoadedUrl = '';
  let logExpanded = false;
  let lastThumbKey = '';

  // ── DOM Element Cache ────────────────────────────────────────────────────
  const dom = {
    // Tabs
    tabNp: document.getElementById('tab-btn-np'),
    tabLink: document.getElementById('tab-btn-link'),
    screenNp: document.getElementById('screen-now-playing'),
    screenLink: document.getElementById('screen-link-playlist'),
    engineText: document.getElementById('engine-text'),

    // Now Playing Screen
    npSongTitle: document.getElementById('np-song-title'),
    npArtistTime: document.getElementById('np-artist-time'),
    npDownloadBtn: document.getElementById('np-download-btn'),
    npDownloadText: document.getElementById('np-download-text'),
    npQuickStatus: document.getElementById('np-quick-status'),
    queueCountBadge: document.getElementById('queue-count-badge'),
    queueList: document.getElementById('queue-list'),
    discContainer: document.getElementById('disc-container'),
    discCoverImg: document.getElementById('np-cover-img'),
    discPlaceholder: document.getElementById('np-placeholder-circle'),
    discGlow: document.getElementById('disc-glow'),
    playbackBadge: document.getElementById('np-playback-indicator'),
    playbackText: document.getElementById('np-playback-text'),

    // Link & Playlist Screen
    linkInput: document.getElementById('spotify-link-input'),
    linkPasteBtn: document.getElementById('link-paste-btn'),
    linkFetchBtn: document.getElementById('link-fetch-btn'),
    clipBanner: document.getElementById('clipboard-banner'),
    clipMsg: document.getElementById('clip-msg'),
    clipUseBtn: document.getElementById('clip-use-btn'),
    clipDismissBtn: document.getElementById('clip-dismiss-btn'),
    collLoading: document.getElementById('collection-loading'),
    collEmpty: document.getElementById('collection-empty'),
    collContent: document.getElementById('collection-content'),
    collCoverImg: document.getElementById('coll-cover-img'),
    collCoverPlaceholder: document.getElementById('coll-cover-placeholder'),
    collBadge: document.getElementById('coll-badge'),
    collTitle: document.getElementById('coll-title'),
    collOwner: document.getElementById('coll-owner'),
    btnSelectAll: document.getElementById('btn-select-all'),
    btnSelectNew: document.getElementById('btn-select-new'),
    btnSelectNone: document.getElementById('btn-select-none'),
    selectedCounter: document.getElementById('selected-counter'),
    trackSearchInput: document.getElementById('track-search-input'),
    trackTableBody: document.getElementById('track-table-body'),
    masterCheckbox: document.getElementById('master-checkbox'),
    optSubfolder: document.getElementById('opt-subfolder'),
    optZip: document.getElementById('opt-zip'),
    btnDownloadSelected: document.getElementById('btn-download-selected'),
    btnDownloadSelectedText: document.getElementById('btn-download-selected-text'),

    // Bottom Bar
    progressLineFill: document.getElementById('progress-line-fill'),
    saveFolderInput: document.getElementById('save-folder-input'),
    browseFolderBtn: document.getElementById('browse-folder-btn'),
    openFolderBtn: document.getElementById('open-folder-btn'),
    statusText: document.getElementById('status-text'),
    finishedGroup: document.getElementById('finished-actions-group'),
    finishedBadge: document.getElementById('finished-badge'),
    finishedOpenFolderBtn: document.getElementById('finished-open-folder-btn'),
    finishedShowZipBtn: document.getElementById('finished-show-zip-btn'),
    cancelDownloadBtn: document.getElementById('cancel-download-btn'),
    toggleDetailsBtn: document.getElementById('toggle-details-btn'),
    logConsole: document.getElementById('log-console-container'),
    logStream: document.getElementById('log-output-stream'),
    clearLogsBtn: document.getElementById('clear-logs-btn'),
  };

  // ── Navigation Tabs ──────────────────────────────────────────────────────
  function switchTab(tabId) {
    activeTab = tabId;
    if (tabId === 'now-playing') {
      dom.tabNp.classList.add('active');
      dom.tabLink.classList.remove('active');
      dom.screenNp.classList.add('active');
      dom.screenLink.classList.remove('active');
    } else {
      dom.tabLink.classList.add('active');
      dom.tabNp.classList.remove('active');
      dom.screenLink.classList.add('active');
      dom.screenNp.classList.remove('active');
      checkClipboardForSpotifyLink();
    }
  }

  dom.tabNp.addEventListener('click', () => switchTab('now-playing'));
  dom.tabLink.addEventListener('click', () => switchTab('link-playlist'));

  // ── Polling Engine (every 300 ms) ────────────────────────────────────────
  function pollBackend() {
    api('/api/poll')
      .then(state => {
        updateUI(state);
      })
      .catch(err => {
        dom.statusText.textContent = 'Connecting to Nazzify backend…';
      })
      .finally(() => {
        setTimeout(pollBackend, 300);
      });
  }

  // ── UI Updates from State ────────────────────────────────────────────────
  function updateUI(state) {
    lastState = state;

    // 1. Engine Information
    if (state.engine) {
      const ffmpeg = state.engine.ffmpeg ? ' + FFmpeg' : '';
      dom.engineText.textContent = `${state.engine.downloader}${ffmpeg}`;
    }

    // 2. Folder Configuration
    if (state.config && state.config.download_folder) {
      if (document.activeElement !== dom.saveFolderInput) {
        dom.saveFolderInput.value = state.config.download_folder;
      }
    }

    // 3. Now Playing Screen
    renderNowPlaying(state.now_playing, state.queue);

    // 4. Link & Playlist Screen
    renderLinkState(state.link);

    // 5. Bottom Bar
    renderBottomBar(state.status_bar);

    // 6. Logs Console
    if (state.logs && logExpanded) {
      renderLogs(state.logs);
    }
  }

  // ── Now Playing Renderer ─────────────────────────────────────────────────
  function renderNowPlaying(np, queue) {
    if (!np) return;

    if (np.ok && np.title) {
      dom.npSongTitle.textContent = np.title;

      const durStr = np.duration
        ? ` • ${Math.floor(np.duration / 60)}:${String(np.duration % 60).padStart(2, '0')}`
        : '';
      dom.npArtistTime.textContent = `${np.artist || 'Unknown'}${durStr}  [${np.status || 'Playing'}]`;

      const isPlaying = (np.status || '').toLowerCase() === 'playing';

      // Update Cover Circle & Glow
      if (np.has_thumbnail) {
        const thumbKey = `${np.title}-${np.artist}`;
        if (lastThumbKey !== thumbKey) {
          lastThumbKey = thumbKey;
          dom.discCoverImg.src = `/api/thumbnail?token=${encodeURIComponent(TOKEN)}&t=${Date.now()}`;
        }
        dom.discCoverImg.style.display = 'block';
        dom.discPlaceholder.style.display = 'none';
      } else {
        dom.discCoverImg.style.display = 'none';
        dom.discPlaceholder.style.display = 'flex';
      }

      // Disc Spin: only while playing
      if (isPlaying) {
        dom.discContainer.classList.add('spinning');
        dom.discContainer.classList.remove('stopped');
        dom.playbackBadge.classList.add('playing');
        dom.playbackText.textContent = 'Playing';
      } else {
        dom.discContainer.classList.remove('spinning');
        dom.discContainer.classList.add('stopped');
        dom.playbackBadge.classList.remove('playing');
        dom.playbackText.textContent = np.status || 'Paused';
      }

      dom.npDownloadBtn.disabled = false;
    } else {
      // Nothing playing
      dom.npSongTitle.textContent = 'Play a song in Spotify';
      dom.npArtistTime.textContent = np.message || 'Start playing music in Spotify to begin.';
      dom.discCoverImg.style.display = 'none';
      dom.discPlaceholder.style.display = 'flex';
      dom.discContainer.classList.remove('spinning');
      dom.discContainer.classList.add('stopped');
      dom.playbackBadge.classList.remove('playing');
      dom.playbackText.textContent = 'Ready';
      dom.npDownloadBtn.disabled = false;
    }

    // Queue Rendering
    renderQueue(queue);
  }

  function renderQueue(queue) {
    if (!queue) return;
    const items = [];
    if (queue.active) {
      items.push({ active: true, ...queue.active });
    }
    if (queue.waiting) {
      queue.waiting.forEach(w => items.push({ active: false, ...w }));
    }

    dom.queueCountBadge.textContent = items.length;

    if (items.length === 0) {
      dom.queueList.innerHTML = '<div class="queue-empty">Queue is clear</div>';
    } else {
      dom.queueList.innerHTML = items
        .map(
          item => `
        <div class="queue-item">
          <span class="queue-item-name">${item.title}</span>
          <span class="queue-item-badge">${item.active ? 'Downloading…' : 'Queued'}</span>
        </div>
      `
        )
        .join('');
    }
  }

  // ── Download Now Playing Button ──────────────────────────────────────────
  dom.npDownloadBtn.addEventListener('click', () => {
    dom.npDownloadBtn.disabled = true;
    dom.npQuickStatus.textContent = 'Queuing…';

    api('/api/download_now_playing', 'POST')
      .then(res => {
        dom.npQuickStatus.textContent = res.message || 'Queued!';
        setTimeout(() => {
          dom.npQuickStatus.textContent = '';
        }, 3000);
      })
      .catch(err => {
        dom.npQuickStatus.textContent = 'Error';
      })
      .finally(() => {
        dom.npDownloadBtn.disabled = false;
      });
  });

  // ── Link & Playlist Screen ───────────────────────────────────────────────
  dom.linkPasteBtn.addEventListener('click', async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        dom.linkInput.value = text.trim();
        dom.linkInput.focus();
      }
    } catch {
      dom.linkInput.focus();
    }
  });

  dom.linkInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      fetchCollection();
    }
  });

  dom.linkFetchBtn.addEventListener('click', fetchCollection);

  function fetchCollection() {
    const url = dom.linkInput.value.trim();
    if (!url) {
      dom.statusText.textContent = 'Please paste a Spotify link first.';
      dom.linkInput.focus();
      return;
    }

    dom.linkFetchBtn.disabled = true;
    dom.collEmpty.style.display = 'none';
    dom.collContent.style.display = 'none';
    dom.collLoading.style.display = 'flex';
    dom.statusText.textContent = 'Fetching metadata from Spotify…';

    api('/api/fetch_link', 'POST', { url })
      .then(res => {
        if (!res.ok) {
          dom.statusText.textContent = res.error || 'Failed to fetch collection.';
          dom.collLoading.style.display = 'none';
          dom.collEmpty.style.display = 'flex';
        }
      })
      .catch(err => {
        dom.statusText.textContent = 'Error communicating with backend.';
        dom.collLoading.style.display = 'none';
        dom.collEmpty.style.display = 'flex';
      })
      .finally(() => {
        dom.linkFetchBtn.disabled = false;
      });
  }

  // Clipboard discovery helper
  async function checkClipboardForSpotifyLink() {
    try {
      const clip = (await navigator.clipboard.readText()).trim();
      const isSpotify = /^https?:\/\/open\.spotify\.com\/(track|album|playlist)/i.test(clip);
      if (isSpotify && clip !== dismissedClipUrl && clip !== dom.linkInput.value.trim()) {
        pendingClipUrl = clip;
        const match = clip.match(/(track|album|playlist)/i);
        const kind = match ? match[1].toUpperCase() : 'LINK';
        dom.clipMsg.textContent = `Spotify ${kind} detected on clipboard`;
        dom.clipBanner.style.display = 'flex';
      }
    } catch {
      // Clipboard read permissions may not be granted
    }
  }

  dom.clipUseBtn.addEventListener('click', () => {
    if (pendingClipUrl) {
      dom.linkInput.value = pendingClipUrl;
      dom.clipBanner.style.display = 'none';
      fetchCollection();
    }
  });

  dom.clipDismissBtn.addEventListener('click', () => {
    dismissedClipUrl = pendingClipUrl;
    dom.clipBanner.style.display = 'none';
  });

  // Render collection content
  function renderLinkState(link) {
    if (!link) return;

    if (link.collection && link.tracks && link.tracks.length > 0) {
      dom.collLoading.style.display = 'none';
      dom.collEmpty.style.display = 'none';
      dom.collContent.style.display = 'flex';

      const coll = link.collection;
      const count = coll.total || link.tracks.length;
      const kind = (coll.kind || 'collection').toUpperCase();

      dom.collBadge.textContent = `${kind} • ${count} TRACKS`;
      dom.collTitle.textContent = coll.name || 'Spotify Collection';
      dom.collOwner.textContent = coll.owner ? `By ${coll.owner}` : '';

      if (coll.cover_url) {
        dom.collCoverImg.src = coll.cover_url;
        dom.collCoverImg.style.display = 'block';
        dom.collCoverPlaceholder.style.display = 'none';
      } else {
        dom.collCoverImg.style.display = 'none';
        dom.collCoverPlaceholder.style.display = 'flex';
      }

      // Initialize local selection set if fresh collection
      const collIdentifier = `${coll.name}-${count}`;
      if (collectionLoadedUrl !== collIdentifier) {
        collectionLoadedUrl = collIdentifier;
        localSelections.clear();
        link.tracks.forEach(t => {
          if (t.checked) localSelections.add(t.tid);
        });
      }

      renderTrackTable(link.tracks, link.busy);
      updateSelectionSummary(link.tracks, link.busy);
    } else if (link.busy) {
      dom.collLoading.style.display = 'flex';
      dom.collEmpty.style.display = 'none';
      dom.collContent.style.display = 'none';
    }
  }

  function renderTrackTable(tracks, isBusy) {
    const query = dom.trackSearchInput.value.trim().toLowerCase();

    // Check if table needs rebuild or partial update
    const filtered = tracks.filter(t => {
      if (!query) return true;
      return (
        t.title.toLowerCase().includes(query) ||
        t.artists.toLowerCase().includes(query)
      );
    });

    dom.trackTableBody.innerHTML = filtered
      .map(t => {
        const isChecked = localSelections.has(t.tid);
        const timeStr = t.duration
          ? `${Math.floor(t.duration / 60)}:${String(t.duration % 60).padStart(2, '0')}`
          : '--:--';

        // Map status clean without "review"
        const cleanStatus = mapCleanStatus(t.status);
        const pillClass = cleanStatus.toLowerCase().replace(' ', '-');

        return `
        <tr class="track-row ${isChecked ? 'checked' : ''}" data-tid="${t.tid}">
          <td class="td-check">
            <input type="checkbox" class="track-check" data-tid="${t.tid}" ${isChecked ? 'checked' : ''}>
          </td>
          <td class="td-num">${t.num}</td>
          <td>
            <div class="track-title-cell">
              <span class="track-name" title="${t.title}">${t.title}</span>
              <span class="track-artists" title="${t.artists}">${t.artists}</span>
            </div>
          </td>
          <td class="td-time">${timeStr}</td>
          <td class="td-status">
            <span class="status-pill ${pillClass}">${cleanStatus}</span>
          </td>
        </tr>
      `;
      })
      .join('');

    // Attach row click listeners for easy toggling
    dom.trackTableBody.querySelectorAll('.track-row').forEach(row => {
      row.addEventListener('click', e => {
        if (e.target.tagName.toLowerCase() === 'input') return;
        const tid = row.dataset.tid;
        if (tid) toggleTrackSelection(tid);
      });
    });

    dom.trackTableBody.querySelectorAll('.track-check').forEach(chk => {
      chk.addEventListener('change', e => {
        const tid = chk.dataset.tid;
        if (tid) {
          if (chk.checked) localSelections.add(tid);
          else localSelections.delete(tid);
          updateSelectionSummary(tracks, isBusy);
        }
      });
    });
  }

  function toggleTrackSelection(tid) {
    if (localSelections.has(tid)) {
      localSelections.delete(tid);
    } else {
      localSelections.add(tid);
    }
    if (lastState && lastState.link && lastState.link.tracks) {
      renderTrackTable(lastState.link.tracks, lastState.link.busy);
      updateSelectionSummary(lastState.link.tracks, lastState.link.busy);
    }
  }

  function mapCleanStatus(status) {
    if (!status) return 'Waiting';
    const s = status.toLowerCase();
    if (s.includes('review') || s.includes('⚠')) return 'Done';
    if (s.includes('done') || s.includes('✓')) return 'Done';
    if (s.includes('downloading')) return 'Downloading';
    if (s.includes('not found') || s.includes('failed') || s.includes('✗')) return 'Not found';
    if (s.includes('have')) return 'Have';
    return status;
  }

  function updateSelectionSummary(tracks, isBusy) {
    const total = tracks.length;
    const selectedCount = localSelections.size;

    dom.selectedCounter.textContent = `${selectedCount} of ${total} selected`;
    dom.btnDownloadSelectedText.textContent = `Download Selected (${selectedCount})`;

    dom.btnDownloadSelected.disabled = isBusy || selectedCount === 0;
    dom.masterCheckbox.checked = total > 0 && selectedCount === total;
  }

  // Toolbar Actions
  dom.btnSelectAll.addEventListener('click', () => {
    if (lastState && lastState.link && lastState.link.tracks) {
      lastState.link.tracks.forEach(t => localSelections.add(t.tid));
      renderTrackTable(lastState.link.tracks, lastState.link.busy);
      updateSelectionSummary(lastState.link.tracks, lastState.link.busy);
    }
  });

  dom.btnSelectNew.addEventListener('click', () => {
    if (lastState && lastState.link && lastState.link.tracks) {
      localSelections.clear();
      lastState.link.tracks.forEach(t => {
        if ((t.status || '').toLowerCase() !== 'have') {
          localSelections.add(t.tid);
        }
      });
      renderTrackTable(lastState.link.tracks, lastState.link.busy);
      updateSelectionSummary(lastState.link.tracks, lastState.link.busy);
    }
  });

  dom.btnSelectNone.addEventListener('click', () => {
    localSelections.clear();
    if (lastState && lastState.link && lastState.link.tracks) {
      renderTrackTable(lastState.link.tracks, lastState.link.busy);
      updateSelectionSummary(lastState.link.tracks, lastState.link.busy);
    }
  });

  dom.masterCheckbox.addEventListener('change', () => {
    const target = dom.masterCheckbox.checked;
    if (lastState && lastState.link && lastState.link.tracks) {
      if (target) {
        lastState.link.tracks.forEach(t => localSelections.add(t.tid));
      } else {
        localSelections.clear();
      }
      renderTrackTable(lastState.link.tracks, lastState.link.busy);
      updateSelectionSummary(lastState.link.tracks, lastState.link.busy);
    }
  });

  dom.trackSearchInput.addEventListener('input', () => {
    if (lastState && lastState.link && lastState.link.tracks) {
      renderTrackTable(lastState.link.tracks, lastState.link.busy);
    }
  });

  // Sticky Download Selected Button
  dom.btnDownloadSelected.addEventListener('click', () => {
    const selectedIds = Array.from(localSelections);
    if (selectedIds.length === 0) return;

    dom.btnDownloadSelected.disabled = true;
    dom.statusText.textContent = `Starting download for ${selectedIds.length} tracks…`;

    const options = {
      subfolder: dom.optSubfolder.checked,
      create_zip: dom.optZip.checked,
      url: dom.linkInput.value.trim(),
    };

    api('/api/download_selected', 'POST', {
      selected_ids: selectedIds,
      options,
    })
      .then(res => {
        if (!res.ok) {
          dom.statusText.textContent = res.error || 'Failed to start download.';
        }
      })
      .catch(err => {
        dom.statusText.textContent = 'Communication error.';
      });
  });

  // ── Bottom Bar & Action Handlers ─────────────────────────────────────────
  function renderBottomBar(sb) {
    if (!sb) return;

    // Status Text
    if (sb.status) {
      dom.statusText.textContent = sb.status;
    }

    // Thin Progress Line
    const pct = Math.max(0, Math.min(100, sb.progress || 0));
    dom.progressLineFill.style.width = `${pct}%`;

    // Cancel Button
    dom.cancelDownloadBtn.disabled = !sb.can_cancel;

    // Finished Actions: "Done: N songs" with [Open folder] & [Show ZIP]
    if (sb.done_summary || sb.done_folder) {
      dom.finishedGroup.style.display = 'flex';
      dom.finishedBadge.textContent = sb.done_summary || 'Done';

      if (sb.done_zip) {
        dom.finishedShowZipBtn.style.display = 'inline-flex';
      } else {
        dom.finishedShowZipBtn.style.display = 'none';
      }
    } else {
      dom.finishedGroup.style.display = 'none';
    }
  }

  // Cancel Button
  dom.cancelDownloadBtn.addEventListener('click', () => {
    dom.cancelDownloadBtn.disabled = true;
    dom.statusText.textContent = 'Cancelling…';
    api('/api/cancel', 'POST');
  });

  // Details / Log Toggle
  dom.toggleDetailsBtn.addEventListener('click', () => {
    logExpanded = !logExpanded;
    if (logExpanded) {
      dom.logConsole.style.display = 'flex';
      dom.toggleDetailsBtn.textContent = 'Hide details';
      if (lastState && lastState.logs) {
        renderLogs(lastState.logs);
      }
    } else {
      dom.logConsole.style.display = 'none';
      dom.toggleDetailsBtn.textContent = 'Show details';
    }
  });

  function renderLogs(logs) {
    const text = logs.join('\n');
    dom.logStream.textContent = text;
    dom.logStream.scrollTop = dom.logStream.scrollHeight;
  }

  dom.clearLogsBtn.addEventListener('click', () => {
    dom.logStream.textContent = '';
  });

  // Folder Operations
  dom.browseFolderBtn.addEventListener('click', () => {
    api('/api/browse_folder', 'POST').then(res => {
      if (res.ok && res.folder) {
        dom.saveFolderInput.value = res.folder;
      }
    });
  });

  dom.openFolderBtn.addEventListener('click', () => {
    const f = dom.saveFolderInput.value.trim();
    api('/api/open_folder', 'POST', { folder: f });
  });

  dom.finishedOpenFolderBtn.addEventListener('click', () => {
    const f = (lastState && lastState.status_bar && lastState.status_bar.done_folder) || dom.saveFolderInput.value.trim();
    api('/api/open_folder', 'POST', { folder: f });
  });

  dom.finishedShowZipBtn.addEventListener('click', () => {
    const z = lastState && lastState.status_bar && lastState.status_bar.done_zip;
    if (z) {
      api('/api/show_zip', 'POST', { zip_path: z });
    }
  });

  // ── Window Close Cleanup Beacon ──────────────────────────────────────────
  window.addEventListener('beforeunload', () => {
    navigator.sendBeacon(`/api/exit?token=${encodeURIComponent(TOKEN)}`);
  });

  // ── Launch Polling Engine ────────────────────────────────────────────────
  pollBackend();

})();
