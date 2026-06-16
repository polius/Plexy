'use strict';

/* ============================================================
   Plexy — download-manager frontend
   ============================================================ */

const $ = (id) => document.getElementById(id);
const LAST_PATH_KEY = 'plexy:lastPath';

/* ---------- Inline SVG icons ---------- */
const S = 'fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"';
const ICONS = {
    folder:  `<svg class="icon" viewBox="0 0 24 24" ${S}><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>`,
    up:      `<svg class="icon" viewBox="0 0 24 24" ${S}><path d="M9 14 4 9l5-5"/><path d="M4 9h11a4 4 0 0 1 4 4v7"/></svg>`,
    file:    `<svg class="icon" viewBox="0 0 24 24" ${S}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>`,
    chev:    `<svg class="chev" viewBox="0 0 24 24" ${S}><polyline points="9 18 15 12 9 6"/></svg>`,
    seed:    `<svg class="icon" viewBox="0 0 24 24" ${S}><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>`,
    leech:   `<svg class="icon" viewBox="0 0 24 24" ${S}><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>`,
    check:   `<svg class="icon" viewBox="0 0 24 24" ${S}><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
    x:       `<svg class="icon" viewBox="0 0 24 24" ${S}><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
    closeX:  `<svg class="icon" viewBox="0 0 24 24" ${S}><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
    trash:   `<svg class="icon" viewBox="0 0 24 24" ${S}><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>`,
    search:  `<svg class="icon" viewBox="0 0 24 24" ${S}><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
    spin:    `<svg class="icon spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56" opacity="0.9"/></svg>`,
    warn:    `<svg class="icon" viewBox="0 0 24 24" ${S}><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
    inbox:   `<svg class="icon" viewBox="0 0 24 24" ${S}><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>`,
};

/* ---------- Add-flow state ---------- */
let magnetLink = '';
let selectedFile = null;     // File (.torrent)
let selectedResult = null;   // nyaa result
let torrentFiles = [];
let selectedIndices = [];
let allSelected = true;
let multiFile = false;
let currentPath = '/';

/* ---------- App state ---------- */
let plexHealthy = false;
let pollTimer = null;
const rowEls = new Map();    // id -> DOM row

/* ============================================================
   Helpers
   ============================================================ */
function formatSize(bytes) {
    if (!bytes || bytes <= 0) return '0 B';
    const k = 1024, u = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.min(u.length - 1, Math.floor(Math.log(bytes) / Math.log(k)));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + u[i];
}
function formatRate(kbps) {
    if (!kbps || kbps < 1) return '0 KB/s';
    if (kbps >= 1024) return (kbps / 1024).toFixed(1) + ' MB/s';
    return Math.round(kbps) + ' KB/s';
}
function formatMB(mb) {
    if (!mb || mb < 0) return '0 MB';
    if (mb >= 1024) return (mb / 1024).toFixed(2) + ' GB';
    return Math.round(mb) + ' MB';
}
function formatTime(s) {
    if (s == null || s < 0 || !isFinite(s)) return '—';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${sec}s`;
    return `${sec}s`;
}
function displayPath(p) {
    if (!p) return '/';
    if (p.startsWith('/downloads')) return p.slice(10) || '/';
    return p;
}

function setLoading(btn, text) {
    if (!btn) return;
    btn.dataset.html = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `${ICONS.spin}<span>${text || 'Working…'}</span>`;
}
function clearLoading(btn) {
    if (!btn || !btn.dataset.html) return;
    btn.disabled = false;
    btn.innerHTML = btn.dataset.html;
    delete btn.dataset.html;
}

/* ---------- Toasts ---------- */
function toast(message, type = 'ok', autoDismiss = true) {
    const el = document.createElement('div');
    el.className = `toast ${type === 'error' || type === 'err' ? 'err' : 'ok'}`;
    const ico = (type === 'error' || type === 'err') ? ICONS.x : ICONS.check;
    el.innerHTML = `<span class="ic">${ico}</span><span class="msg"></span><span class="x">${ICONS.closeX}</span>`;
    el.querySelector('.msg').textContent = message;
    el.addEventListener('click', () => dismissToast(el));
    $('toasts').appendChild(el);
    if (autoDismiss) setTimeout(() => dismissToast(el), 5000);
}
function dismissToast(el) {
    if (!el || el.classList.contains('out')) return;
    el.classList.add('out');
    setTimeout(() => el.remove(), 250);
}

