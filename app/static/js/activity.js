// The Activity tab: a month calendar plus a backlog of unscheduled items. Everything is editable.

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

function gridDays(month) {
    const first = new Date(month.getFullYear(), month.getMonth(), 1);
    const offset = (first.getDay() + 6) % 7; // week starts on Monday
    const start = new Date(first.getFullYear(), first.getMonth(), 1 - offset);
    return Array.from({ length: 42 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
}

const isOverdue = (a) => a.status !== 'done' && a.start_at && dayKey(a) < localYmd(new Date());

// --- data ----------------------------------------------------------------------------------------

export async function loadActivities() {
    const days = gridDays(state.month);
    // pad by a day each side so all-day items near the edges are never cut off by time-zone offsets
    const start = new Date(days[0].getFullYear(), days[0].getMonth(), days[0].getDate() - 1);
    const end = new Date(days[41].getFullYear(), days[41].getMonth(), days[41].getDate() + 2);
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

function chip(a) {
    const time = !a.all_day && a.start_at ? `<span class="opacity-70">${timeOf(new Date(a.start_at))}</span> ` : '';
    let style = a.source === 'email'
        ? 'bg-blue-500/15 border-blue-500/30 text-blue-200'
        : 'bg-violet-500/15 border-violet-500/30 text-violet-200';
    if (a.status === 'done') style = 'bg-slate-700/30 border-slate-600/30 text-slate-500 line-through';
    else if (isOverdue(a)) style = 'bg-red-500/15 border-red-500/40 text-red-200';
    return `<div draggable="true" data-activity="${a.id}" class="truncate text-[11px] px-1.5 py-0.5 rounded border cursor-pointer ${style}" title="${esc(a.title)}">${time}${esc(a.title)}</div>`;
}

function renderCalendar() {
    const todayKey = localYmd(new Date());
    const byDay = {};
    state.activities.filter((a) => a.start_at).forEach((a) => { (byDay[dayKey(a)] ||= []).push(a); });

    $('monthLabel').textContent = state.month.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    $('weekdayRow').innerHTML = WEEKDAYS.map((d) => `<div class="text-center text-[11px] font-semibold text-slate-500 py-2">${d}</div>`).join('');

    $('calendarGrid').innerHTML = gridDays(state.month).map((d) => {
        const key = localYmd(d);
        const items = byDay[key] || [];
        const inMonth = d.getMonth() === state.month.getMonth();
        const more = items.length > MAX_CHIPS ? `<div class="text-[10px] text-slate-500 px-1">+${items.length - MAX_CHIPS} more</div>` : '';
        return `
        <div data-day="${key}" class="min-h-0 overflow-hidden border border-slate-800/80 p-1.5 flex flex-col gap-1 cursor-pointer hover:bg-slate-800/40 ${inMonth ? '' : 'opacity-40'} ${key === todayKey ? 'bg-blue-500/5 ring-1 ring-inset ring-blue-500/40' : ''}">
            <span class="text-[11px] font-medium ${key === todayKey ? 'text-blue-400' : 'text-slate-400'}">${d.getDate()}</span>
            ${items.slice(0, MAX_CHIPS).map(chip).join('')}${more}
        </div>`;
    }).join('');
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
    const goToMonth = async (date) => { state.month = new Date(date.getFullYear(), date.getMonth(), 1); await loadActivities(); };
    $('monthPrev').addEventListener('click', () => goToMonth(new Date(state.month.getFullYear(), state.month.getMonth() - 1, 1)));
    $('monthNext').addEventListener('click', () => goToMonth(new Date(state.month.getFullYear(), state.month.getMonth() + 1, 1)));
    $('monthToday').addEventListener('click', () => goToMonth(new Date()));
    $('activityAdd').addEventListener('click', () => openDialog({ date: localYmd(new Date()) }));

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

