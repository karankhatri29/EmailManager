// The home dashboard's numbers and widgets: key-number tiles, the "Do this first" card, top senders,
// a per-mailbox breakdown and a busiest-hours chart. Everything is computed in the browser from data we
// already load (emails, scheduled tasks and the activity list), and every item can be switched off in
// Settings. Guidelines followed: few numbers, each one actionable, colour = threshold (red overdue,
// orange due today, blue upcoming, green fine), and the most important thing at the top left.

import { api } from './api.js';
import { providerLabel } from './accounts.js';
import { KPI_CATALOG } from './dashboard-catalog.js';
import { cssVar, getDashPrefs } from './settings.js';
import { CATEGORY_COLORS, state } from './state.js';
import { $, esc, localYmd, senderName, timeOf, toast } from './util.js';

const URGENT = 'Urgent / Action Required';
const TIMEFRAME_DAYS = { 'Last 1 Day': 1, 'Last 1 Week': 7, 'Last 1 Month': 30 };

// What the dashboard can ask the rest of the app to do (wired in main.js).
let hooks = { openEmail() {}, goActivity() {}, filterAccount() {}, refreshActivities() {} };

// --- icons (Heroicons outline paths) -------------------------------------------------------------

const ICON_PATHS = {
    alert: 'M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z',
    clock: 'M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
    calendar: 'M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5',
    check: 'M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
    inbox: 'M2.25 13.5h3.86a2.25 2.25 0 0 1 2.012 1.244l.256.512a2.25 2.25 0 0 0 2.013 1.244h3.218a2.25 2.25 0 0 0 2.013-1.244l.256-.512a2.25 2.25 0 0 1 2.013-1.244h3.859M2.25 13.5V6.75A2.25 2.25 0 0 1 4.5 4.5h15a2.25 2.25 0 0 1 2.25 2.25v6.75M2.25 13.5v4.5A2.25 2.25 0 0 0 4.5 20.25h15a2.25 2.25 0 0 0 2.25-2.25v-4.5',
    star: 'M11.48 3.499a.562.562 0 0 1 1.04 0l2.125 5.111a.563.563 0 0 0 .475.345l5.518.442c.499.04.701.663.321.988l-4.204 3.602a.563.563 0 0 0-.182.557l1.285 5.385a.562.562 0 0 1-.84.61l-4.725-2.885a.562.562 0 0 0-.586 0L6.982 20.54a.562.562 0 0 1-.84-.61l1.285-5.386a.562.562 0 0 0-.182-.557l-4.204-3.602a.562.562 0 0 1 .321-.988l5.518-.442a.563.563 0 0 0 .475-.345L11.48 3.5Z',
    bookmark: 'M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0 1 11.186 0Z',
    archive: 'm20.25 7.5-.625 10.632a2.25 2.25 0 0 1-2.247 2.118H6.622a2.25 2.25 0 0 1-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125Z',
    bolt: 'm3.75 13.5 10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75Z',
};
const icon = (name, cls = 'w-5 h-5') =>
    `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor" class="${cls}" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="${ICON_PATHS[name]}" /></svg>`;

// --- data ----------------------------------------------------------------------------------------

// All-day items are stored as midnight UTC of their date; timed items are real instants (same rule as activity.js).
const activityDay = (a) => (a.all_day ? a.start_at.slice(0, 10) : localYmd(new Date(a.start_at)));
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);

// Tasks are read from a wide window around today so overdue items and next week's items are both there.
export async function loadDashActivities() {
    const now = new Date();
    try {
        state.dashActivities = await api('/api/activities', {
            params: { start: addDays(now, -90).toISOString(), end: addDays(now, 60).toISOString() },
        });
    } catch (err) {
        console.error('Could not load tasks for the dashboard:', err);
        state.dashActivities = null; // the task numbers then show as unavailable
    }
}