/* ============================================================
   Plex
   ============================================================ */
function setPlexChip(state) {
    $('plexChip').className = `plex-chip is-${state}`;
    $('plexChipLabel').textContent =
        state === 'online' ? 'Plex connected' : state === 'offline' ? 'Plex offline' : 'Checking Plex…';
}

async function checkPlexHealth() {
    try {
        const res = await fetch('/api/plex/health');
        const data = await res.json();
        if (!data.connected) throw new Error();
        plexHealthy = true;
        setPlexChip('online');
        renderPlexOnline();
        loadPlexLibraries();
    } catch {
        plexHealthy = false;
        setPlexChip('offline');
        renderPlexOffline();
    }
}

function renderPlexOnline() {
    $('refreshBtn').disabled = !$('plexLibrary').value;
}
function renderPlexOffline() {
    $('plexBody').innerHTML =
        `<div class="note warn">${ICONS.warn}<div><strong>Plex isn't connected.</strong> Set the <code>PLEX_URL</code> and <code>PLEX_TOKEN</code> environment variables to enable automatic library refresh. Downloads still work without it.</div></div>`;
}

async function loadPlexLibraries() {
    try {
        const res = await fetch('/api/plex/libraries');
        const data = await res.json();
        const sel = $('plexLibrary');
        sel.innerHTML = '<option value="">Select a library…</option>';
        (data.libraries || []).forEach((lib) => {
            const o = document.createElement('option');
            o.value = lib.title; o.textContent = lib.title;
            sel.appendChild(o);
        });
    } catch {
        $('plexLibrary').innerHTML = '<option value="">Could not load libraries</option>';
    }
}

async function refreshPlex() {
    const library = $('plexLibrary').value;
    if (!library) { toast('Select a library first', 'error'); return; }
    const btn = $('refreshBtn');
    setLoading(btn, 'Refreshing…');
    try {
        const res = await fetch('/api/plex/refresh', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ library_name: library }),
        });
        if (!res.ok) throw new Error('Failed to refresh library');
        toast(`Scanning "${library}" — new files will appear in Plex shortly`);
    } catch (err) {
        toast('Error refreshing library: ' + err.message, 'error');
    } finally {
        clearLoading(btn);
    }
}

/* ============================================================
   Add flow — view switching
   ============================================================ */
const VIEW_META = {
    source: ['Add a download', 'Choose a torrent source to get started'],
    files:  ['Select files', 'Choose which files to download'],
    dest:   ['Choose destination', 'Where should the files be saved?'],
};

function setAddView(view) {
    ['source', 'files', 'dest'].forEach((v) => { $(`view-${v}`).hidden = (v !== view); });
    $('addTitle').textContent = VIEW_META[view][0];
    $('addSub').textContent = VIEW_META[view][1];

    const crumbs = $('addCrumbs');
    if (view === 'source') { crumbs.hidden = true; crumbs.innerHTML = ''; return; }
    crumbs.hidden = false;
    const steps = multiFile ? ['source', 'files', 'dest'] : ['source', 'dest'];
    const labels = { source: 'Source', files: 'Files', dest: 'Destination' };
    crumbs.innerHTML = steps.map((s, i) =>
        `${i ? '<span class="crumb-sep">›</span>' : ''}<span class="crumb-step ${s === view ? 'active' : ''}">${labels[s]}</span>`
    ).join('');
}

/* ---------- Tabs ---------- */
function switchTab(name) {
    document.querySelectorAll('.tab').forEach((t) => {
        const on = t.dataset.tab === name;
        t.classList.toggle('active', on);
        t.setAttribute('aria-selected', on);
    });
    document.querySelectorAll('.tab-panel').forEach((p) => { p.hidden = true; });
    $(`panel-${name}`).hidden = false;

    magnetLink = ''; selectedResult = null; selectedFile = null;
    torrentFiles = []; selectedIndices = [];
    resetFilePicker();

    setTimeout(() => {
        if (name === 'magnet') $('magnetInput').focus();
        else if (name === 'nyaa') $('nyaaInput').focus();
    }, 0);
}

