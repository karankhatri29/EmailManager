// The Timetable tab: a weekly grid of classes (Mon-Sun) with a "now" line, plus an add / edit dialog.
// Desktop and tablet show one column per day; phones (<640px) show one day at a time with day tabs.
// Everything is rendered from JS into #view-timetable; the dialog is created on first use.

import { api } from './api.js';
import { icon } from './icons.js';
import { $, esc, pad, toast } from './util.js';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const HOUR_PX = 56; // height of one hour in the grid
const DEFAULT_START = 8;
const DEFAULT_END = 18;
// Same palette the server uses to pick a default colour.
const PALETTE = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#6366f1', '#84cc16'];

const phone = window.matchMedia('(max-width: 639px)');

const view = {
    slots: [],
    loaded: false,
    showWeekend: false,
    day: (new Date().getDay() + 6) % 7, // selected day on phones, 0 = Monday
    feedUrl: '',
    feedOpen: false,
};

// --- helpers -------------------------------------------------------------------------------------

const toMin = (hhmm) => { const [h, m] = String(hhmm).split(':').map(Number); return h * 60 + m; };
const fromMin = (min) => `${pad(Math.floor(min / 60))}:${pad(min % 60)}`;
const todayIndex = () => (new Date().getDay() + 6) % 7;

// The hour range shown: 08:00-18:00 unless a class falls outside it.
function hourRange() {
    let first = DEFAULT_START;
    let last = DEFAULT_END;
    for (const s of view.slots) {
        first = Math.min(first, Math.floor(toMin(s.start_time) / 60));
        last = Math.max(last, Math.ceil(toMin(s.end_time) / 60));
    }
    return [first, last];
}

// Weekend columns appear only when a class is there or the user asked for them.
function visibleDays() {
    if (phone.matches) return [view.day];
    const weekend = view.showWeekend || view.slots.some((s) => s.weekday >= 5);
    return weekend ? [0, 1, 2, 3, 4, 5, 6] : [0, 1, 2, 3, 4];
}

// Side-by-side lanes for slots that overlap (possible when their terms differ).
function withLanes(slots) {
    const sorted = [...slots].sort((a, b) => toMin(a.start_time) - toMin(b.start_time));
    const laneEnds = [];
    const placed = sorted.map((slot) => {
        let lane = laneEnds.findIndex((end) => end <= toMin(slot.start_time));
        if (lane < 0) lane = laneEnds.length;
        laneEnds[lane] = toMin(slot.end_time);
        return { slot, lane };
    });
    return placed.map((p) => ({ ...p, lanes: laneEnds.length }));
}

// --- rendering -----------------------------------------------------------------------------------

function blockHtml({ slot, lane, lanes }, firstHour) {
    const top = ((toMin(slot.start_time) - firstHour * 60) / 60) * HOUR_PX;
    const height = Math.max(((toMin(slot.end_time) - toMin(slot.start_time)) / 60) * HOUR_PX, 22);
    const color = /^#[0-9a-fA-F]{6}$/.test(slot.color) ? slot.color : '#3b82f6';
    const label = `${slot.title}${slot.code ? `, ${slot.code}` : ''}, ${DAY_NAMES[slot.weekday]} ${slot.start_time} to ${slot.end_time}${slot.room ? `, room ${slot.room}` : ''}. Edit`;
    const width = 100 / lanes;
    return `
        <button type="button" data-slot="${slot.id}" aria-label="${esc(label)}"
            class="absolute text-left overflow-hidden rounded-lg px-2 py-1 text-fg text-[11px] leading-tight cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 hover:brightness-110"
            style="top:${top}px;height:${height - 2}px;left:calc(${lane * width}% + 2px);width:calc(${width}% - 4px);background:${color}30;border-left:3px solid ${color};outline-color:${color}">
            <span class="block font-bold truncate">${esc(slot.title)}</span>
            ${slot.code ? `<span class="block truncate opacity-80">${esc(slot.code)}</span>` : ''}
            ${slot.room ? `<span class="block truncate opacity-80">${esc(slot.room)}</span>` : ''}
            <span class="block truncate opacity-70">${esc(slot.start_time)}-${esc(slot.end_time)}</span>
        </button>`;
}

