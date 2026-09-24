// The To-dos tab: a personal task list grouped by due date (Overdue, Today, Upcoming, Later, No date, Done).
// Tasks are Activity rows (see /api/todos and /api/activities). Items with source 'email' came from an email.
// Everything is rendered into #view-todos; the edit dialog is created on first use and appended to <body>.

import { api } from './api.js';
import { icon } from './icons.js';
import { $, esc, localYmd, pad, toast } from './util.js';

const PRIORITIES = { 1: 'Low', 2: 'Medium', 3: 'High' };
const DOT = { 1: 'bg-slate-400', 2: 'bg-amber-400', 3: 'bg-red-500' };
const GROUPS = [
    ['overdue', 'Overdue'], ['today', 'Today'], ['upcoming', 'Upcoming (next 7 days)'],
    ['later', 'Later'], ['nodate', 'No date'], ['done', 'Done'],
];
const FOCUS = 'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2';

const ui = {
    tasks: [],
    filter: 'all',        // all | open | done
    course: '',           // '' = every course
    doneOpen: false,      // is the Done section expanded?
    quick: { due: '', priority: 2 },
    editing: null,        // task being edited in the dialog
    opener: null,         // element to give focus back to when the dialog closes
};

// --- dates ---------------------------------------------------------------------------------------
// All-day items are stored as midnight UTC of their date, timed items are real instants (local day).