function resetFilePicker() {
    const pill = $('filePill');
    pill.className = 'file-pill';
    pill.innerHTML = '';
    $('fileContinue').hidden = true;
    $('fileInput').value = '';
}

/* ---------- Source: file ---------- */
function onFileChosen(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.torrent')) {
        toast('Please choose a valid .torrent file', 'error'); return;
    }
    selectedFile = file; magnetLink = ''; torrentFiles = []; selectedIndices = [];
    const pill = $('filePill');
    pill.className = 'file-pill show';
    pill.innerHTML = `${ICONS.check}<span class="fn"></span><span class="meta"></span>`;
    pill.querySelector('.fn').textContent = file.name;
    pill.querySelector('.meta').textContent = formatSize(file.size);
    $('fileContinue').hidden = false;
}

/* ---------- Source: nyaa ---------- */
async function searchNyaa() {
    const q = $('nyaaInput').value.trim();
    if (!q) { toast('Enter a search query', 'error'); return; }
    const box = $('nyaaResults');
    const btn = $('nyaaSearchBtn');
    setLoading(btn, 'Searching…');
    box.innerHTML = `<div class="loading">${ICONS.spin}<span>Searching nyaa.si…</span></div>`;
    try {
        const res = await fetch(`/api/search/nyaa?query=${encodeURIComponent(q)}`);
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = await res.json();
        const results = (data.results || []).sort((a, b) => b.seeders - a.seeders);
        if (!results.length) {
            box.innerHTML = `<div class="empty">${ICONS.search}<div class="empty-title">No results</div><div class="empty-sub">Try different search terms</div></div>`;
            return;
        }
        const wrap = document.createElement('div');
        wrap.className = 'results';
        results.forEach((r) => {
            const item = document.createElement('div');
            item.className = 'result';
            item.tabIndex = 0; item.setAttribute('role', 'button');
            item.innerHTML = `
                <div class="result-name"></div>
                <div class="result-meta">
                    <span class="rdate"></span><span class="sep"></span>
                    <span class="rsize"></span><span class="sep"></span>
                    <span class="badge seed">${ICONS.seed}<span></span></span>
                    <span class="badge leech">${ICONS.leech}<span></span></span>
                    <span class="sep"></span><span class="rcat"></span>
                </div>`;
            item.querySelector('.result-name').textContent = r.name;
            item.querySelector('.rdate').textContent = r.date;
            item.querySelector('.rsize').textContent = r.size;
            item.querySelector('.badge.seed span').textContent = r.seeders;
            item.querySelector('.badge.leech span').textContent = r.leechers;
            item.querySelector('.rcat').textContent = r.category;
            const pick = () => selectResult(item, r);
            item.addEventListener('click', pick);
            item.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
            wrap.appendChild(item);
        });
        box.innerHTML = '';
        box.appendChild(wrap);
        const cont = document.createElement('button');
        cont.className = 'btn btn-primary btn-block';
        cont.style.marginTop = '16px';
        cont.id = 'nyaaContinue';
        cont.disabled = true;
        cont.textContent = 'Continue';
        cont.addEventListener('click', () => fetchTorrentInfo(cont));
        box.appendChild(cont);
    } catch (err) {
        box.innerHTML = `<div class="empty">${ICONS.warn}<div class="empty-title">Search failed</div><div class="empty-sub"></div></div>`;
        box.querySelector('.empty-sub').textContent = err.message;
    } finally {
        clearLoading(btn);
    }
}

function selectResult(node, result) {
    document.querySelectorAll('.result').forEach((el) => el.classList.remove('selected'));
    node.classList.add('selected');
    selectedResult = result; magnetLink = result.magnet;
    selectedFile = null; torrentFiles = []; selectedIndices = [];
    const cont = $('nyaaContinue');
    if (cont) cont.disabled = false;
}

/* ---------- Source -> metadata ---------- */
function continueFromMagnet() {
    if (!magnetLink) {
        const v = $('magnetInput').value.trim();
        if (v.startsWith('magnet:')) magnetLink = v;
    }
    if (!magnetLink) { toast('Enter a valid magnet link', 'error'); return; }
    fetchTorrentInfo($('magnetContinue'));
}

