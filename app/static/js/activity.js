// The Activity tab: a calendar with Day / Week / Month views plus a backlog of unscheduled items.
// Everything is editable. The view follows the screen size by default (phone: day, tablet and laptop:
// week, wide desktop: month). Picking a view in the toolbar overrides that until the screen crosses a
// size breakpoint (rotating a phone, resizing a window) or the page is reloaded.

import { api } from './api.js';
import { openEmailDrawer } from './dashboard.js';
import { state } from './state.js';
import { $, esc, localYmd, timeOf, toast } from './util.js';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MAX_CHIPS = 3;

// --- dates ---------------------------------------------------------------------------------------
// All-day items are stored as midnight UTC of their date, so their day is read in UTC.
// Timed items are real instants, so their day is read in the browser's time zone.

const dayKey = (a) => (a.all_day ? a.start_at.slice(0, 10) : localYmd(new Date(a.start_at)));

const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const startOfWeek = (d) => addDays(d, -((d.getDay() + 6) % 7)); // weeks start on Monday

function monthGrid(anchor) {
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const start = startOfWeek(first);
    return Array.from({ length: 42 }, (_, i) => addDays(start, i));
}

const isOverdue = (a) => a.status !== 'done' && a.start_at && dayKey(a) < localYmd(new Date());

// --- views ---------------------------------------------------------------------------------------

export function autoView(width = window.innerWidth) {
    if (width < 640) return 'day';
    if (width < 1280) return 'week';
    return 'month';
}

export const currentView = () => state.calViewOverride || autoView();

export function visibleDays() {
    const anchor = state.calAnchor;
    switch (currentView()) {
        case 'day': return [addDays(anchor, 0)];
        case 'week': return Array.from({ length: 7 }, (_, i) => addDays(startOfWeek(anchor), i));
        default: return monthGrid(anchor);
    }
}