function gridHtml() {
    const [first, last] = hourRange();
    const days = visibleDays();
    const height = (last - first) * HOUR_PX;
    const today = todayIndex();
    const hours = Array.from({ length: last - first }, (_, i) => first + i);

    const axis = hours.map((h) => `<div class="absolute right-2 text-[10px] text-slate-500 -translate-y-1/2" style="top:${(h - first) * HOUR_PX}px">${pad(h)}:00</div>`).join('');
    const heads = days.map((d) => `<div class="text-center text-xs font-bold py-2 ${d === today ? 'text-blue-400' : 'text-slate-400'}">${phone.matches ? DAY_NAMES[d] : DAYS[d]}</div>`).join('');
    const lines = hours.map((h) => `<div class="absolute inset-x-0 border-t border-slate-700/40" style="top:${(h - first) * HOUR_PX}px"></div>`).join('');
    const cols = days.map((d) => {
        const blocks = withLanes(view.slots.filter((s) => s.weekday === d)).map((p) => blockHtml(p, first)).join('');
        return `
            <div data-col="${d}" class="relative border-l border-slate-700/40 cursor-cell ${d === today ? 'bg-blue-500/5' : ''}" style="height:${height}px">
                ${lines}${blocks}
                ${d === today ? '<div data-now class="absolute inset-x-0 h-0.5 bg-red-500 pointer-events-none z-10 hidden"></div>' : ''}
            </div>`;
    }).join('');

    const template = `grid-template-columns:3.25rem repeat(${days.length},minmax(0,1fr))`;
    return `
        <div class="glass-card p-2 sm:p-3 overflow-hidden" data-grid data-first="${first}">
            <div class="grid" style="${template}"><div></div>${heads}</div>
            <div class="grid overflow-y-auto" style="${template};max-height:70dvh">
                <div class="relative" style="height:${height}px">${axis}</div>${cols}
            </div>
        </div>`;
}

function dayTabsHtml() {
    const today = todayIndex();
    const tabs = DAYS.map((name, d) => {
        const count = view.slots.filter((s) => s.weekday === d).length;
        return `<button type="button" role="tab" data-day="${d}" aria-selected="${d === view.day}" aria-pressed="${d === view.day}" aria-label="${DAY_NAMES[d]}${d === today ? ', today' : ''}, ${count} classes">${name}${count ? ` <span class="opacity-70">${count}</span>` : ''}</button>`;
    }).join('');
    return `<div class="seg w-full mb-3 overflow-x-auto" role="tablist" aria-label="Day of the week">${tabs}</div>`;
}

function feedHtml() {
    if (!view.feedOpen) return '';
    return `
        <div class="glass-card p-4 mb-4 space-y-2">
            <p class="text-sm font-semibold text-fg">Add to your calendar app</p>
            <p class="text-xs text-slate-400">Subscribe to this link in Google Calendar, Apple Calendar or Outlook. Your classes repeat weekly.</p>
            <div class="flex gap-2">
                <input id="ttFeedUrl" class="field" readonly value="${esc(view.feedUrl)}" aria-label="Calendar feed link">
                <button type="button" data-act="copy-feed" class="bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium px-4 rounded-lg cursor-pointer">Copy</button>
            </div>
        </div>`;
}

function render() {
    const root = $('view-timetable');
    if (!root) return;
    const weekendShown = view.showWeekend || view.slots.some((s) => s.weekday >= 5);
    const empty = view.loaded && view.slots.length === 0;
    root.innerHTML = `
        <div class="p-4 sm:p-6 max-w-6xl mx-auto">
            <div class="flex flex-wrap items-center gap-2 mb-4">
                <span class="flex items-center gap-2 text-xl font-extrabold tracking-tight text-fg">${icon('timetable', 'w-5 h-5')} Timetable</span>
                <div class="ml-auto flex flex-wrap items-center gap-2">
                    ${phone.matches ? '' : `<button type="button" data-act="weekend" aria-pressed="${weekendShown}" ${view.slots.some((s) => s.weekday >= 5) ? 'disabled' : ''} class="text-sm text-slate-400 hover:text-fg px-3 py-2 cursor-pointer">${weekendShown ? 'Hide' : 'Show'} weekend</button>`}
                    <button type="button" data-act="feed" aria-expanded="${view.feedOpen}" class="flex items-center gap-1.5 text-sm text-slate-400 hover:text-fg px-3 py-2 cursor-pointer">${icon('link')} Add to your calendar app</button>
                    <button type="button" data-act="add" class="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium px-4 py-2 rounded-lg cursor-pointer">${icon('plus')} Add class</button>
                </div>
            </div>
            ${feedHtml()}
            ${empty ? `<div class="glass-card p-6 text-center mb-4"><p class="text-sm font-bold text-fg">No classes yet</p><p class="text-xs text-slate-400 mt-1">Add your first class, or click an empty spot in the grid.</p></div>` : ''}
            ${phone.matches ? dayTabsHtml() : ''}
            ${view.loaded ? gridHtml() : '<p class="text-sm text-slate-400" role="status">Loading your timetable...</p>'}
        </div>`;
    updateNow();
}

