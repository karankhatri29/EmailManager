import { api } from './api.js';
import { state } from './state.js';
import { providerLabel } from './accounts.js';
import { renderExplorer } from './explorer.js';
import { loadDashActivities, renderAll, widgetOn } from './kpis.js';
import { bindMailTools, mailToolsHtml } from './mailtools.js';
import { $, esc, senderName, toast } from './util.js';

const URGENT = 'Urgent / Action Required';

// Query parameters shared by every inbox request: the timeframe, and the mailbox filter if one is set.
const inboxParams = (extra = {}) => ({
    time_filter: state.timeframe,
    ...(state.accountFilter !== null ? { account_id: state.accountFilter } : {}),
    ...extra,
});

// --- data ----------------------------------------------------------------------------------------

// Reads stored emails (instant). refresh asks the server to start a background sync of every mailbox.
// silent = background poll: skip the re-render unless something actually changed.
export async function loadEmails({ refresh = false, silent = false } = {}) {
    const emails = await api('/api/emails', { params: inboxParams({ refresh }) });
    const changed = JSON.stringify(emails) !== JSON.stringify(state.emails);
    if (changed || !silent) {
        state.emails = emails;
        await renderDashboard();
    }
    return changed;
}

// --- rendering -----------------------------------------------------------------------------------

// "karan.singh@x.com" -> "Karan"
function firstName() {
    const local = String((state.user && state.user.email) || '').split('@')[0];
    const word = local.split(/[._+-]/)[0].replace(/\d+/g, '');
    return word ? word.charAt(0).toUpperCase() + word.slice(1) : '';
}

// The five-second answer: what is on fire today?
function renderHero(m) {
    const name = firstName();
    $('heroTitle').textContent = name ? `Hey ${name}` : 'Hey there';

    const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
    const parts = [];
    if (m.tasks && m.overdue.length) parts.push(`${m.overdue.length} overdue`);
    if (m.tasks && m.dueToday.length) parts.push(`${m.dueToday.length} due today`);
    if (m.urgent) parts.push(plural(m.urgent, 'urgent email', 'urgent emails'));

    let line;
    if (!state.accounts.length) line = 'Connect a mailbox and I will sort it out for you.';
    else if (parts.length) line = `You have ${parts.join(', ')}.`;
    else if (m.total === 0) line = 'Nothing new in this window. Inbox zen.';
    else line = 'All clear. Nothing urgent right now.';
    $('heroSub').textContent = line;
}

export async function renderDashboard() {
    await renderTasks(); // also stores state.tasks, which the "Do this first" card can use
    await loadDashActivities();
    renderHome();
}

// Everything on the home screen that is computed locally (used again when Settings switches change).
function renderHome() {
    const m = renderAll();
    renderHero(m);
}

// Groups follow how the scheduler ranks tasks: a dated deadline, something soon, or no deadline at all.
const TASK_GROUPS = [
    { label: 'Deadline set', tone: 'warn', test: (t) => t.sort_tier <= 2 },
    { label: 'Coming up', tone: 'info', test: (t) => t.sort_tier === 3 },
    { label: 'No deadline', tone: 'neutral', test: (t) => t.sort_tier >= 4 },
];

// The server words the date ("Due Tomorrow", "Overdue by 2 days", "Fri 2 Oct", "ASAP", "No date").
const deadlineText = (t) => t.deadline;

async function renderTasks() {
    const list = $('taskList');
    let tasks = [];
    try {
        tasks = await api('/api/scheduler', { params: inboxParams() });
    } catch (err) {
        state.tasks = [];
        list.innerHTML = `<div class="p-4 text-xs text-red-400 text-center">${esc(err.message)}</div>`;
        return;
    }
    state.tasks = tasks;
    $('taskCount').textContent = tasks.length;

    if (!tasks.length) {
        const message = state.accounts.length
            ? 'Nothing needs action in this period.'
            : 'Connect a mailbox to see your action items.';
        list.innerHTML = `<div class="flex items-center justify-center h-full text-slate-500 text-sm p-4 text-center">${message}</div>`;
        return;
    }

    // Which mailbox a task came from only matters when there is more than one.
    const accountOf = (emailId) => state.accounts.find((a) => a.id === Number(emailId.split(':')[0]));
    const showMailbox = state.accounts.length > 1 && state.accountFilter === null;

    const card = (t) => {
        const mailbox = showMailbox ? accountOf(t.id) : null;
        const tone = t.sort_tier <= 2 ? 'warn' : t.sort_tier === 3 ? 'info' : 'neutral';
        return `
        <div data-email="${esc(t.id)}" class="p-4 bg-slate-800/40 hover:bg-slate-700/50 rounded-2xl cursor-pointer border border-slate-700/50 hover:border-blue-500/30 transition flex flex-col gap-2 group">
            <div class="flex items-start justify-between gap-2">
                <p class="text-fg font-semibold text-sm leading-snug group-hover:text-blue-400 transition-colors">${esc(t.task)}</p>
                <span class="chip tone-${tone} shrink-0">${esc(deadlineText(t))}</span>
            </div>
            <div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-400">
                <span class="truncate">${esc(senderName(t.sender))}</span>
                ${mailbox ? `<span class="ml-auto text-[10px] text-slate-500 truncate max-w-[50%]" title="${esc(mailbox.email_address)}">${esc(providerLabel(mailbox))} · ${esc(mailbox.email_address)}</span>` : ''}
            </div>
        </div>`;
    };

    list.innerHTML = TASK_GROUPS.map((group) => {
        const items = tasks.filter(group.test);
        if (!items.length) return '';
        return `
        <div class="space-y-2">
            <p class="px-1 pt-2 flex items-center gap-2 text-[0.68rem] font-bold uppercase tracking-wider text-slate-400">
                <span class="dot tone-${group.tone}"></span>${group.label}<span class="text-slate-500 font-semibold">${items.length}</span>
            </p>
            ${items.map(card).join('')}
        </div>`;
    }).join('');
}