async function fetchTorrentInfo(btn) {
    try {
        let res;
        if (selectedFile) {
            const fd = new FormData();
            fd.append('file', selectedFile);
            setLoading(btn, 'Reading file…');
            res = await fetch('/api/torrent/info/file', { method: 'POST', body: fd });
        } else if (magnetLink) {
            setLoading(btn, 'Fetching metadata…');
            res = await fetch('/api/torrent/info', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ magnet_link: magnetLink }),
            });
        } else { toast('Choose a source first', 'error'); return; }

        if (!res.ok) {
            let msg = 'Could not load torrent information';
            try { const e = await res.json(); if (e.detail) msg = e.detail; } catch {}
            if (res.status === 408) msg = 'Timed out fetching metadata — the magnet may have no active seeders.';
            toast(msg, 'error');
            return;
        }
        const data = await res.json();
        torrentFiles = data.files || [];
        multiFile = torrentFiles.length > 1;

        if (multiFile) {
            showFiles(data);
        } else {
            selectedIndices = [];          // single file -> download everything
            goToDest();
        }
    } catch (err) {
        toast('Error loading torrent: ' + err.message, 'error');
    } finally {
        clearLoading(btn);
    }
}

/* ---------- File selection ---------- */
function showFiles(data) {
    selectedIndices = torrentFiles.map((_, i) => i);
    allSelected = true;
    const sum = $('fsSummary');
    sum.innerHTML = `<div class="ic">${ICONS.file}</div><div class="t"><div class="nm"></div><div class="mt"></div></div>`;
    sum.querySelector('.nm').textContent = data.name;
    sum.querySelector('.mt').textContent = `${data.num_files} files · ${formatSize(data.total_size)}`;
    $('fsTotalCount').textContent = torrentFiles.length;
    renderFileList();
    updateFsStats();
    setAddView('files');
}

function renderFileList() {
    const list = $('fsList'), viewport = $('fsViewport');
    const H = 44, BUF = 8;
    viewport.style.height = (torrentFiles.length * H) + 'px';
    viewport.innerHTML = '';
    function draw() {
        const top = list.scrollTop, h = list.clientHeight;
        const start = Math.max(0, Math.floor(top / H) - BUF);
        const end = Math.min(torrentFiles.length, Math.ceil((top + h) / H) + BUF);
        viewport.querySelectorAll('.fs-item').forEach((n) => n.remove());
        for (let i = start; i < end; i++) {
            const f = torrentFiles[i];
            const row = document.createElement('div');
            row.className = 'fs-item';
            row.style.cssText = `position:absolute;left:0;right:0;height:${H}px;top:${i * H}px`;
            row.innerHTML = `<input type="checkbox" class="check"><div class="info"><span class="fn"></span><span class="fz"></span></div>`;
            const cb = row.querySelector('.check');
            cb.checked = selectedIndices.includes(i);
            row.querySelector('.fn').textContent = f.name;
            row.querySelector('.fz').textContent = formatSize(f.size);
            row.addEventListener('click', (e) => {
                if (e.target !== cb) cb.checked = !cb.checked;
                toggleFile(i, cb.checked);
            });
            viewport.appendChild(row);
        }
    }
    draw();
    let t;
    list.onscroll = () => { clearTimeout(t); t = setTimeout(draw, 8); };
}

function toggleFile(i, checked) {
    if (checked) { if (!selectedIndices.includes(i)) selectedIndices.push(i); }
    else selectedIndices = selectedIndices.filter((x) => x !== i);
    updateFsStats();
}

function updateFsStats() {
    const sel = selectedIndices.length;
    $('fsSelCount').textContent = sel;
    const size = torrentFiles.reduce((s, f, i) => selectedIndices.includes(i) ? s + f.size : s, 0);
    $('fsSelSize').textContent = formatSize(size);
    allSelected = sel === torrentFiles.length && sel > 0;
    $('fsToggleAll').textContent = allSelected ? 'Deselect all' : 'Select all';
    $('filesContinue').disabled = sel === 0;
}

function toggleAllFiles() {
    selectedIndices = allSelected ? [] : torrentFiles.map((_, i) => i);
    const on = selectedIndices.length > 0;
    $('fsList').querySelectorAll('.check').forEach((cb) => { cb.checked = on; });
    updateFsStats();
}