function rangeLabel(view, days) {
    const fmt = (d, opts) => d.toLocaleDateString('en-US', opts);
    if (view === 'month') return fmt(state.calAnchor, { month: 'long', year: 'numeric' });
    if (view === 'day') return fmt(days[0], { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
    const [first, last] = [days[0], days[days.length - 1]];
    const sameMonth = first.getMonth() === last.getMonth();
    return `${fmt(first, { day: 'numeric', month: sameMonth ? undefined : 'short' })} – ${fmt(last, { day: 'numeric', month: 'short', year: 'numeric' })}`;
}

// --- data ----------------------------------------------------------------------------------------

export async function loadActivities() {
    const days = visibleDays();
    // pad by a day each side so all-day items near the edges are never cut off by time-zone offsets
    const start = addDays(days[0], -1);
    const end = addDays(days[days.length - 1], 2);
    state.activities = await api('/api/activities', {
        params: { start: start.toISOString(), end: end.toISOString() },
    });
    renderActivity();
}

async function save(id, changes, { reloadOnError = true } = {}) {
    try {
        const updated = await api(`/api/activities/${id}`, { method: 'PATCH', body: changes });
        state.activities = state.activities.map((a) => (a.id === id ? updated : a));
    } catch (err) {
        toast(err.message, 'error');
        if (reloadOnError) await loadActivities();
    }
    renderActivity();
}

// --- rendering -----------------------------------------------------------------------------------

function chipStyle(a) {
    if (a.status === 'done') return 'bg-slate-700/30 border-slate-600/30 text-slate-500 line-through';
    if (isOverdue(a)) return 'bg-red-500/15 border-red-500/40 text-red-200';
    return a.source === 'email'
        ? 'bg-blue-500/15 border-blue-500/30 text-blue-200'
        : 'bg-violet-500/15 border-violet-500/30 text-violet-200';
}

const byTime = (a, b) => String(a.start_at).localeCompare(String(b.start_at)) || a.title.localeCompare(b.title);

function chip(a) {
    const time = !a.all_day && a.start_at ? `<span class="opacity-70">${timeOf(new Date(a.start_at))}</span> ` : '';
    return `<div draggable="true" data-activity="${a.id}" class="truncate text-[11px] px-1.5 py-0.5 rounded border cursor-pointer ${chipStyle(a)}" title="${esc(a.title)}">${time}${esc(a.title)}</div>`;
}

// Day view: one roomy row per item.
function dayRow(a) {
    const when = a.all_day || !a.start_at
        ? 'All day'
        : `${timeOf(new Date(a.start_at))}${a.end_at ? `–${timeOf(new Date(a.end_at))}` : ''}`;
    return `
        <div draggable="true" data-activity="${a.id}" class="flex items-start gap-3 p-3 rounded-2xl border cursor-pointer ${chipStyle(a)}">
            <span class="text-xs font-bold w-20 shrink-0 pt-0.5 opacity-80">${when}</span>
            <div class="min-w-0">
                <p class="text-sm font-semibold leading-snug">${esc(a.title)}</p>
                ${a.notes ? `<p class="text-xs opacity-70 mt-0.5 line-clamp-2">${esc(a.notes)}</p>` : ''}
                ${a.source === 'email' ? '<p class="text-[10px] opacity-70 mt-0.5">✉ from email</p>' : ''}
            </div>
        </div>`;
}

const GRID_CLASS = {
    month: 'grid grid-cols-7 grid-rows-6 xl:flex-1 h-[26rem] sm:h-[34rem] xl:h-auto xl:min-h-0',
    // phones: a vertical list of days; wider: seven columns
    week: 'grid grid-cols-1 sm:grid-cols-7 sm:grid-rows-1 xl:flex-1 sm:h-[34rem] xl:h-auto xl:min-h-0',
    day: 'grid grid-cols-1 min-h-[22rem] xl:flex-1 xl:min-h-0',
};

function monthCell(d, byDay, todayKey) {
    const key = localYmd(d);
    const items = (byDay[key] || []).sort(byTime);
    const inMonth = d.getMonth() === state.calAnchor.getMonth();
    const more = items.length > MAX_CHIPS ? `<div class="text-[10px] text-slate-500 px-1">+${items.length - MAX_CHIPS} more</div>` : '';
    return `
        <div data-day="${key}" class="min-h-0 overflow-hidden border border-slate-800/80 p-1.5 flex flex-col gap-1 cursor-pointer hover:bg-slate-800/40 ${inMonth ? '' : 'opacity-40'} ${key === todayKey ? 'bg-blue-500/5 ring-1 ring-inset ring-blue-500/40' : ''}">
            <span class="text-[11px] font-medium ${key === todayKey ? 'text-blue-400' : 'text-slate-400'}">${d.getDate()}</span>
            ${items.slice(0, MAX_CHIPS).map(chip).join('')}${more}
        </div>`;
}

function weekCell(d, byDay, todayKey) {
    const key = localYmd(d);
    const items = (byDay[key] || []).sort(byTime);
    const today = key === todayKey;
    return `
        <div data-day="${key}" class="min-h-[4.5rem] sm:min-h-0 overflow-y-auto border border-slate-800/80 p-2 flex flex-row sm:flex-col gap-2 sm:gap-1.5 cursor-pointer hover:bg-slate-800/40 ${today ? 'bg-blue-500/5 ring-1 ring-inset ring-blue-500/40' : ''}">
            <div class="shrink-0 w-12 sm:w-auto ${today ? 'text-blue-400' : 'text-slate-400'}">
                <span class="block uppercase text-[10px] font-bold tracking-wider">${WEEKDAYS[(d.getDay() + 6) % 7]}</span>
                <span class="text-lg sm:text-base font-extrabold leading-none">${d.getDate()}</span>
            </div>
            <div class="flex-1 min-w-0 flex flex-col gap-1">${items.map(chip).join('')}</div>
        </div>`;
}

function dayPanel(d, byDay) {
    const items = (byDay[localYmd(d)] || []).sort(byTime);
    const body = items.length
        ? items.map(dayRow).join('')
        : `<div class="flex-1 flex flex-col items-center justify-center text-center gap-1 py-12 text-slate-400">
               <p class="text-3xl" aria-hidden="true">🌴</p>
               <p class="text-sm font-bold text-fg">Nothing planned</p>
               <p class="text-xs">Tap anywhere to add something for this day.</p>
           </div>`;
    return `<div data-day="${localYmd(d)}" class="p-3 sm:p-5 flex flex-col gap-2.5 overflow-y-auto cursor-pointer">${body}</div>`;
}

function renderCalendar() {
    const view = currentView();
    const days = visibleDays();
    const todayKey = localYmd(new Date());
    const byDay = {};
    state.activities.filter((a) => a.start_at).forEach((a) => { (byDay[dayKey(a)] ||= []).push(a); });

    $('monthLabel').textContent = rangeLabel(view, days);

    // The Mon..Sun header only makes sense for the month grid (week cells carry their own day name).
    const header = $('weekdayRow');
    header.classList.toggle('hidden', view !== 'month');
    header.innerHTML = WEEKDAYS.map((d) => `<div class="text-center text-[11px] font-semibold text-slate-500 py-2">${d}</div>`).join('');

    const grid = $('calendarGrid');
    grid.className = GRID_CLASS[view];
    grid.dataset.view = view;
    grid.innerHTML = view === 'day'
        ? dayPanel(days[0], byDay)
        : days.map((d) => (view === 'week' ? weekCell(d, byDay, todayKey) : monthCell(d, byDay, todayKey))).join('');

    syncViewControls(view);
}

// The toolbar toggle always shows the view that is on screen, whether it was chosen or picked for the size.
function syncViewControls(view) {
    document.querySelectorAll('[data-cal-view]').forEach((button) => {
        button.setAttribute('aria-pressed', String(button.dataset.calView === view));
    });
}

function renderBacklog() {
    const backlog = state.activities.filter((a) => !a.start_at && a.status !== 'done');
    $('backlogCount').textContent = backlog.length;
    $('backlogList').innerHTML = backlog.length
        ? backlog.map((a) => `
            <div draggable="true" data-activity="${a.id}" class="p-2.5 rounded-lg bg-slate-800/50 border border-slate-700/50 hover:border-blue-500/40 cursor-pointer flex items-start gap-2">
                <button data-done="${a.id}" class="mt-0.5 w-4 h-4 rounded border border-slate-500 hover:border-emerald-400 shrink-0 cursor-pointer" title="Mark done"></button>
                <div class="min-w-0">
                    <p class="text-sm text-slate-200 leading-snug">${esc(a.title)}</p>
                    <p class="text-[10px] text-slate-500 mt-0.5">${a.source === 'email' ? '✉ from email · ' : ''}drag onto a day to schedule</p>
                </div>
            </div>`).join('')
        : '<p class="text-xs text-slate-500 p-2">Nothing waiting to be scheduled.</p>';
}

export function renderActivity() {
    renderCalendar();
    renderBacklog();
}

// --- add / edit dialog ---------------------------------------------------------------------------

function openDialog({ activity = null, date = '' } = {}) {
    const f = $('activityForm');
    f.reset();
    f.dataset.id = activity ? activity.id : '';
    $('dialogTitle').textContent = activity ? 'Edit activity' : 'New activity';
    $('activityDelete').classList.toggle('hidden', !activity);

    let day = date;
    let start = '';
    let end = '';
    if (activity) {
        f.elements.title.value = activity.title;
        f.elements.notes.value = activity.notes || '';
        f.elements.done.checked = activity.status === 'done';
        if (activity.start_at) {
            day = dayKey(activity);
            if (!activity.all_day) {
                start = timeOf(new Date(activity.start_at));
                if (activity.end_at) end = timeOf(new Date(activity.end_at));
            }
        }
    }
    f.elements.date.value = day;
    f.elements.start.value = start;
    f.elements.end.value = end;

    const source = state.emails.find((e) => activity && e.id === activity.email_id);
    $('activitySource').innerHTML = activity && activity.source === 'email'
        ? `Created from an email${source ? `: <button type="button" id="viewSourceEmail" class="underline text-blue-400 hover:text-blue-300 cursor-pointer">${esc(source.subject)}</button>` : ''}`
        : '';
    if (source) {
        $('viewSourceEmail').addEventListener('click', () => { closeDialog(); openEmailDrawer(source.id); });
    }

    $('activityModal').classList.remove('hidden');
    f.elements.title.focus();
}

function closeDialog() {
    $('activityModal').classList.add('hidden');
}

// Builds the API payload: no date = backlog; date only = all-day; date + time = a timed event.
function payloadFromForm(f) {
    const title = f.elements.title.value.trim();
    const date = f.elements.date.value;
    const start = f.elements.start.value;
    const end = f.elements.end.value;
    const payload = { title, notes: f.elements.notes.value.trim() || null };

    if (!date) return { ...payload, start_at: null, end_at: null, all_day: false };
    if (!start) return { ...payload, start_at: `${date}T00:00:00Z`, end_at: null, all_day: true };
    return {
        ...payload,
        start_at: new Date(`${date}T${start}`).toISOString(),
        end_at: end ? new Date(`${date}T${end}`).toISOString() : null,
        all_day: false,
    };
}

async function submitDialog(event) {
    event.preventDefault();
    const f = $('activityForm');
    const id = f.dataset.id ? Number(f.dataset.id) : null;
    const payload = payloadFromForm(f);
    if (!payload.title) return;

    try {
        if (id) {
            await api(`/api/activities/${id}`, {
                method: 'PATCH',
                body: { ...payload, status: f.elements.done.checked ? 'done' : 'todo' },
            });
        } else {
            await api('/api/activities', { method: 'POST', body: payload });
        }
        closeDialog();
        await loadActivities();
    } catch (err) {
        toast(err.message, 'error');
    }
}

async function deleteFromDialog() {
    const id = Number($('activityForm').dataset.id);
    if (!window.confirm('Delete this activity?')) return;
    try {
        await api(`/api/activities/${id}`, { method: 'DELETE' });
        closeDialog();
        await loadActivities();
    } catch (err) {
        toast(err.message, 'error');
    }
}

// --- calendar feed (ICS) -------------------------------------------------------------------------

async function showFeed() {
    try {
        const { url } = await api('/api/calendar/feed');
        $('feedUrl').value = url;
        $('feedModal').classList.remove('hidden');
    } catch (err) {
        toast(err.message, 'error');
    }
}

async function rotateFeed() {
    if (!window.confirm('Create a new link? Calendars using the old one will stop updating.')) return;
    try {
        const { url } = await api('/api/calendar/feed/rotate', { method: 'POST' });
        $('feedUrl').value = url;
        toast('New calendar link created', 'success');
    } catch (err) {
        toast(err.message, 'error');
    }
}

async function copyFeed() {
    const input = $('feedUrl');
    input.select();
    try {
        await navigator.clipboard.writeText(input.value);
        toast('Link copied', 'success', 2500);
    } catch {
        toast('Press Ctrl+C to copy the selected link', 'info');
    }
}

// --- drag and drop -------------------------------------------------------------------------------

async function dropOnDay(activityId, day) {
    const a = state.activities.find((x) => x.id === activityId);
    if (!a || (a.start_at && dayKey(a) === day)) return;

    let changes;
    if (!a.start_at || a.all_day) {
        changes = { start_at: `${day}T00:00:00Z`, all_day: true };
    } else {
        // keep the time of day, and the length of the event
        const old = new Date(a.start_at);
        const [y, m, d] = day.split('-').map(Number);
        const moved = new Date(y, m - 1, d, old.getHours(), old.getMinutes());
        changes = { start_at: moved.toISOString() };
        if (a.end_at) changes.end_at = new Date(moved.getTime() + (new Date(a.end_at) - old)).toISOString();
    }

    Object.assign(a, changes); // update immediately, the server confirms below
    renderActivity();
    await save(activityId, changes);
}

// --- wiring --------------------------------------------------------------------------------------

export function initActivity() {
    // Previous / next moves by one day, one week or one month, depending on the view.
    const shift = async (direction) => {
        const a = state.calAnchor;
        const view = currentView();
        state.calAnchor = view === 'day' ? addDays(a, direction)
            : view === 'week' ? addDays(a, 7 * direction)
                : new Date(a.getFullYear(), a.getMonth() + direction, 1);
        await loadActivities();
    };
    $('monthPrev').addEventListener('click', () => shift(-1));
    $('monthNext').addEventListener('click', () => shift(1));
    $('monthToday').addEventListener('click', async () => { state.calAnchor = new Date(); await loadActivities(); });
    $('activityAdd').addEventListener('click', () => {
        openDialog({ date: localYmd(currentView() === 'day' ? state.calAnchor : new Date()) });
    });

    // Manual view choice. It lasts until the screen size class changes, then the screen size decides again.
    document.querySelectorAll('[data-cal-view]').forEach((button) => {
        button.addEventListener('click', async () => {
            state.calViewOverride = button.dataset.calView;
            await loadActivities();
        });
    });
    for (const query of ['(min-width: 640px)', '(min-width: 1280px)']) {
        window.matchMedia(query).addEventListener('change', () => {
            state.calViewOverride = null;
            if (state.user) loadActivities();
        });
    }

    const grid = $('calendarGrid');
    grid.addEventListener('click', (event) => {
        const chipEl = event.target.closest('[data-activity]');
        if (chipEl) {
            openDialog({ activity: state.activities.find((a) => a.id === Number(chipEl.dataset.activity)) });
            return;
        }
        const cell = event.target.closest('[data-day]');
        if (cell) openDialog({ date: cell.dataset.day });
    });

    // Dragging works from calendar chips and from the backlog.
    for (const container of [grid, $('backlogList')]) {
        container.addEventListener('dragstart', (event) => {
            const el = event.target.closest('[data-activity]');
            if (!el) return;
            event.dataTransfer.setData('text/plain', el.dataset.activity);
            event.dataTransfer.effectAllowed = 'move';
        });
    }
    grid.addEventListener('dragover', (event) => {
        if (event.target.closest('[data-day]')) event.preventDefault();
    });
    grid.addEventListener('drop', (event) => {
        const cell = event.target.closest('[data-day]');
        if (!cell) return;
        event.preventDefault();
        const id = Number(event.dataTransfer.getData('text/plain'));
        if (id) dropOnDay(id, cell.dataset.day);
    });

    $('backlogList').addEventListener('click', (event) => {
        const done = event.target.closest('[data-done]');
        if (done) {
            save(Number(done.dataset.done), { status: 'done' });
            return;
        }
        const card = event.target.closest('[data-activity]');
        if (card) openDialog({ activity: state.activities.find((a) => a.id === Number(card.dataset.activity)) });
    });

    $('activityForm').addEventListener('submit', submitDialog);
    $('activityDelete').addEventListener('click', deleteFromDialog);
    $('activityCancel').addEventListener('click', closeDialog);
    $('activityModal').addEventListener('click', (e) => { if (e.target === $('activityModal')) closeDialog(); });

    $('feedOpen').addEventListener('click', showFeed);
    $('feedCopy').addEventListener('click', copyFeed);
    $('feedRotate').addEventListener('click', rotateFeed);
    $('feedClose').addEventListener('click', () => $('feedModal').classList.add('hidden'));
    $('feedModal').addEventListener('click', (e) => { if (e.target === $('feedModal')) $('feedModal').classList.add('hidden'); });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { closeDialog(); $('feedModal').classList.add('hidden'); }
    });
}