export function computeMetrics() {
    const emails = state.emails;
    const today = localYmd(new Date());
    const m = {
        total: emails.length,
        urgent: emails.filter((e) => e.category === URGENT).length,
        important: emails.filter((e) => e.category === 'Important').length,
        promo: emails.filter((e) => e.category === 'Promotional').length,
        days: TIMEFRAME_DAYS[state.timeframe] || 1,
        tasks: state.dashActivities !== null && state.dashActivities !== undefined,
    };

    const byHour = Array(24).fill(0);
    emails.forEach((e) => { byHour[new Date(e.date).getHours()] += 1; });
    m.byHour = byHour;
    m.busiest = emails.length ? byHour.indexOf(Math.max(...byHour)) : null;

    if (m.tasks) {
        const all = state.dashActivities;
        const open = all.filter((a) => a.start_at && a.status !== 'done');
        const weekAgo = localYmd(addDays(new Date(), -6));
        const weekAhead = localYmd(addDays(new Date(), 7));
        m.overdue = open.filter((a) => activityDay(a) < today);
        m.dueToday = open.filter((a) => activityDay(a) === today);
        m.next7 = open.filter((a) => activityDay(a) > today && activityDay(a) <= weekAhead);
        m.unscheduled = all.filter((a) => !a.start_at && a.status !== 'done');
        const thisWeek = all.filter((a) => a.start_at && activityDay(a) >= weekAgo && activityDay(a) <= today);
        m.week = { total: thisWeek.length, done: thisWeek.filter((a) => a.status === 'done').length };
    }
    return m;
}

const hourLabel = (h) => `${h % 12 === 0 ? 12 : h % 12} ${h < 12 ? 'AM' : 'PM'}`;

// --- key-number tiles ----------------------------------------------------------------------------

const unavailable = (label, ic) => ({ label, value: '–', sub: 'Could not load tasks', tone: 'neutral', icon: ic });

const TILES = {
    needsAction: (m) => ({
        label: 'Needs action', value: m.urgent, icon: 'alert', go: 'list',
        sub: m.urgent ? 'Urgent emails to handle' : 'Nothing urgent',
        tone: m.urgent ? 'urgent' : 'good',
    }),
    overdue: (m) => !m.tasks ? unavailable('Overdue', 'clock') : ({
        label: 'Overdue', value: m.overdue.length, icon: 'clock', go: 'activity',
        sub: m.overdue.length ? 'Do these first' : 'Nothing overdue',
        tone: m.overdue.length ? 'urgent' : 'good',
    }),
    dueToday: (m) => !m.tasks ? unavailable('Due today', 'calendar') : ({
        label: 'Due today', value: m.dueToday.length, icon: 'calendar', go: 'activity',
        sub: m.dueToday.length ? 'Still open' : 'Nothing due today',
        tone: m.dueToday.length ? 'warn' : 'good',
    }),
    next7: (m) => !m.tasks ? unavailable('Next 7 days', 'calendar') : ({
        label: 'Next 7 days', value: m.next7.length, icon: 'calendar', go: 'activity',
        sub: m.next7.length ? 'Coming up' : 'A clear week ahead',
        tone: 'info',
    }),
    completion: (m) => {
        if (!m.tasks) return unavailable('On-time completion', 'check');
        if (!m.week.total) return { label: 'On-time completion', value: '–', icon: 'check', sub: 'No tasks scheduled this week', tone: 'neutral' };
        const pct = Math.round((m.week.done / m.week.total) * 100);
        return {
            label: 'On-time completion', value: `${pct}%`, icon: 'check', go: 'activity',
            sub: `${m.week.done} of ${m.week.total} done this week`,
            tone: pct >= 80 ? 'good' : pct >= 50 ? 'warn' : 'urgent',
        };
    },
    emails: (m) => ({
        label: 'Emails received', value: m.total, icon: 'inbox',
        sub: m.total ? `About ${Math.max(1, Math.round(m.total / m.days))} a day` : 'No mail in this period',
        tone: 'brand',
    }),
    important: (m) => ({
        label: 'Important mail', value: m.important, icon: 'star', go: 'list',
        sub: m.important ? 'Worth a look' : 'Nothing flagged', tone: 'good',
    }),
    unscheduled: (m) => !m.tasks ? unavailable('Unscheduled backlog', 'bookmark') : ({
        label: 'Unscheduled backlog', value: m.unscheduled.length, icon: 'bookmark', go: 'activity',
        sub: m.unscheduled.length ? 'Waiting for a date' : 'Everything has a date',
        tone: m.unscheduled.length ? 'neutral' : 'good',
    }),
    promo: (m) => {
        const pct = m.total ? Math.round((m.promo / m.total) * 100) : 0;
        return {
            label: 'Promotional share', value: m.total ? `${pct}%` : '–', icon: 'archive',
            sub: m.total ? 'of your mail is promotional' : 'No mail in this period',
            tone: pct >= 50 ? 'warn' : 'neutral',
        };
    },
    busiestHour: (m) => ({
        label: 'Busiest hour', value: m.busiest === null ? '–' : hourLabel(m.busiest), icon: 'clock',
        sub: m.busiest === null ? 'No mail in this period' : `${m.byHour[m.busiest]} email${m.byHour[m.busiest] === 1 ? '' : 's'} then`,
        tone: 'neutral',
    }),
};