const dayKey = (t) => (!t.start_at ? '' : t.all_day ? t.start_at.slice(0, 10) : localYmd(new Date(t.start_at)));
const fromKey = (k) => { const [y, m, d] = k.split('-').map(Number); return new Date(y, m - 1, d); };
const shiftKey = (n) => { const d = new Date(); return localYmd(new Date(d.getFullYear(), d.getMonth(), d.getDate() + n)); };
const daysFromToday = (k) => Math.round((fromKey(k) - fromKey(localYmd(new Date()))) / 86400000);
const timeLabel = (t) => (t.all_day || !t.start_at ? '' : (d => `${pad(d.getHours())}:${pad(d.getMinutes())}`)(new Date(t.start_at)));
const localInput = (iso) => { const d = new Date(iso); return `${localYmd(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };

function dueLabel(t) {
    const k = dayKey(t);
    if (!k) return '';
    const n = daysFromToday(k);
    const base = n === 0 ? 'Today' : n === 1 ? 'Tomorrow' : n === -1 ? 'Yesterday'
        : fromKey(k).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
    return `${base}${timeLabel(t) ? ` ${timeLabel(t)}` : ''}`;
}

function groupOf(t) {
    if (t.status === 'done') return 'done';
    const k = dayKey(t);
    if (!k) return 'nodate';
    const n = daysFromToday(k);
    return n < 0 ? 'overdue' : n === 0 ? 'today' : n <= 7 ? 'upcoming' : 'later';
}

// Earliest due first, then higher priority, then title.
const byDue = (a, b) => String(a.start_at || '￿').localeCompare(String(b.start_at || '￿'))
    || (b.priority - a.priority) || a.title.localeCompare(b.title);

// The API takes UTC instants: a date alone becomes an all-day item, a date plus time a real moment.
function dueFields(date, time) {
    if (!date) return { start_at: null, all_day: false };
    if (time) return { start_at: new Date(`${date}T${time}`).toISOString(), all_day: false };
    return { start_at: `${date}T00:00:00Z`, all_day: true };
}

// --- data ----------------------------------------------------------------------------------------

export async function loadTodos() {
    const root = $('view-todos');
    if (!root) return;
    if (!$('todoList')) buildShell(root);
    try {
        ui.tasks = await api('/api/todos', { params: { status: 'all', limit: 1000 } });
    } catch (err) {
        toast(err.message, 'error');
    }
    render();
}

const changed = () => document.dispatchEvent(new CustomEvent('taskschanged'));
const courses = () => [...new Set(ui.tasks.map((t) => t.course).filter(Boolean))].sort((a, b) => a.localeCompare(b));

async function toggle(id) {
    const task = ui.tasks.find((t) => t.id === id);
    if (!task) return;
    const previous = task.status;
    task.status = previous === 'done' ? 'todo' : 'done'; // optimistic
    render();
    try {
        Object.assign(task, await api(`/api/activities/${id}`, { method: 'PATCH', body: { status: task.status } }));
        changed();
    } catch (err) {
        task.status = previous; // roll back
        toast(`Could not update "${task.title}": ${err.message}`, 'error');
    }
    render();
}

async function quickAdd(input) {
    const title = input.value.trim();
    if (!title) return;
    const course = $('todoQaCourse').value.trim();
    input.disabled = true;
    try {
        const body = { title, priority: ui.quick.priority, ...dueFields(ui.quick.due, ''), ...(course ? { course } : {}) };
        ui.tasks.push(await api('/api/activities', { method: 'POST', body }));
        input.value = '';
        ui.quick = { due: '', priority: 2 };
        changed();
    } catch (err) {
        toast(err.message, 'error');
    }
    input.disabled = false;
    input.focus();
    render();
}

// --- rendering -----------------------------------------------------------------------------------

function buildShell(root) {
    root.innerHTML = `
        <div class="max-w-3xl mx-auto w-full p-4 sm:p-6 space-y-4">
            <header>
                <h2 class="text-xl sm:text-2xl font-extrabold tracking-tight text-fg">To-dos</h2>
                <p id="todoProgress" class="text-sm text-slate-400 mt-1" aria-live="polite"></p>
            </header>
            <form id="todoQuick" class="glass-card p-3 sm:p-4 space-y-3">
                <div class="flex gap-2">
                    <label for="todoQaTitle" class="sr-only">New task</label>
                    <input id="todoQaTitle" class="field flex-1 min-w-0" maxlength="255" autocomplete="off" placeholder="Add a task and press Enter">
                    <button type="submit" class="shrink-0 min-h-[44px] px-4 rounded-xl text-sm font-semibold text-white cursor-pointer ${FOCUS}" style="background-image: var(--grad)" aria-label="Add task">${icon('plus', 'w-5 h-5')}</button>
                </div>
                <div id="todoQaChips" class="flex flex-wrap items-center gap-2"></div>
            </form>
            <div class="space-y-2">
                <div class="seg" role="group" aria-label="Show tasks" id="todoFilter">
                    <button type="button" data-filter="all" aria-pressed="true">All</button>
                    <button type="button" data-filter="open" aria-pressed="false">Open</button>
                    <button type="button" data-filter="done" aria-pressed="false">Done</button>
                </div>
                <div id="todoCourses" class="flex flex-wrap gap-2" role="group" aria-label="Filter by course"></div>
            </div>
            <div id="todoList" class="space-y-5"></div>
        </div>`;
    root.addEventListener('click', onClick);
    root.addEventListener('input', onInput);
    root.querySelector('#todoQuick').addEventListener('submit', (e) => { e.preventDefault(); quickAdd($('todoQaTitle')); });
}

// Small pill button used for the quick-add options and the course filter.
const pill = (attrs, label, on) => `<button type="button" ${attrs} aria-pressed="${on}" class="chip tone-${on ? 'brand' : 'neutral'} min-h-[36px] cursor-pointer ${FOCUS}">${label}</button>`;

function renderQuick() {
    const q = ui.quick;
    const custom = q.due && q.due !== shiftKey(0) && q.due !== shiftKey(1);
    const prev = $('todoQaCourse');
    const course = prev ? prev.value : '';
    $('todoQaChips').innerHTML = `
        ${pill('data-qdue="today"', 'Today', q.due === shiftKey(0))}
        ${pill('data-qdue="tomorrow"', 'Tomorrow', q.due === shiftKey(1))}
        <label class="chip tone-${custom ? 'brand' : 'neutral'} min-h-[36px] gap-1.5">Date
            <input id="todoQaDate" type="date" value="${custom ? esc(q.due) : ''}" class="bg-transparent text-xs text-fg ${FOCUS}" aria-label="Due date">
        </label>
        <span class="seg" role="group" aria-label="Priority">
            ${[1, 2, 3].map((p) => `<button type="button" data-qprio="${p}" aria-pressed="${q.priority === p}">${PRIORITIES[p]}</button>`).join('')}
        </span>
        <label class="sr-only" for="todoQaCourse">Course</label>
        <input id="todoQaCourse" list="todoCourseList" class="field !w-36 !py-1.5 text-sm" maxlength="120" placeholder="Course" value="${esc(course)}">
        <datalist id="todoCourseList">${courses().map((c) => `<option value="${esc(c)}"></option>`).join('')}</datalist>`;
}

function row(t) {
    const done = t.status === 'done';
    const group = groupOf(t);
    const due = dueLabel(t);
    return `
        <li class="flex items-start gap-2 p-2 sm:p-3 rounded-2xl glass-card" data-id="${t.id}">
            <button type="button" data-toggle="${t.id}" aria-pressed="${done}" aria-label="Mark &quot;${esc(t.title)}&quot; as ${done ? 'not done' : 'done'}"
                class="shrink-0 w-11 h-11 -m-0.5 flex items-center justify-center cursor-pointer ${FOCUS}">
                <span class="w-6 h-6 rounded-full border-2 flex items-center justify-center ${done ? 'border-transparent text-white' : 'border-slate-400'}" ${done ? 'style="background-image: var(--grad)"' : ''}>${done ? icon('check', 'w-4 h-4') : ''}</span>
            </button>
            <div class="min-w-0 flex-1 py-1.5">
                <p class="text-sm font-semibold text-fg break-words ${done ? 'line-through opacity-60' : ''}">${esc(t.title)}</p>
                ${t.notes ? `<p class="text-xs text-slate-400 mt-0.5 line-clamp-2 break-words">${esc(t.notes)}</p>` : ''}
                <div class="flex flex-wrap items-center gap-1.5 mt-1.5 text-xs">
                    ${due ? `<span class="chip ${group === 'overdue' ? 'tone-urgent' : 'tone-neutral'}">${icon('clock', 'w-3 h-3 mr-1')}${esc(due)}</span>` : ''}
                    ${t.course ? `<span class="chip tone-info">${esc(t.course)}</span>` : ''}
                    <span class="inline-flex items-center gap-1 text-slate-400"><span class="w-2.5 h-2.5 rounded-full ${DOT[t.priority] || DOT[2]}" aria-hidden="true"></span>${esc(PRIORITIES[t.priority] || 'Medium')}</span>
                    ${t.source === 'email' ? `<span class="inline-flex items-center gap-1 text-slate-400" title="Found in an email">${icon('mail', 'w-3.5 h-3.5')}from email</span>` : ''}
                </div>
            </div>
            <button type="button" data-edit="${t.id}" class="icon-btn shrink-0 ${FOCUS}" aria-label="Edit &quot;${esc(t.title)}&quot;">${icon('edit', 'w-4 h-4')}</button>
            <button type="button" data-delete="${t.id}" class="icon-btn shrink-0 ${FOCUS}" aria-label="Delete &quot;${esc(t.title)}&quot;">${icon('trash', 'w-4 h-4')}</button>
        </li>`;
}

function render() {
    if (!$('todoList')) return;
    const all = ui.tasks;

    // progress: tasks due today
    const today = all.filter((t) => dayKey(t) === shiftKey(0));
    const open = all.filter((t) => t.status !== 'done').length;
    $('todoProgress').textContent = today.length
        ? `${today.filter((t) => t.status === 'done').length} of ${today.length} done today`
        : `${open} open task${open === 1 ? '' : 's'}`;

    // filters
    document.querySelectorAll('#todoFilter [data-filter]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.filter === ui.filter)));
    const list = courses();
    if (ui.course && !list.includes(ui.course)) ui.course = '';
    $('todoCourses').innerHTML = list.length
        ? pill('data-course=""', 'All courses', !ui.course) + list.map((c) => pill(`data-course="${esc(c)}"`, esc(c), ui.course === c)).join('')
        : '';
    renderQuick();

    // list
    const shown = all
        .filter((t) => (ui.filter === 'open' ? t.status !== 'done' : ui.filter === 'done' ? t.status === 'done' : true))
        .filter((t) => !ui.course || t.course === ui.course)
        .sort(byDue);
    if (!shown.length) {
        $('todoList').innerHTML = `<div class="glass-card p-8 text-center text-slate-400 text-sm">${
            all.length ? 'Nothing matches these filters.' : 'Nothing to do yet. Add your first task above.'}</div>`;
        return;
    }
    $('todoList').innerHTML = GROUPS.map(([key, label]) => {
        const items = shown.filter((t) => groupOf(t) === key);
        if (!items.length) return '';
        // Done is collapsed unless the Done filter is chosen or the person opened it
        const collapsible = key === 'done' && ui.filter !== 'done';
        const expanded = !collapsible || ui.doneOpen;
        return `
            <section aria-label="${label}">
                ${collapsible
        ? `<button type="button" data-donetoggle aria-expanded="${expanded}" class="flex items-center gap-1.5 min-h-[44px] text-[0.68rem] font-bold uppercase tracking-wider text-slate-400 cursor-pointer ${FOCUS}">${icon(expanded ? 'chevronDown' : 'chevronRight', 'w-4 h-4')}${label} (${items.length})</button>`
        : `<h3 class="text-[0.68rem] font-bold uppercase tracking-wider mb-2 ${key === 'overdue' ? 'text-red-400' : 'text-slate-400'}">${label} (${items.length})</h3>`}
                ${expanded ? `<ul class="space-y-2">${items.map(row).join('')}</ul>` : ''}
            </section>`;
    }).join('');
}

// --- events --------------------------------------------------------------------------------------

function onClick(e) {
    const hit = (sel) => e.target.closest(sel);
    let el;
    if ((el = hit('[data-toggle]'))) toggle(Number(el.dataset.toggle));
    else if ((el = hit('[data-edit]'))) openDialog(Number(el.dataset.edit), el);
    else if ((el = hit('[data-delete]'))) openDialog(Number(el.dataset.delete), el, true);
    else if ((el = hit('[data-filter]'))) { ui.filter = el.dataset.filter; render(); }
    else if ((el = hit('[data-course]'))) { ui.course = el.dataset.course; render(); }
    else if (hit('[data-donetoggle]')) { ui.doneOpen = !ui.doneOpen; render(); }
    else if ((el = hit('[data-qdue]'))) {
        const key = shiftKey(el.dataset.qdue === 'today' ? 0 : 1);
        ui.quick.due = ui.quick.due === key ? '' : key;
        renderQuick();
    } else if ((el = hit('[data-qprio]'))) { ui.quick.priority = Number(el.dataset.qprio); renderQuick(); }
}

function onInput(e) {
    if (e.target.id === 'todoQaDate') { ui.quick.due = e.target.value; renderQuick(); $('todoQaDate').focus(); }
}

// --- edit dialog ---------------------------------------------------------------------------------

function buildDialog() {
    const modal = document.createElement('div');
    modal.id = 'todoModal';
    modal.className = 'hidden fixed inset-0 bg-black/60 backdrop-blur-sm z-[70] items-center justify-center p-4';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'todoModalTitle');
    modal.innerHTML = `
        <form id="todoForm" class="glass-card modal-card w-full max-w-lg max-h-[92dvh] overflow-y-auto p-5 sm:p-6 space-y-4" novalidate>
            <div class="flex items-start justify-between gap-3">
                <h2 id="todoModalTitle" class="text-xl font-extrabold tracking-tight text-fg">Edit task</h2>
                <button type="button" id="todoModalClose" class="icon-btn -mt-1 -mr-2 ${FOCUS}" aria-label="Close">${icon('x', 'w-5 h-5')}</button>
            </div>
            <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Title</span>
                <input id="todoFTitle" class="field" maxlength="255" required></label>
            <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Notes</span>
                <textarea id="todoFNotes" class="field" rows="3" maxlength="5000"></textarea></label>
            <div class="grid grid-cols-2 gap-3">
                <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Due date</span>
                    <input id="todoFDate" type="date" class="field"></label>
                <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Time (optional)</span>
                    <input id="todoFTime" type="time" class="field"></label>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Course</span>
                    <input id="todoFCourse" list="todoCourseList" class="field" maxlength="120"></label>
                <label class="block space-y-1"><span class="text-xs font-semibold text-slate-400">Reminder</span>
                    <input id="todoFRemind" type="datetime-local" class="field"></label>
            </div>
            <div class="space-y-1"><span class="text-xs font-semibold text-slate-400">Priority</span>
                <div class="seg w-full" role="group" aria-label="Priority">
                    ${[1, 2, 3].map((p) => `<button type="button" data-fprio="${p}" aria-pressed="false">${PRIORITIES[p]}</button>`).join('')}
                </div></div>
            <p id="todoFError" class="hidden text-sm text-red-400" role="alert"></p>
            <div id="todoFActions" class="flex flex-wrap items-center gap-2 justify-between">
                <button type="button" id="todoFDelete" class="min-h-[44px] px-3 text-sm font-semibold text-red-400 cursor-pointer ${FOCUS}">Delete</button>
                <div class="flex gap-2">
                    <button type="button" id="todoFCancel" class="min-h-[44px] px-4 rounded-xl text-sm font-semibold text-slate-300 cursor-pointer ${FOCUS}">Cancel</button>
                    <button type="submit" id="todoFSave" class="min-h-[44px] px-5 rounded-xl text-sm font-semibold text-white disabled:opacity-50 cursor-pointer ${FOCUS}" style="background-image: var(--grad)">Save</button>
                </div>
            </div>
            <div id="todoFConfirm" class="hidden flex-wrap items-center gap-2 justify-between">
                <p class="text-sm text-fg">Delete this task for good?</p>
                <div class="flex gap-2">
                    <button type="button" id="todoFKeep" class="min-h-[44px] px-4 rounded-xl text-sm font-semibold text-slate-300 cursor-pointer ${FOCUS}">Keep it</button>
                    <button type="button" id="todoFReallyDelete" class="min-h-[44px] px-4 rounded-xl text-sm font-semibold text-white bg-red-600 hover:bg-red-500 cursor-pointer ${FOCUS}">Yes, delete</button>
                </div>
            </div>
        </form>`;
    document.body.appendChild(modal);

    modal.addEventListener('mousedown', (e) => { if (e.target === modal) closeDialog(); }); // backdrop
    modal.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { e.stopPropagation(); closeDialog(); return; }
        if (e.key !== 'Tab') return; // keep focus inside the dialog
        const items = [...modal.querySelectorAll('button, input, textarea')].filter((x) => x.offsetParent !== null || x === document.activeElement);
        if (!items.length) return;
        const [first, last] = [items[0], items[items.length - 1]];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    modal.addEventListener('click', (e) => {
        const p = e.target.closest('[data-fprio]');
        if (p) setPriority(Number(p.dataset.fprio));
    });
    $('todoModalClose').addEventListener('click', closeDialog);
    $('todoFCancel').addEventListener('click', closeDialog);
    $('todoFDelete').addEventListener('click', () => showConfirm(true));
    $('todoFKeep').addEventListener('click', () => showConfirm(false));
    $('todoFReallyDelete').addEventListener('click', removeTask);
    $('todoForm').addEventListener('submit', (e) => { e.preventDefault(); saveTask(); });
}

let formPriority = 2;
function setPriority(p) {
    formPriority = p;
    document.querySelectorAll('#todoModal [data-fprio]').forEach((b) => b.setAttribute('aria-pressed', String(Number(b.dataset.fprio) === p)));
}

function showConfirm(on) {
    $('todoFActions').classList.toggle('hidden', on);
    $('todoFConfirm').classList.toggle('hidden', !on);
    $('todoFConfirm').classList.toggle('flex', on);
    ($(on ? 'todoFKeep' : 'todoFDelete')).focus();
}

function formError(message) {
    $('todoFError').textContent = message || '';
    $('todoFError').classList.toggle('hidden', !message);
}

function openDialog(id, opener, confirmDelete = false) {
    const task = ui.tasks.find((t) => t.id === id);
    if (!task) return;
    if (!$('todoModal')) buildDialog();
    ui.editing = task;
    ui.opener = opener || document.activeElement;
    $('todoFTitle').value = task.title;
    $('todoFNotes').value = task.notes || '';
    $('todoFDate').value = dayKey(task);
    $('todoFTime').value = timeLabel(task);
    $('todoFCourse').value = task.course || '';
    $('todoFRemind').value = task.remind_at ? localInput(task.remind_at) : '';
    setPriority(task.priority || 2);
    formError('');
    const modal = $('todoModal');
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    showConfirm(false);
    if (confirmDelete) showConfirm(true); else $('todoFTitle').focus();
}

function closeDialog() {
    const modal = $('todoModal');
    if (!modal) return;
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    ui.editing = null;
    if (ui.opener && ui.opener.isConnected) ui.opener.focus();
}

async function saveTask() {
    const task = ui.editing;
    if (!task) return;
    const date = $('todoFDate').value;
    const time = date ? $('todoFTime').value : '';
    const remind = $('todoFRemind').value;
    const course = $('todoFCourse').value.trim();
    const body = {
        title: $('todoFTitle').value.trim(),
        notes: $('todoFNotes').value.trim() || null,
        priority: formPriority,
        course: course || null,
        remind_at: remind ? new Date(remind).toISOString() : null,
    };
    // only touch the due date when it changed, so an email item's own end time survives a plain edit
    if (date !== dayKey(task) || time !== timeLabel(task)) Object.assign(body, dueFields(date, time), { end_at: null });
    if (!body.title) { formError('Give the task a title.'); return; }

    $('todoFSave').disabled = true;
    formError('');
    try {
        Object.assign(task, await api(`/api/activities/${task.id}`, { method: 'PATCH', body }));
        closeDialog();
        changed();
        render();
    } catch (err) {
        formError(err.message); // e.g. validation errors from the server, shown inline
    }
    $('todoFSave').disabled = false;
}

async function removeTask() {
    const task = ui.editing;
    if (!task) return;
    try {
        await api(`/api/activities/${task.id}`, { method: 'DELETE' });
        ui.tasks = ui.tasks.filter((t) => t.id !== task.id);
        closeDialog();
        changed();
        render();
    } catch (err) {
        showConfirm(false);
        formError(err.message);
    }
}

// --- startup -------------------------------------------------------------------------------------

// Called once at startup. Builds the page skeleton early; loadTodos() fills it whenever the tab opens.
export function initTodos() {
    const root = $('view-todos');
    if (root && !$('todoList')) buildShell(root);
    render();
}