// Positions the red "now" line in today's column; hidden outside the shown hours.
function updateNow() {
    const line = document.querySelector('#view-timetable [data-now]');
    const grid = document.querySelector('#view-timetable [data-grid]');
    if (!line || !grid) return;
    const now = new Date();
    const first = Number(grid.dataset.first);
    const y = ((now.getHours() * 60 + now.getMinutes() - first * 60) / 60) * HOUR_PX;
    const [, last] = hourRange();
    line.style.top = `${y}px`;
    line.classList.toggle('hidden', y < 0 || y > (last - first) * HOUR_PX);
}

// --- data ----------------------------------------------------------------------------------------

export async function loadTimetable() {
    try {
        view.slots = await api('/api/timetable');
        view.loaded = true;
    } catch (err) {
        toast(err.message, 'error');
    }
    render();
}

// --- add / edit dialog ---------------------------------------------------------------------------

const dlg = { el: null, slot: null, days: new Set(), color: null, opener: null, confirming: false };

function buildDialog() {
    const el = document.createElement('div');
    el.id = 'timetableModal';
    el.className = 'hidden fixed inset-0 bg-black/60 backdrop-blur-sm z-[70] items-center justify-center p-4';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-labelledby', 'ttTitle');
    el.innerHTML = `
        <form id="ttForm" class="glass-card modal-card w-full max-w-md p-4 sm:p-6 space-y-3 max-h-[92dvh] overflow-y-auto" novalidate>
            <h2 id="ttTitle" class="text-lg font-extrabold tracking-tight text-fg"></h2>
            <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Title</span>
                <input name="title" required maxlength="120" placeholder="e.g. Linear Algebra" class="field"></label>
            <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Course code (optional)</span>
                <input name="code" maxlength="32" placeholder="e.g. MATH201" class="field"></label>
            <div class="space-y-1"><span id="ttDaysLabel" class="text-xs font-semibold text-slate-400"></span>
                <div id="ttDays" class="seg w-full overflow-x-auto" role="group" aria-labelledby="ttDaysLabel"></div></div>
            <div class="grid grid-cols-2 gap-2">
                <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Starts</span>
                    <input name="start" type="time" required class="field"></label>
                <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Ends</span>
                    <input name="end" type="time" required class="field"></label>
            </div>
            <div class="grid grid-cols-2 gap-2">
                <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Room</span>
                    <input name="room" maxlength="80" class="field"></label>
                <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Instructor</span>
                    <input name="instructor" maxlength="120" class="field"></label>
            </div>
            <div class="space-y-1"><span id="ttColorLabel" class="text-xs font-semibold text-slate-400">Colour</span>
                <div id="ttColors" class="flex flex-wrap items-center gap-2" role="group" aria-labelledby="ttColorLabel"></div></div>
            <div class="grid grid-cols-2 gap-2">
                <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Term starts (optional)</span>
                    <input name="term_start" type="date" class="field"></label>
                <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Term ends (optional)</span>
                    <input name="term_end" type="date" class="field"></label>
            </div>
            <label id="ttCourseRow" class="hidden items-center gap-2 text-xs text-slate-400 cursor-pointer">
                <input name="apply_to_course" type="checkbox"> Apply title, code, colour, instructor and term to every meeting of this course</label>
            <p id="ttError" role="alert" class="hidden text-sm text-red-400"></p>
            <div id="ttActions" class="flex items-center gap-2 pt-1"></div>
        </form>`;
    document.body.appendChild(el);

    const form = el.querySelector('#ttForm');
    form.addEventListener('submit', submitDialog);
    el.addEventListener('click', (e) => { if (e.target === el) closeDialog(); });

    // Day chips: several at once when adding, exactly one when editing.
    el.querySelector('#ttDays').addEventListener('click', (e) => {
        const b = e.target.closest('[data-wd]');
        if (!b) return;
        const wd = Number(b.dataset.wd);
        if (dlg.slot) dlg.days = new Set([wd]);
        else if (dlg.days.has(wd)) dlg.days.delete(wd);
        else dlg.days.add(wd);
        renderDays();
    });
    el.querySelector('#ttColors').addEventListener('click', (e) => {
        const b = e.target.closest('[data-color]');
        if (b) { dlg.color = b.dataset.color; renderColors(); }
    });
    el.querySelector('#ttColors').addEventListener('input', (e) => {
        if (e.target.name === 'custom_color') { dlg.color = e.target.value; renderColors(); }
    });
    el.querySelector('#ttActions').addEventListener('click', (e) => {
        const act = e.target.closest('[data-tt]');
        if (!act) return;
        if (act.dataset.tt === 'cancel') closeDialog();
        if (act.dataset.tt === 'delete') { dlg.confirming = true; renderActions(); }
        if (act.dataset.tt === 'keep') { dlg.confirming = false; renderActions(); }
        if (act.dataset.tt === 'confirm-delete') deleteSlot();
    });

    // Escape closes; Tab stays inside the dialog.
    el.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { e.stopPropagation(); closeDialog(); return; }
        if (e.key !== 'Tab') return;
        const items = [...el.querySelectorAll('button, input, select')].filter((n) => !n.disabled && n.offsetParent !== null);
        if (!items.length) return;
        const [head, tail] = [items[0], items[items.length - 1]];
        if (e.shiftKey && document.activeElement === head) { e.preventDefault(); tail.focus(); }
        else if (!e.shiftKey && document.activeElement === tail) { e.preventDefault(); head.focus(); }
    });
    return el;
}