function tileHtml(id, tile) {
    const clickable = Boolean(tile.go);
    return `
        <button type="button" data-kpi="${id}" data-go="${tile.go || ''}" class="stat-tile tone-${tile.tone} text-left ${clickable ? 'cursor-pointer' : 'cursor-default'}" ${clickable ? '' : 'tabindex="-1"'}>
            <span class="stat-icon">${icon(tile.icon, 'w-5 h-5')}</span>
            <p class="stat-label">${esc(tile.label)}</p>
            <p class="stat-num">${esc(tile.value)}</p>
            <p class="stat-sub">${esc(tile.sub)}</p>
        </button>`;
}

function renderTiles(m) {
    const { kpis } = getDashPrefs();
    $('kpiGrid').innerHTML = KPI_CATALOG.filter((k) => kpis.includes(k.id)).map((k) => tileHtml(k.id, TILES[k.id](m))).join('');
}

// --- "Do this first" -----------------------------------------------------------------------------

const byStart = (a, b) => String(a.start_at).localeCompare(String(b.start_at));

function focusItem(m) {
    if (m.tasks && m.overdue.length) {
        const a = [...m.overdue].sort(byStart)[0];
        const when = new Date(`${activityDay(a)}T12:00:00`).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
        return { kind: 'activity', a, title: a.title, why: `Overdue since ${when}`, tone: 'urgent' };
    }
    if (m.tasks && m.dueToday.length) {
        const a = [...m.dueToday].sort(byStart)[0];
        return { kind: 'activity', a, title: a.title, why: a.all_day ? 'Due today' : `Due today at ${timeOf(new Date(a.start_at))}`, tone: 'warn' };
    }
    const t = (state.tasks || [])[0];
    if (t) return { kind: 'task', t, title: t.task, why: t.deadline, tone: t.sort_tier <= 2 ? 'warn' : 'info', from: senderName(t.sender) };
    return null;
}

function renderFocus(m) {
    const item = focusItem(m);
    const card = $('card-focus');
    if (!state.accounts.length && !m.tasks) { card.innerHTML = ''; return; }

    if (!item) {
        card.className = 'glass-card p-4 sm:p-5 tone-good focus-card';
        card.innerHTML = `
            <div class="flex items-center gap-4">
                <span class="focus-icon">${icon('check', 'w-6 h-6')}</span>
                <div><p class="stat-label">Do this first</p>
                    <p class="text-lg font-extrabold text-fg leading-snug mt-0.5">You're all caught up</p>
                    <p class="text-sm text-slate-400 mt-0.5">Nothing overdue or due today. Enjoy the quiet.</p></div>
            </div>`;
        card.dataset.focusKind = '';
        return;
    }

    card.className = `glass-card p-4 sm:p-5 tone-${item.tone} focus-card`;
    card.dataset.focusKind = item.kind;
    card.dataset.focusId = item.kind === 'activity' ? item.a.id : item.t.id;
    card.innerHTML = `
        <div class="flex items-start gap-4">
            <span class="focus-icon">${icon('bolt', 'w-6 h-6')}</span>
            <div class="min-w-0 flex-1">
                <p class="stat-label">Do this first</p>
                <p class="text-lg sm:text-xl font-extrabold text-fg leading-snug mt-0.5 break-words">${esc(item.title)}</p>
                <div class="flex flex-wrap items-center gap-2 mt-2">
                    <span class="chip">${esc(item.why)}</span>
                    ${item.from ? `<span class="text-xs text-slate-400 truncate">from ${esc(item.from)}</span>` : ''}
                    ${item.kind === 'activity' && item.a.source === 'email' ? '<span class="text-xs text-slate-400">from an email</span>' : ''}
                </div>
            </div>
        </div>
        <div class="flex flex-wrap gap-2 mt-4">
            <button type="button" data-focus="open" class="bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium px-5 py-2 rounded-lg cursor-pointer">Open</button>
            ${item.kind === 'activity' ? '<button type="button" data-focus="done" class="bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-200 text-sm font-semibold px-5 py-2 rounded-lg cursor-pointer">Mark done</button>' : ''}
        </div>`;
}