// Kept for callers that re-lay-out the home screen (resize, sidebar, theme): redraws the Inbox explorer.
export function renderCharts() {
    if (widgetOn('inbox')) renderExplorer();
}

// --- email drawer (read one email: summary + original) ------------------------------------------

// `known` is for mail outside the loaded inbox window (e.g. an old ticket), which the caller fetched itself.
export function openEmailDrawer(emailId, known = null) {
    const email = known || state.emails.find((e) => e.id === emailId);
    if (!email) return;
    state.selectedEmailId = emailId;

    const account = state.accounts.find((a) => a.id === email.account_id);
    const messageId = email.id.split(':').slice(1).join(':');
    const gmailLink = account && account.provider === 'google'
        ? `https://mail.google.com/mail/u/${encodeURIComponent(account.email_address)}/#all/${encodeURIComponent(messageId)}`
        : null;

    $('drawerContent').innerHTML = `
        <div class="p-6 border-b border-slate-700/50 bg-slate-800/30 shrink-0">
            <h2 class="text-lg font-bold text-fg leading-tight mb-2">${esc(email.subject)}</h2>
            <p class="text-slate-400 text-sm"><strong>From:</strong> ${esc(email.sender)}</p>
            <p class="text-slate-500 text-xs mt-1">${esc(new Date(email.date).toLocaleString())}${account ? ` · ${esc(providerLabel(account))} · ${esc(account.email_address)}` : ''}</p>
            <div class="mt-3 flex items-center gap-2 text-[11px]">
                <span class="px-2 py-0.5 rounded-full border border-slate-600/50 text-slate-300">${esc(email.category)}</span>
                ${gmailLink ? `<a href="${esc(gmailLink)}" target="_blank" rel="noopener noreferrer" class="text-blue-400 hover:text-blue-300 underline">Open in Gmail ↗</a>` : ''}
            </div>
        </div>
        ${mailToolsHtml(email)}
        <div class="flex-1 overflow-y-auto p-6 space-y-5">
            <div class="bg-blue-900/20 border border-blue-500/30 rounded-lg p-4">
                ${email.summary
                    ? `<div class="space-y-1">${email.summary}</div>`
                    : '<span class="text-slate-400 text-sm">No AI summary for this email (yet).</span>'}
            </div>
            <div class="border border-slate-700/50 rounded-lg bg-slate-900/50">
                <div class="p-3 bg-slate-800/50 border-b border-slate-700/50 rounded-t-lg">
                    <span class="text-xs font-bold text-slate-400 uppercase">Original message</span>
                </div>
                <div class="p-4 text-sm text-slate-300 font-mono whitespace-pre-wrap break-words">${esc(email.body)}</div>
            </div>
        </div>`;

    bindMailTools($('drawerContent'), email, (updated, message) => {
        const at = state.emails.findIndex((e) => e.id === updated.id);
        if (at >= 0) state.emails[at] = updated;
        if (message) toast(message, 'success');
        openEmailDrawer(updated.id, updated);
        document.dispatchEvent(new CustomEvent('emailchanged'));
    });

    $('drawer').classList.remove('translate-x-full');
    $('drawerBackdrop').classList.remove('hidden');
}

export function closeEmailDrawer() {
    $('drawer').classList.add('translate-x-full');
    $('drawerBackdrop').classList.add('hidden');
}

export function initDashboard() {
    $('taskList').addEventListener('click', (event) => {
        const card = event.target.closest('[data-email]');
        if (card) openEmailDrawer(card.dataset.email);
    });
    $('drawerClose').addEventListener('click', closeEmailDrawer);
    $('drawerBackdrop').addEventListener('click', closeEmailDrawer);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeEmailDrawer(); });
    document.addEventListener('emailchanged', () => loadEmails({ silent: true })); // category, done or snooze changed
    document.addEventListener('tripschanged', renderHome); // a scan found (or changed) bookings
    document.addEventListener('dashchange', renderHome); // Settings switched a number or widget on/off
}