/* ---------- Destination ---------- */
function goToDest() {
    setAddView('dest');
    const last = localStorage.getItem(LAST_PATH_KEY) || '/';
    loadFolders(last);
}

async function loadFolders(path) {
    const list = $('folderList');
    list.style.opacity = '0.5';
    try {
        const url = path ? `/api/folders?path=${encodeURIComponent(path)}` : '/api/folders';
        const res = await fetch(url);
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = await res.json();

        currentPath = data.current_path;
        $('crumbPath').textContent = currentPath;
        $('addBtnPath').textContent = currentPath;
        $('browserCount').textContent =
            `${data.folder_count || 0} folder${data.folder_count !== 1 ? 's' : ''} · ${data.file_count || 0} file${data.file_count !== 1 ? 's' : ''}`;
        $('folderFilter').value = '';
        list.innerHTML = '';

        if (data.parent_path) {
            const up = mkRow('row is-folder', `${ICONS.up}<span class="name">Parent folder</span>`, () => loadFolders(data.parent_path));
            list.appendChild(up);
        }
        (data.folders || []).forEach((folder) => {
            const row = mkRow('row is-folder', `${ICONS.folder}<span class="name"></span>${ICONS.chev}`, () => loadFolders(folder.path));
            row.dataset.name = folder.name.toLowerCase();
            row.querySelector('.name').textContent = folder.name;
            list.appendChild(row);
        });
        (data.files || []).forEach((file) => {
            const row = document.createElement('div');
            row.className = 'row is-file';
            row.innerHTML = `${ICONS.file}<span class="name"></span><span class="size"></span>`;
            row.querySelector('.name').textContent = file.name;
            row.querySelector('.size').textContent = formatSize(file.size);
            list.appendChild(row);
        });
        if (!list.children.length) {
            list.innerHTML = `<div class="empty">${ICONS.inbox}<div class="empty-title">Empty folder</div><div class="empty-sub">No files or subfolders here</div></div>`;
        }
    } catch (err) {
        toast('Error loading folders: ' + err.message, 'error');
    } finally {
        list.style.opacity = '1';
    }
}

function mkRow(cls, html, onActivate) {
    const row = document.createElement('div');
    row.className = cls;
    row.innerHTML = html;
    row.tabIndex = 0; row.setAttribute('role', 'button');
    row.addEventListener('click', onActivate);
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onActivate(); } });
    return row;
}

function filterFolders() {
    const q = $('folderFilter').value.toLowerCase();
    $('folderList').querySelectorAll('.row[data-name]').forEach((row) => {
        row.style.display = row.dataset.name.includes(q) ? '' : 'none';
    });
}

async function createFolder() {
    const name = (window.prompt('New folder name:') || '').trim();
    if (!name) return;
    try {
        const res = await fetch('/api/folders/create', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: currentPath, name }),
        });
        if (!res.ok) {
            const e = await res.json().catch(() => ({}));
            throw new Error(e.detail || 'Could not create folder');
        }
        const data = await res.json();
        toast(`Created "${name}"`);
        loadFolders(data.path);   // navigate into the new folder
    } catch (err) {
        toast(err.message, 'error');
    }
}