async function markFocusDone(id) {
    try {
        await api(`/api/activities/${id}`, { method: 'PATCH', body: { status: 'done' } });
        toast('Marked done. Nice one.', 'success', 2500);
        await loadDashActivities();
        renderAll();
        await hooks.refreshActivities();
    } catch (err) {
        toast(err.message, 'error');
    }
}

// --- top senders ---------------------------------------------------------------------------------

function renderSenders() {
    const groups = new Map();
    state.emails.forEach((e) => {
        const name = senderName(e.sender) || 'Unknown';
        const key = name.toLowerCase();
        const g = groups.get(key) || { name, count: 0, cats: {} };
        g.count += 1;
        g.cats[e.category] = (g.cats[e.category] || 0) + 1;
        groups.set(key, g);
    });
    const top = [...groups.values()].sort((a, b) => b.count - a.count).slice(0, 5);
    const host = $('sendersList');
    if (!top.length) {
        host.innerHTML = '<p class="text-sm text-slate-500 py-6 text-center">No mail in this period.</p>';
        return;
    }
    const max = top[0].count;
    host.innerHTML = top.map((g) => {
        const dominant = Object.entries(g.cats).sort((a, b) => b[1] - a[1])[0][0];
        const noisy = dominant === 'Promotional' && g.count >= 3;
        return `
        <div class="flex items-center gap-3 py-2">
            <span class="w-8 h-8 rounded-full bg-blue-500/15 text-blue-400 flex items-center justify-center text-xs font-bold shrink-0" aria-hidden="true">${esc(g.name.charAt(0).toUpperCase())}</span>
            <div class="min-w-0 flex-1">
                <div class="flex items-center justify-between gap-2">
                    <span class="text-sm font-semibold text-fg truncate">${esc(g.name)}</span>
                    <span class="text-xs font-bold text-slate-300 tabular-nums">${g.count}</span>
                </div>
                <div class="h-1.5 rounded-full bg-slate-700/50 mt-1.5 overflow-hidden">
                    <div class="h-full rounded-full" style="width:${Math.round((g.count / max) * 100)}%;background:${CATEGORY_COLORS[dominant] || 'currentColor'}"></div>
                </div>
                ${noisy ? '<p class="text-[11px] text-slate-400 mt-1">Mostly promotional. Worth unsubscribing?</p>' : ''}
            </div>
        </div>`;
    }).join('');
}

// --- mailbox breakdown ---------------------------------------------------------------------------

function renderMailboxes() {
    const host = $('mailboxList');
    const rows = state.accounts.map((a) => {
        const mine = state.emails.filter((e) => e.account_id === a.id);
        return { a, total: mine.length, urgent: mine.filter((e) => e.category === URGENT).length };
    });
    const max = Math.max(1, ...rows.map((r) => r.total));
    host.innerHTML = rows.map(({ a, total, urgent }) => `
        <button type="button" data-account="${a.id}" class="w-full text-left py-2 group cursor-pointer">
            <div class="flex items-center justify-between gap-2">
                <span class="text-sm font-semibold text-fg truncate group-hover:text-blue-400 transition-colors" title="${esc(a.email_address)}">${esc(a.email_address)}</span>
                <span class="text-xs font-bold text-slate-300 tabular-nums">${total}</span>
            </div>
            <div class="flex items-center gap-2 mt-1">
                <div class="h-1.5 rounded-full bg-slate-700/50 flex-1 overflow-hidden"><div class="h-full rounded-full bg-blue-500" style="width:${Math.round((total / max) * 100)}%"></div></div>
                <span class="text-[11px] text-slate-400 whitespace-nowrap">${esc(providerLabel(a))}${urgent ? ` · ${urgent} urgent` : ''}${a.status === 'needs_reauth' ? ' · needs reconnecting' : ''}</span>
            </div>
        </button>`).join('');
}

