// Entry point: sign-in gate, tabs, sync polling. Each view lives in its own module.

import { loadActivities, initActivity } from './activity.js';
import { initAccounts, loadAccounts } from './accounts.js';
import { setUnauthorizedHandler, api } from './api.js';
import { initAuth, logout } from './auth.js';
import { initDashboard, loadEmails, renderCharts } from './dashboard.js';
import { getPrefs, initSettings } from './settings.js';
import { state } from './state.js';
import { $, show, toast } from './util.js';

const POLL_MS = 4000;
let pollTimer = null;
let wasSyncing = false;
let booted = false;

// --- screens -------------------------------------------------------------------------------------

function showAuth() {
    clearInterval(pollTimer);
    pollTimer = null;
    state.user = null;
    show($('appShell'), false);
    show($('authScreen'), true);
}

function showApp() {
    show($('authScreen'), false);
    show($('appShell'), true);
    $('userEmail').textContent = state.user.email;
}

function switchTab(name) {
    const tabs = { dashboard: 'view-dashboard', activity: 'view-activity' };
    for (const [tab, viewId] of Object.entries(tabs)) {
        const active = tab === name;
        show($(viewId), active);
        const button = $(`tab-${tab}`);
        button.classList.toggle('btn-active', active);
        button.classList.toggle('btn-inactive', !active);
    }
    if (name === 'dashboard') renderCharts();
    if (name === 'activity') loadActivities();
}

// --- background sync status ----------------------------------------------------------------------

function setSyncStatus(html, cls) {
    const el = $('syncStatus');
    el.className = `ml-auto self-center text-xs font-medium ${cls}`;
    el.innerHTML = html;
    // Phones hide the top-bar copy; the menu shows this one instead.
    const side = $('syncStatusSide');
    if (side) {
        side.className = `sm:hidden text-xs font-medium mb-4 min-h-[1rem] ${cls}`;
        side.innerHTML = html;
    }
}

async function refreshEverything({ silent = true } = {}) {
    await Promise.all([loadEmails({ silent }), loadAccounts(), loadActivities()]);
}

async function pollSync() {
    try {
        const s = await api('/api/sync/status');

        if (s.syncing) {
            setSyncStatus('<span class="inline-block w-2 h-2 rounded-full bg-blue-400 animate-pulse mr-2"></span>Syncing in background…', 'text-blue-400');
            await loadEmails({ silent: true }); // show new emails and summaries as they land
        } else if (s.error) {
            setSyncStatus(`⚠ Sync problem: ${s.error.replace(/^\w+: /, '')}`, 'text-red-400');
        } else if (s.finished_at) {
            setSyncStatus(`✓ Synced ${new Date(s.finished_at).toLocaleTimeString()}`, 'text-emerald-400');
        } else {
            setSyncStatus('', 'text-slate-500');
        }

        if (wasSyncing && !s.syncing) await refreshEverything(); // includes calendar items created from new mail
        wasSyncing = s.syncing;
    } catch (err) {
        if (err.status !== 401) console.error('Status poll failed:', err);
    }
}

async function syncNow() {
    if (!isDesktop()) setDrawer(false); // reveal the dashboard again after tapping Sync in the drawer
    try {
        await api('/api/sync', { method: 'POST', params: { time_filter: state.timeframe } });
        await pollSync();
    } catch (err) {
        toast(err.message, 'error');
    }
}

// --- boot ----------------------------------------------------------------------------------------

// Google sends the browser back to /?connected=... or /?connect_error=...
function handleConnectResult() {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get('connected');
    const error = params.get('connect_error');
    if (!connected && !error) return;

    const messages = {
        access_denied: 'Access was not granted, so nothing was connected.',
        invalid_state: 'That sign-in link expired. Please try connecting again.',
        login_required: 'Please sign in first, then connect your mailbox.',
        not_configured: 'Outlook is not set up on this server yet. An administrator needs to add the Microsoft app credentials.',
        failed: 'Could not finish connecting the mailbox. Please try again.',
    };
    if (connected) toast(`Connected ${connected}. Syncing your mail…`, 'success');
    else toast(messages[error] || 'Could not connect the mailbox.', 'error', 8000);
    window.history.replaceState({}, '', window.location.pathname);
}

async function startApp(user) {
    state.user = user;
    showApp();
    switchTab('dashboard');
    if (!booted) {
        booted = true;
        handleConnectResult();
    }

    await loadAccounts();
    await Promise.all([loadEmails(), loadActivities()]); // instant: served from the database
    await pollSync();
    clearInterval(pollTimer);
    pollTimer = setInterval(pollSync, POLL_MS);
}

async function boot() {
    try {
        await startApp(await api('/api/auth/me'));
    } catch {
        showAuth();
    }
}

// Below 1024px the sidebar is an off-canvas drawer (opened from the menu button); above it, a column
// that can be collapsed. The CSS for both lives in index.html and keys off these two classes.
const isDesktop = () => window.matchMedia('(min-width: 1024px)').matches;

function setDrawer(open) {
    $('sidebar').classList.toggle('open', open);
    $('menuBtn').setAttribute('aria-expanded', String(open));
}

function toggleSidebar() {
    $('sidebar').classList.toggle('collapsed');
    setTimeout(renderCharts, 310); // charts re-measure once the width has settled
}

document.addEventListener('DOMContentLoaded', () => {
    setUnauthorizedHandler(showAuth);
    initAuth(startApp);
    initAccounts(
        () => refreshEverything({ silent: false }),
        () => loadEmails(), // the mailbox filter changed: re-read mail and tasks for it
    );
    initDashboard();
    initActivity();

    // Settings (theme, accent, ...) are applied when settings.js loads; here we wire the dialog and the
    // saved default timeframe.
    state.timeframe = getPrefs().timeframe;
    $('timeFilter').value = state.timeframe;
    initSettings({
        onTimeframeChange: async (timeframe) => {
            state.timeframe = timeframe;
            $('timeFilter').value = timeframe;
            await loadEmails();
        },
    });

    $('tab-dashboard').addEventListener('click', () => switchTab('dashboard'));
    $('tab-activity').addEventListener('click', () => switchTab('activity'));
    $('syncNow').addEventListener('click', syncNow);
    $('sidebarToggle').addEventListener('click', toggleSidebar);
    $('menuBtn').addEventListener('click', () => setDrawer(true));
    $('sidebarBackdrop').addEventListener('click', () => setDrawer(false));
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setDrawer(false); });
    window.matchMedia('(min-width: 1024px)').addEventListener('change', () => { setDrawer(false); renderCharts(); });
    $('timeFilter').addEventListener('change', async (event) => {
        state.timeframe = event.target.value;
        if (!isDesktop()) setDrawer(false);
        await loadEmails();
    });
    $('logout').addEventListener('click', async () => {
        await logout();
        showAuth();
    });

    boot();
});