function renderDays() {
    $('ttDays').innerHTML = DAYS.map((name, d) => `<button type="button" data-wd="${d}" aria-pressed="${dlg.days.has(d)}" aria-label="${DAY_NAMES[d]}">${name}</button>`).join('');
}

function renderColors() {
    const custom = dlg.color && !PALETTE.includes(dlg.color) ? dlg.color : '#64748b';
    $('ttColors').innerHTML = PALETTE.map((c) => `
        <button type="button" data-color="${c}" aria-pressed="${dlg.color === c}" aria-label="Colour ${c}"
            class="w-7 h-7 rounded-full cursor-pointer border-2 ${dlg.color === c ? 'border-white ring-2 ring-offset-0' : 'border-transparent'}" style="background:${c};--tw-ring-color:${c}"></button>`).join('')
        + `<label class="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer">Custom
            <input type="color" name="custom_color" value="${custom}" class="w-7 h-7 rounded cursor-pointer bg-transparent" aria-label="Custom colour"></label>`
        + (dlg.color ? '' : '<span class="text-xs text-slate-500">Automatic</span>');
}

function renderActions() {
    const editing = Boolean(dlg.slot);
    $('ttActions').innerHTML = dlg.confirming
        ? `<span class="text-sm text-fg">Delete this class?</span>
           <button type="button" data-tt="keep" class="ml-auto text-sm text-slate-400 hover:text-fg px-3 py-2 cursor-pointer">Keep it</button>
           <button type="button" data-tt="confirm-delete" class="text-sm font-medium text-white bg-red-600 hover:bg-red-500 px-4 py-2 rounded-lg cursor-pointer">Delete</button>`
        : `${editing ? `<button type="button" data-tt="delete" class="flex items-center gap-1 text-sm text-red-400 hover:text-red-300 cursor-pointer">${icon('trash')} Delete</button>` : ''}
           <button type="button" data-tt="cancel" class="ml-auto text-sm text-slate-400 hover:text-fg px-3 py-2 cursor-pointer">Cancel</button>
           <button type="submit" class="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm font-medium px-5 py-2 rounded-lg cursor-pointer">Save</button>`;
}

function showError(message) {
    const p = $('ttError');
    p.textContent = message || '';
    p.classList.toggle('hidden', !message);
}

// `slot` edits an existing class; otherwise `prefill` ({weekday, start}) seeds a new one.
function openDialog({ slot = null, prefill = {} } = {}) {
    dlg.el = dlg.el || buildDialog();
    dlg.slot = slot;
    dlg.opener = document.activeElement;
    dlg.confirming = false;
    const f = $('ttForm');
    f.reset();

    const weekday = slot ? slot.weekday : (prefill.weekday ?? Math.min(todayIndex(), 6));
    dlg.days = new Set([weekday]);
    dlg.color = slot ? slot.color : null;
    $('ttTitle').textContent = slot ? 'Edit class' : 'Add class';
    $('ttDaysLabel').textContent = slot ? 'Day' : 'Days it meets';
    $('ttCourseRow').classList.toggle('hidden', !slot);
    $('ttCourseRow').classList.toggle('flex', Boolean(slot));

    const start = slot ? slot.start_time : (prefill.start || '09:00');
    const end = slot ? slot.end_time : fromMin(Math.min(toMin(start) + 60, 23 * 60 + 59));
    const values = {
        title: slot ? slot.title : '', code: slot?.code || '', start, end,
        room: slot?.room || '', instructor: slot?.instructor || '',
        term_start: slot?.term_start || '', term_end: slot?.term_end || '',
    };
    for (const [name, value] of Object.entries(values)) f.elements[name].value = value;

    showError('');
    renderDays();
    renderColors();
    renderActions();
    dlg.el.classList.remove('hidden');
    dlg.el.classList.add('flex');
    f.elements.title.focus();
}

