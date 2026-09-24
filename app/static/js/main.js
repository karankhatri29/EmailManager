// Entry point: sign-in gate, tabs, sync polling. Each view lives in its own module.

import { loadActivities, initActivity } from './activity.js';
import { initAccounts, loadAccounts, renderAccountFilter } from './accounts.js';
import { setUnauthorizedHandler, api } from './api.js';
import { initAuth, logout } from './auth.js';
import { initAccountSettings } from './accountsettings.js';
import { initDashboard, loadEmails, openEmailDrawer, renderCharts } from './dashboard.js';
import { initExplorer } from './explorer.js';
import { hydrateIcons } from './icons.js';
import { initKpis } from './kpis.js';
import { initNotifications, loadNotifications } from './notifications.js';
import { initTimetable, loadTimetable } from './timetable.js';
import { initTodos, loadTodos } from './todos.js';
import { initTools, loadTools, showPanel } from './tools.js';
import { initTrips, loadTrips } from './trips.js';
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
    const tabs = {
        dashboard: 'view-dashboard', activity: 'view-activity', trips: 'view-trips',
        tools: 'view-tools', timetable: 'view-timetable', todos: 'view-todos',
    };
    const blockViews = ['view-timetable', 'view-todos']; // these scroll as a normal block; the others are flex columns
    // The bottom bar (phone) and rail (tablet) mirror the tabs, and the top bar shows the page title.
    $('pageTitle').textContent = {
        activity: 'Activity', trips: 'Trips', tools: 'Inbox tools', timetable: 'Timetable', todos: 'To-do',
    }[name] || 'Home';
    document.querySelectorAll('[data-nav]').forEach((item) => {
        if (item.dataset.nav === name) item.setAttribute('aria-current', 'page');
        else item.removeAttribute('aria-current');
    });
    for (const [tab, viewId] of Object.entries(tabs)) {
        const active = tab === name;
        show($(viewId), active, blockViews.includes(viewId) ? 'block' : 'flex');
        const button = $(`tab-${tab}`); // the extra views (tools, timetable, to-do) live in the menu, not the top bar
        if (button) {
            button.classList.toggle('btn-active', active);
            button.classList.toggle('btn-inactive', !active);
        }
    }
    if (name === 'dashboard') renderCharts();
    if (name === 'activity') loadActivities();
    if (name === 'trips') loadTrips().catch(() => {});
    if (name === 'tools') loadTools();
    if (name === 'timetable') loadTimetable();
    if (name === 'todos') loadTodos();
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
    await Promise.all([loadEmails({ silent }), loadAccounts(), loadActivities(), loadTrips().catch(() => {}), loadNotifications()]);
}

async function pollSync() {
    try {
        const s = await api('/api/sync/status');
        document.querySelectorAll('[data-action="sync"]').forEach((item) => item.classList.toggle('is-syncing', s.syncing));

        if (s.syncing) {
            setSyncStatus('<span class="inline-block w-2 h-2 rounded-full bg-blue-400 animate-pulse mr-2"></span>Syncing in background…', 'text-blue-400');
            await loadEmails({ silent: true }); // show new emails and summaries as they land
        } else if (s.error) {
            setSyncStatus(`Sync problem: ${s.error.replace(/^\w+: /, '')}`, 'text-red-400');
        } else if (s.finished_at) {
            setSyncStatus(`Synced ${new Date(s.finished_at).toLocaleTimeString()}`, 'text-emerald-400');
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
    loadNotifications();
    loadTrips().catch(() => {}); // starts the ticket scan if this mailbox has not been scanned lately
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

// Which layout is active: phone / tablet / laptop / desktop (see css/adaptive.css).
export function uiMode(width = window.innerWidth) {
    if (width < 640) return 'phone';
    if (width < 1024) return 'tablet';
    return width < 1536 ? 'laptop' : 'desktop';
}
const applyUiMode = () => { document.documentElement.dataset.ui = uiMode(); };

// The 24h / 7d / 30d chips on the dashboard mirror the timeframe.
function syncTimeChips() {
    document.querySelectorAll('[data-timeframe]').forEach((chip) => {
        chip.setAttribute('aria-pressed', String(chip.dataset.timeframe === state.timeframe));
    });
}

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
    initExplorer({ openEmail: openEmailDrawer });
    initTrips({ openEmail: (id, email) => openEmailDrawer(id, email), goTrips: () => switchTab('trips') });
    initTools({ openEmail: openEmailDrawer });
    initTimetable();
    initTodos();
    initAccountSettings();
    initNotifications({ goTools: (kind) => { showPanel({ followup: 'followups', cleanup: 'newsletters' }[kind] || 'briefing'); switchTab('tools'); }, goActivity: () => switchTab('activity') });
    hydrateIcons();
    document.addEventListener('taskschanged', () => loadActivities()); // a task added from the inbox shows in the calendar
    initKpis({
        openEmail: openEmailDrawer,
        goActivity: () => switchTab('activity'),
        refreshActivities: loadActivities,
        filterAccount: async (id) => { // clicking a mailbox in the breakdown shows just that mailbox
            state.accountFilter = id;
            renderAccountFilter();
            await loadEmails();
        },
    });

    // Settings (theme, accent, ...) are applied when settings.js loads; here we wire the dialog and the
    // saved default timeframe.
    state.timeframe = getPrefs().timeframe;
    $('timeFilter').value = state.timeframe;
    initSettings({
        onTimeframeChange: async (timeframe) => {
            state.timeframe = timeframe;
            $('timeFilter').value = timeframe;
            syncTimeChips();
            await loadEmails();
        },
    });

    $('tab-dashboard').addEventListener('click', () => switchTab('dashboard'));
    $('tab-activity').addEventListener('click', () => switchTab('activity'));
    $('tab-trips').addEventListener('click', () => switchTab('trips'));
    $('syncNow').addEventListener('click', syncNow);

    // Phone bottom bar and tablet rail.
    document.querySelectorAll('[data-nav]').forEach((item) => item.addEventListener('click', () => {
        switchTab(item.dataset.nav);
        if (!isDesktop()) setDrawer(false); // the menu sheet closes once you have chosen where to go
    }));
    document.querySelectorAll('[data-action="sync"]').forEach((item) => item.addEventListener('click', syncNow));
    document.querySelectorAll('[data-action="menu"]').forEach((item) => item.addEventListener('click', () => setDrawer(true)));
    document.querySelectorAll('[data-timeframe]').forEach((chip) => chip.addEventListener('click', () => {
        $('timeFilter').value = chip.dataset.timeframe;
        $('timeFilter').dispatchEvent(new Event('change'));
    }));
    syncTimeChips();
    applyUiMode();
    for (const query of ['(min-width: 640px)', '(min-width: 1024px)', '(min-width: 1536px)']) {
        window.matchMedia(query).addEventListener('change', () => { applyUiMode(); renderCharts(); });
    }

    $('sidebarToggle').addEventListener('click', toggleSidebar);
    $('menuBtn').addEventListener('click', () => setDrawer(true));
    $('sidebarBackdrop').addEventListener('click', () => setDrawer(false));
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setDrawer(false); });
    window.matchMedia('(min-width: 1024px)').addEventListener('change', () => { setDrawer(false); renderCharts(); });
    $('timeFilter').addEventListener('change', async (event) => {
        state.timeframe = event.target.value;
        syncTimeChips();
        if (!isDesktop()) setDrawer(false);
        await loadEmails();
    });
    $('logout').addEventListener('click', async () => {
        await logout();
        showAuth();
    });

    boot();
});