/* ---------- Add the download ---------- */
async function addDownload(btn) {
    setLoading(btn, 'Adding…');
    try {
        const flatten = $('flattenCheck').checked;
        let res;
        if (selectedFile) {
            const fd = new FormData();
            fd.append('file', selectedFile);
            fd.append('download_path', currentPath);
            fd.append('skip_parent_folder', 'true');
            fd.append('flatten_all', flatten ? 'true' : 'false');
            if (selectedIndices.length) fd.append('selected_files', JSON.stringify(selectedIndices));
            res = await fetch('/api/download/file', { method: 'POST', body: fd });
        } else {
            const body = { magnet_link: magnetLink, download_path: currentPath, skip_parent_folder: true, flatten_all: flatten };
            if (selectedIndices.length) body.selected_files = selectedIndices;
            res = await fetch('/api/download', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
        }
        if (!res.ok) {
            const e = await res.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(e.detail || 'Failed to start download');
        }
        localStorage.setItem(LAST_PATH_KEY, currentPath);
        toast('Download added');
        resetAddCard();
        refreshDownloads();
    } catch (err) {
        toast('Error adding download: ' + err.message, 'error');
        clearLoading(btn);
    }
}

function resetAddCard() {
    magnetLink = ''; selectedFile = null; selectedResult = null;
    torrentFiles = []; selectedIndices = []; multiFile = false;
    $('magnetInput').value = '';
    $('nyaaInput').value = '';
    $('nyaaResults').innerHTML = '';
    $('flattenCheck').checked = false;
    resetFilePicker();
    setAddView('source');
    clearLoading($('addBtn'));
}

/* ============================================================
   Downloads list
   ============================================================ */
function startPolling() {
    refreshDownloads();
    pollTimer = setInterval(refreshDownloads, 1000);
}

async function refreshDownloads() {
    try {
        const res = await fetch('/api/downloads');
        if (!res.ok) return;
        const data = await res.json();
        renderDownloads(data.downloads || []);
    } catch { /* keep last view on transient error */ }
}

function renderDownloads(items) {
    const list = $('dlList');
    const count = $('dlCount');

    if (!items.length) {
        rowEls.clear();
        list.innerHTML = `<div class="empty">${ICONS.inbox}<div class="empty-title">No downloads yet</div><div class="empty-sub">Add one from the panel above</div></div>`;
        count.hidden = true;
        return;
    }
    count.hidden = false;
    count.textContent = items.length;

    // remove the empty-state if present
    if (list.querySelector('.empty')) list.innerHTML = '';

    const seen = new Set();
    items.forEach((item) => {
        seen.add(item.id);
        let row = rowEls.get(item.id);
        if (!row) { row = buildRow(item.id); rowEls.set(item.id, row); }
        updateRow(row, item);
        list.appendChild(row);  // keep server order (sorted by start_time)
    });
    // drop rows no longer present
    rowEls.forEach((row, id) => {
        if (!seen.has(id)) { row.remove(); rowEls.delete(id); }
    });
}

function buildRow(id) {
    const row = document.createElement('div');
    row.className = 'dl-row';
    row.dataset.id = id;
    row.innerHTML = `
        <div class="dl-row-body">
            <div class="dl-row-top">
                <span class="dl-ic"></span>
                <span class="dl-row-name"></span>
                <span class="dl-pct"></span>
            </div>
            <div class="bar"><div class="bar-fill"></div></div>
            <div class="dl-meta"></div>
        </div>
        <div class="dl-actions"></div>`;
    return row;
}

function updateRow(row, d) {
    const status = d.status || 'downloading';
    const pct = Math.max(0, Math.min(100, d.progress || 0));
    row.dataset.status = status;

    row.querySelector('.dl-row-name').textContent = d.name || 'Fetching metadata…';

    const ic = row.querySelector('.dl-ic');
    const fill = row.querySelector('.bar-fill');
    const pctEl = row.querySelector('.dl-pct');
    const meta = row.querySelector('.dl-meta');
    const actions = row.querySelector('.dl-actions');

    if (status === 'completed') {
        ic.className = 'dl-ic done'; ic.innerHTML = ICONS.check;
        fill.className = 'bar-fill done'; fill.style.width = '100%';
        pctEl.className = 'dl-pct done'; pctEl.textContent = '100%';
        meta.textContent = `Saved to ${displayPath(d.path)}`;
        setActions(actions, 'clear', d.id);
    } else if (status === 'error') {
        ic.className = 'dl-ic err'; ic.innerHTML = ICONS.x;
        fill.className = 'bar-fill err'; fill.style.width = (pct || 100) + '%';
        pctEl.className = 'dl-pct err'; pctEl.textContent = 'Error';
        meta.textContent = d.error || 'Download failed';
        setActions(actions, 'clear', d.id);
    } else if (status === 'cancelled') {
        ic.className = 'dl-ic'; ic.innerHTML = ICONS.x;
        fill.className = 'bar-fill'; fill.style.width = pct + '%';
        pctEl.className = 'dl-pct'; pctEl.textContent = 'Cancelled';
        meta.textContent = 'Download cancelled';
        setActions(actions, 'clear', d.id);
    } else {
        ic.className = 'dl-ic'; ic.innerHTML = ICONS.spin;
        fill.className = 'bar-fill'; fill.style.width = pct + '%';
        pctEl.className = 'dl-pct'; pctEl.textContent = pct.toFixed(1) + '%';
        const parts = [
            `↓ ${formatRate(d.download_rate)}`,
            `↑ ${formatRate(d.upload_rate)}`,
            `${d.num_peers || 0} peers`,
        ];
        if (d.total_size > 0) parts.push(`${formatMB(d.total_download)} / ${formatMB(d.total_size)}`);
        parts.push(pct >= 99.9 ? 'finishing…' : (d.eta_seconds > 0 ? `${formatTime(d.eta_seconds)} left` : 'estimating…'));
        meta.textContent = parts.join('  ·  ');
        setActions(actions, 'cancel', d.id);
    }
}

function setActions(container, kind, id) {
    if (container.dataset.kind === kind) return;
    container.dataset.kind = kind;
    container.innerHTML = '';
    const btn = document.createElement('button');
    if (kind === 'cancel') {
        btn.className = 'icon-btn danger';
        btn.title = 'Cancel & delete files';
        btn.innerHTML = ICONS.trash;
        btn.addEventListener('click', () => cancelDownload(id));
    } else {
        btn.className = 'icon-btn';
        btn.title = 'Remove from list (keeps files)';
        btn.innerHTML = ICONS.closeX;
        btn.addEventListener('click', () => dismissDownload(id));
    }
    container.appendChild(btn);
}

async function cancelDownload(id) {
    if (!confirm('Cancel this download and delete its partial files?')) return;
    try {
        await fetch('/api/cancel', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ download_id: id }),
        });
        await fetch('/api/dismiss', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ download_id: id }),
        });
        toast('Download cancelled');
        refreshDownloads();
    } catch (err) {
        toast('Error cancelling: ' + err.message, 'error');
    }
}