function closeDialog() {
    if (!dlg.el || dlg.el.classList.contains('hidden')) return;
    dlg.el.classList.add('hidden');
    dlg.el.classList.remove('flex');
    if (dlg.opener && dlg.opener.isConnected && dlg.opener.focus) dlg.opener.focus();
    dlg.opener = null;
}

async function submitDialog(event) {
    event.preventDefault();
    const f = $('ttForm');
    const text = (name) => f.elements[name].value.trim() || null;
    const title = f.elements.title.value.trim();
    if (!title) return showError('Give the class a title.');
    if (!f.elements.start.value || !f.elements.end.value) return showError('Choose a start and end time.');
    if (!dlg.days.size) return showError('Choose at least one day.');

    const fields = {
        title, code: text('code'), start_time: f.elements.start.value, end_time: f.elements.end.value,
        room: text('room'), instructor: text('instructor'),
        term_start: text('term_start'), term_end: text('term_end'),
    };
    const submit = f.querySelector('[type="submit"]');
    submit.disabled = true;
    showError('');
    try {
        if (dlg.slot) {
            await api(`/api/timetable/${dlg.slot.id}`, {
                method: 'PATCH',
                body: { ...fields, weekday: [...dlg.days][0], color: dlg.color || dlg.slot.color, apply_to_course: f.elements.apply_to_course.checked },
            });
        } else {
            const body = { ...fields, weekdays: [...dlg.days].sort() };
            if (dlg.color) body.color = dlg.color;
            await api('/api/timetable', { method: 'POST', body });
        }
        closeDialog();
        await loadTimetable();
    } catch (err) {
        showError(err.message); // e.g. "That time overlaps with ..."
    } finally {
        submit.disabled = false;
    }
}

async function deleteSlot() {
    try {
        await api(`/api/timetable/${dlg.slot.id}`, { method: 'DELETE' });
        closeDialog();
        await loadTimetable();
    } catch (err) {
        dlg.confirming = false;
        renderActions();
        showError(err.message);
    }
}

// --- calendar feed (ICS) -------------------------------------------------------------------------

async function toggleFeed() {
    if (view.feedOpen) { view.feedOpen = false; render(); return; }
    try {
        if (!view.feedUrl) view.feedUrl = (await api('/api/calendar/feed')).url;
        view.feedOpen = true;
        render();
        const input = $('ttFeedUrl');
        if (input) input.select();
    } catch (err) {
        toast(err.message, 'error');
    }
}

async function copyFeed() {
    const input = $('ttFeedUrl');
    if (!input) return;
    input.select();
    try {
        await navigator.clipboard.writeText(input.value);
        toast('Link copied', 'success', 2500);
    } catch {
        toast('Press Ctrl+C to copy the selected link', 'info');
    }
}

// --- wiring --------------------------------------------------------------------------------------

export function initTimetable() {
    const root = $('view-timetable');
    if (!root) return;

    root.addEventListener('click', (event) => {
        const block = event.target.closest('[data-slot]');
        if (block) {
            openDialog({ slot: view.slots.find((s) => s.id === Number(block.dataset.slot)) });
            return;
        }
        const tab = event.target.closest('[data-day]');
        if (tab) { view.day = Number(tab.dataset.day); render(); return; }

        const act = event.target.closest('[data-act]');
        if (act) {
            if (act.dataset.act === 'add') openDialog({ prefill: { weekday: phone.matches ? view.day : undefined } });
            if (act.dataset.act === 'weekend') { view.showWeekend = !view.showWeekend; render(); }
            if (act.dataset.act === 'feed') toggleFeed();
            if (act.dataset.act === 'copy-feed') copyFeed();
            return;
        }

        // An empty spot in a day column: pre-fill that weekday and the hour that was clicked.
        const col = event.target.closest('[data-col]');
        const grid = event.target.closest('[data-grid]');
        if (col && grid) {
            const hour = Number(grid.dataset.first) + Math.floor((event.clientY - col.getBoundingClientRect().top) / HOUR_PX);
            openDialog({ prefill: { weekday: Number(col.dataset.col), start: fromMin(Math.min(Math.max(hour, 0), 22) * 60) } });
        }
    });

    // Switch between the phone day view and the week grid when the screen crosses 640px.
    phone.addEventListener('change', () => { if (view.loaded) render(); });

    // Keep the "now" line moving while the tab is on screen.
    setInterval(() => { if (root.offsetParent !== null) updateNow(); }, 60000);
}