// --- busiest hours chart -------------------------------------------------------------------------

function renderBusyHours(m) {
    if (!window.Plotly || $('card-busyHours').classList.contains('hidden')) return;
    const brand = `hsl(${cssVar('--brand-h') || 262} 92% 62%)`;
    Plotly.react('busyChart', [{
        x: m.byHour.map((_, h) => hourLabel(h)),
        y: m.byHour,
        type: 'bar',
        marker: { color: brand, opacity: 0.9 },
        hovertemplate: '%{x}: %{y} emails<extra></extra>',
    }], {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: cssVar('--chart-text'), family: 'Plus Jakarta Sans, Inter, sans-serif', size: 11 },
        margin: { t: 10, b: 32, l: 30, r: 8 },
        bargap: 0.25,
        xaxis: { tickmode: 'array', tickvals: m.byHour.map((_, h) => hourLabel(h)).filter((_, h) => h % 3 === 0), showgrid: false },
        yaxis: { gridcolor: cssVar('--chart-grid'), zeroline: false, rangemode: 'tozero', tickformat: 'd' },
        showlegend: false,
    }, { displayModeBar: false, responsive: true });
}

// --- assembling ----------------------------------------------------------------------------------

// Which widgets are visible, from Settings (and from the data: the mailbox breakdown needs 2+ mailboxes).
export const widgetOn = (id) => !$(`card-${id}`).classList.contains('hidden');

function applyVisibility() {
    const { widgets, kpis } = getDashPrefs();
    const on = (id) => widgets.includes(id);
    const show = (id, visible) => $(`card-${id}`).classList.toggle('hidden', !visible);

    show('focus', on('focus'));
    show('priorityMix', on('priorityMix'));
    show('volume', on('volume'));
    show('senders', on('senders'));
    show('mailboxes', on('mailboxes') && state.accounts.length > 1 && state.accountFilter === null);
    show('busyHours', on('busyHours'));

    $('kpiGrid').classList.toggle('hidden', kpis.length === 0);
    $('chartRow').classList.toggle('hidden', !(on('priorityMix') || on('volume')));
    $('insightRow').classList.toggle('hidden', !widgetOn('senders') && !widgetOn('mailboxes'));
}

export function renderAll() {
    applyVisibility();
    const m = computeMetrics();
    renderTiles(m);
    if (widgetOn('focus')) renderFocus(m);
    if (widgetOn('senders')) renderSenders();
    if (widgetOn('mailboxes')) renderMailboxes();
    renderBusyHours(m);
    return m;
}

export function initKpis(wiring) {
    hooks = { ...hooks, ...wiring };

    $('kpiGrid').addEventListener('click', (event) => {
        const tile = event.target.closest('[data-kpi]');
        if (!tile || !tile.dataset.go) return;
        if (tile.dataset.go === 'activity') hooks.goActivity();
        else $('taskList').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });

    $('card-focus').addEventListener('click', (event) => {
        const button = event.target.closest('[data-focus]');
        if (!button) return;
        const card = $('card-focus');
        const id = card.dataset.focusId;
        if (button.dataset.focus === 'done') return markFocusDone(Number(id));
        if (card.dataset.focusKind === 'task') return hooks.openEmail(id);
        const activity = (state.dashActivities || []).find((a) => a.id === Number(id));
        if (activity && activity.email_id) hooks.openEmail(activity.email_id);
        else hooks.goActivity();
    });

    $('mailboxList').addEventListener('click', (event) => {
        const row = event.target.closest('[data-account]');
        if (row) hooks.filterAccount(Number(row.dataset.account));
    });

    // 'dashchange' (Settings switches) is handled in dashboard.js, which re-renders these and the charts.
    document.addEventListener('themechange', () => renderBusyHours(computeMetrics()));
}