async function dismissDownload(id) {
    try {
        await fetch('/api/dismiss', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ download_id: id }),
        });
        refreshDownloads();
    } catch (err) {
        toast('Error: ' + err.message, 'error');
    }
}

/* ============================================================
   Wire up
   ============================================================ */
document.addEventListener('DOMContentLoaded', () => {
    checkPlexHealth();
    startPolling();

    document.querySelectorAll('.tab').forEach((t) =>
        t.addEventListener('click', () => switchTab(t.dataset.tab)));

    // File source
    const dz = $('dropzone'), fi = $('fileInput');
    dz.addEventListener('click', () => fi.click());
    dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fi.click(); } });
    fi.addEventListener('change', (e) => onFileChosen(e.target.files[0]));
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach((ev) => {
        dz.addEventListener(ev, (e) => { e.preventDefault(); e.stopPropagation(); });
        document.body.addEventListener(ev, (e) => e.preventDefault(), false);
    });
    ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, () => dz.classList.add('dragover')));
    ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, () => dz.classList.remove('dragover')));
    dz.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) onFileChosen(f); });
    $('fileContinue').addEventListener('click', () => fetchTorrentInfo($('fileContinue')));

    // Magnet
    $('magnetContinue').addEventListener('click', continueFromMagnet);
    $('magnetInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') continueFromMagnet(); });
    $('magnetInput').addEventListener('input', () => { magnetLink = ''; torrentFiles = []; selectedIndices = []; });

    // Nyaa
    $('nyaaSearchBtn').addEventListener('click', searchNyaa);
    $('nyaaInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') searchNyaa(); });

    // Files view
    $('fsToggleAll').addEventListener('click', toggleAllFiles);
    $('filesContinue').addEventListener('click', goToDest);
    $('filesBack').addEventListener('click', () => setAddView('source'));

    // Dest view
    $('folderFilter').addEventListener('input', filterFolders);
    $('newFolderBtn').addEventListener('click', createFolder);
    $('addBtn').addEventListener('click', () => addDownload($('addBtn')));
    $('destBack').addEventListener('click', () => setAddView(multiFile ? 'files' : 'source'));

    // Plex
    $('plexLibrary').addEventListener('change', () => { $('refreshBtn').disabled = !$('plexLibrary').value; });
    $('refreshBtn').addEventListener('click', refreshPlex);

    $('magnetInput').focus();
});
