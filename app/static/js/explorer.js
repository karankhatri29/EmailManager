// The Inbox explorer: one interactive card that replaces the old donut and line chart.
//
//   priority chips (with counts) + a proportional mix bar   -> filter by priority
//   a stacked-bar timeline (hours for 24h, days otherwise)  -> click a bar to filter by that time
//   search, and "Top senders" clicks                        -> filter by text / sender
//   the email list                                          -> open an email, or turn it into a task
//
// Every control narrows the same list, and each control's numbers reflect the other filters, so the chips
// and bars always show what is left to look at. All of it is computed in the browser from state.emails.

import { api } from './api.js';
import { CATEGORY_COLORS, state } from './state.js';
import { $, esc, localYmd, senderName, timeOf, toast } from './util.js';

const PAGE_SIZE = 25;
const HOUR_MS = 3600 * 1000;
const URGENT = 'Urgent / Action Required';

// Display order is also the sort rank for "Smart" sorting.
const CATEGORIES = [
    { id: URGENT, label: 'Action required' },
    { id: 'Important', label: 'Important' },
    { id: 'General', label: 'General' },
    { id: 'Promotional', label: 'Promotional' },
];
const RANK = Object.fromEntries(CATEGORIES.map((c, i) => [c.id, i]));
// Stacking order in a bar, bottom to top: the urgent slice sits on top where it is easiest to spot.
const STACK = [...CATEGORIES].reverse();

const view = { priority: null, bucket: null, sender: null, query: '', sort: 'smart', shown: PAGE_SIZE, ask: null };
let hooks = { openEmail() {} };
let typingTimer = null; // debounce for the live filter; "Ask" cancels a pending one

const colour = (id) => CATEGORY_COLORS[id] || '#94a3b8';

// --- time buckets --------------------------------------------------------------------------------

// Hourly bars for the last day, daily bars for a week or a month. One extra bar at the start covers the
// partial hour / day that the server's "last N days" window also includes.
function buildBuckets() {
    const now = new Date();
    const hourly = state.timeframe === 'Last 1 Day';
    const buckets = [];
    if (hourly) {
        const last = new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours());
        for (let i = 24; i >= 0; i -= 1) {
            const start = new Date(last.getTime() - i * HOUR_MS);
            buckets.push({ start, end: new Date(start.getTime() + HOUR_MS) });
        }
    } else {
        // A day per bar up to a month; a week per bar beyond that.
        const days = { 'Last 1 Week': 7, 'Last 1 Month': 30, 'Last 3 Months': 91, 'Last 1 Year': 364 }[state.timeframe] || 30;
        const step = days > 31 ? 7 : 1;
        for (let i = Math.ceil(days / step); i >= 0; i -= 1) {
            buckets.push({
                start: new Date(now.getFullYear(), now.getMonth(), now.getDate() - i * step),
                end: new Date(now.getFullYear(), now.getMonth(), now.getDate() - i * step + step),
                weekly: step > 1,
            });
        }
    }
    return { hourly, buckets };
}

function bucketLabel(bucket, hourly) {
    if (bucket.weekly) return `Week of ${bucket.start.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`;
    return hourly
        ? `${bucket.start.toLocaleDateString('en-US', { weekday: 'short' })} ${bucket.start.toLocaleTimeString('en-US', { hour: 'numeric' })}`
        : bucket.start.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

const inBucket = (email, bucket) => {
    const t = new Date(email.date).getTime();
    return t >= bucket.start.getTime() && t < bucket.end.getTime();
};

// --- filtering -----------------------------------------------------------------------------------

// `skip` leaves one filter out so a control can show what it *would* offer given all the others.
function matching(skip = '') {
    const query = view.query.trim().toLowerCase();
    return state.emails.filter((e) => {
        if (skip !== 'priority' && view.priority && e.category !== view.priority) return false;
        if (skip !== 'bucket' && view.bucket && !inBucket(e, view.bucket)) return false;
        if (view.sender && senderName(e.sender).toLowerCase() !== view.sender.toLowerCase()) return false;
        if (query && !`${e.subject} ${e.sender} ${e.body}`.toLowerCase().includes(query)) return false;
        return true;
    });
}

const anyFilter = () => Boolean(view.priority || view.bucket || view.sender || view.query.trim());

// --- rendering: chips + mix bar ------------------------------------------------------------------

function renderChips() {
    const pool = matching('priority');
    const counts = Object.fromEntries(CATEGORIES.map((c) => [c.id, 0]));
    pool.forEach((e) => { if (e.category in counts) counts[e.category] += 1; });

    const chip = (id, label, count, dot) => `
        <button type="button" data-priority="${esc(id ?? '')}" aria-pressed="${view.priority === id}" class="pchip">
            ${dot ? `<span class="w-2 h-2 rounded-full shrink-0" style="background:${dot}"></span>` : ''}
            <span>${esc(label)}</span><span class="pchip-n">${count}</span>
        </button>`;

    $('priorityChips').innerHTML = chip(null, 'All', pool.length, null)
        + CATEGORIES.map((c) => chip(c.id, c.label, counts[c.id], colour(c.id))).join('');

    // The mix bar is the donut chart's information in a form that is also a control.
    const total = pool.length || 1;
    $('mixBar').innerHTML = CATEGORIES.filter((c) => counts[c.id]).map((c) => `
        <button type="button" data-priority="${esc(c.id)}" class="h-full transition-[flex-grow] duration-300 ${view.priority && view.priority !== c.id ? 'opacity-30' : ''}"
            style="flex:${counts[c.id]} 1 0;background:${colour(c.id)}" title="${esc(c.label)}: ${counts[c.id]} (${Math.round((counts[c.id] / total) * 100)}%)"
            aria-label="${esc(c.label)}, ${counts[c.id]} emails"></button>`).join('');
}

// --- rendering: timeline -------------------------------------------------------------------------

let timelineData = { hourly: true, rows: [] };

function renderTimeline() {
    const { hourly, buckets } = buildBuckets();
    const pool = matching('bucket');
    const rows = buckets.map((bucket) => {
        const counts = Object.fromEntries(CATEGORIES.map((c) => [c.id, 0]));
        let total = 0;
        pool.forEach((e) => { if (inBucket(e, bucket) && e.category in counts) { counts[e.category] += 1; total += 1; } });
        return { bucket, counts, total };
    });
    timelineData = { hourly, rows };

    const max = Math.max(1, ...rows.map((r) => r.total));
    const selected = view.bucket ? view.bucket.start.getTime() : null;

    $('timelineBars').innerHTML = rows.map((row, i) => {
        const isSelected = selected === row.bucket.start.getTime();
        const dim = selected !== null && !isSelected;
        const segments = STACK.map((c) => (row.counts[c.id]
            ? `<span class="tl-seg" style="height:${(row.counts[c.id] / max) * 100}%;background:${colour(c.id)}"></span>`
            : '')).join('');
        return `
            <button type="button" data-bucket="${i}" aria-pressed="${isSelected}" class="tl-col ${dim ? 'tl-dim' : ''}"
                aria-label="${esc(bucketLabel(row.bucket, hourly))}: ${row.total} emails">${segments}</button>`;
    }).join('');

    // Axis labels, thinned so they never collide (about one per 4 hours / 1 day / 5 days).
    const step = hourly ? 4 : buckets.length <= 8 ? 1 : buckets.length > 40 ? 8 : 5;
    $('timelineAxis').innerHTML = rows.map((row, i) => `
        <span class="tl-tick">${i % step === 0 ? esc(hourly
        ? row.bucket.start.toLocaleTimeString('en-US', { hour: 'numeric' }).replace(' ', '').toLowerCase()
        : row.bucket.start.toLocaleDateString('en-US', buckets.length <= 8 ? { weekday: 'short' } : { day: 'numeric', month: 'short' })) : ''}</span>`).join('');

    setCaption(view.bucket ? rows.findIndex((r) => r.bucket.start.getTime() === selected) : -1);
}

// The line under the bars: what you are pointing at, or what is selected.
function setCaption(index) {
    const caption = $('timelineCaption');
    const { hourly, rows } = timelineData;
    if (index < 0 || !rows[index]) {
        caption.textContent = view.bucket ? '' : 'Tap a bar to see just that time.';
        return;
    }
    const { bucket, counts, total } = rows[index];
    const parts = CATEGORIES.filter((c) => counts[c.id]).map((c) => `${counts[c.id]} ${c.label.toLowerCase()}`);
    caption.textContent = `${bucketLabel(bucket, hourly)}: ${total} email${total === 1 ? '' : 's'}${parts.length ? ` (${parts.join(', ')})` : ''}`;
}

// --- rendering: list -----------------------------------------------------------------------------

const plain = (html) => String(html || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();

function whenLabel(date) {
    const d = new Date(date);
    return localYmd(d) === localYmd(new Date())
        ? timeOf(d)
        : d.toLocaleDateString('en-US', { weekday: 'short', day: 'numeric' });
}

function sorted(list) {
    const byDate = (a, b) => new Date(b.date) - new Date(a.date);
    return [...list].sort(view.sort === 'newest'
        ? byDate
        : (a, b) => (RANK[a.category] ?? 9) - (RANK[b.category] ?? 9) || b.score - a.score || byDate(a, b));
}

function row(e) {
    const preview = plain(e.summary) || plain(e.body) || plain(e.snippet);
    const account = state.accounts.length > 1 ? state.accounts.find((a) => a.id === e.account_id) : null;
    return `
        <div data-email="${esc(e.id)}" class="xrow group ${e.is_done ? 'opacity-60' : ''}" role="button" tabindex="0">
            <span class="w-2.5 h-2.5 rounded-full shrink-0 mt-1.5" style="background:${colour(e.category)}" title="${esc(e.category)}"></span>
            <div class="min-w-0 flex-1">
                <div class="flex items-center justify-between gap-2">
                    <span class="text-xs font-bold text-slate-300 truncate">${esc(senderName(e.sender))}${e.is_done ? ' <span class="text-[10px] font-semibold text-emerald-400">Done</span>' : ''}</span>
                    <span class="text-[11px] text-slate-500 whitespace-nowrap">${esc(whenLabel(e.date))}</span>
                </div>
                <p class="text-sm font-semibold text-fg truncate group-hover:text-blue-400 transition-colors">${esc(e.subject)}</p>
                <p class="text-xs text-slate-400 truncate">${esc(preview.slice(0, 140))}</p>
                ${account ? `<p class="text-[10px] text-slate-500 truncate mt-0.5">${esc(account.email_address)}</p>` : ''}
            </div>
            <button type="button" data-addtask="${esc(e.id)}" class="xtask" title="Add to my tasks" aria-label="Add to my tasks">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-4 h-4" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" /></svg>
                <span class="hidden sm:inline">Task</span>
            </button>
        </div>`;
}

function renderList() {
    // "Ask" results are already ranked by relevance, and cover all stored mail, not just this timeframe.
    const list = view.ask ? view.ask.items : sorted(matching());
    const visible = list.slice(0, view.shown);

    const info = $('inboxAskInfo');
    info.classList.toggle('hidden', !view.ask);
    if (view.ask) {
        info.textContent = `${view.ask.plan.explanation}${view.ask.semantic ? ', ranked by meaning' : ''}. ${list.length} result${list.length === 1 ? '' : 's'}.`;
    }

    $('inboxCount').textContent = view.ask
        ? 'Answers to your question'
        : anyFilter()
        ? `${list.length} of ${state.emails.length} emails`
        : `${state.emails.length} email${state.emails.length === 1 ? '' : 's'}`;

    if (view.ask && !list.length) {
        $('inboxList').innerHTML = '<p class="text-sm text-slate-500 py-10 text-center">Nothing in your stored mail matches that. Try different words, or sync a longer timeframe.</p>';
    } else if (!state.emails.length && !view.ask) {
        $('inboxList').innerHTML = `<p class="text-sm text-slate-500 py-10 text-center">${state.accounts.length ? 'No mail in this period.' : 'Connect a mailbox to see your mail here.'}</p>`;
    } else if (!list.length) {
        $('inboxList').innerHTML = `
            <div class="py-10 text-center">
                <p class="text-sm font-bold text-fg">Nothing matches</p>
                <button type="button" data-clear class="mt-2 text-xs font-semibold text-blue-400 hover:text-blue-300 cursor-pointer">Clear filters</button>
            </div>`;
    } else {
        $('inboxList').innerHTML = visible.map(row).join('');
    }

    const more = $('inboxMore');
    more.classList.toggle('hidden', list.length <= visible.length);
    more.textContent = `Show ${Math.min(PAGE_SIZE, list.length - visible.length)} more`;
}

// The active filters, each removable.
function renderFilterBar() {
    const bar = $('inboxFilters');
    const chips = [];
    const add = (key, text) => chips.push(`<button type="button" data-drop="${key}" class="fchip">${esc(text)} <span aria-hidden="true">×</span><span class="sr-only">remove</span></button>`);
    if (view.priority) add('priority', CATEGORIES.find((c) => c.id === view.priority).label);
    if (view.bucket) add('bucket', bucketLabel(view.bucket, timelineData.hourly));
    if (view.sender) add('sender', `From ${view.sender}`);
    if (view.query.trim()) add('query', `"${view.query.trim()}"`);
    if (view.ask) add('ask', `Asked: ${view.ask.question}`);

    bar.classList.toggle('hidden', !chips.length);
    bar.classList.toggle('flex', chips.length > 0);
    bar.innerHTML = chips.length
        ? chips.join('') + '<button type="button" data-clear class="text-xs font-semibold text-slate-400 hover:text-fg cursor-pointer ml-1">Clear all</button>'
        : '';
}

export function renderExplorer() {
    if (!$('card-inbox') || $('card-inbox').classList.contains('hidden')) return;
    // A saved bucket / sender can disappear when the timeframe or mailbox changes; drop stale ones.
    if (view.bucket && !buildBuckets().buckets.some((b) => b.start.getTime() === view.bucket.start.getTime())) view.bucket = null;
    renderChips();
    renderTimeline();
    renderFilterBar();
    renderList();
    document.querySelectorAll('[data-sort]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.sort === view.sort)));
}

// --- actions --------------------------------------------------------------------------------------

const refresh = () => { view.shown = PAGE_SIZE; renderExplorer(); };
const reveal = () => $('card-inbox').scrollIntoView({ behavior: 'smooth', block: 'start' });

// Called from the KPI tiles ("Needs action" -> action-required mail).
export function focusPriority(category) {
    view.priority = category;
    refresh();
    reveal();
}

// Called from "Top senders".
export function setSender(name) {
    view.sender = view.sender && view.sender.toLowerCase() === name.toLowerCase() ? null : name;
    refresh();
    if (view.sender) reveal();
}

// Natural-language search over everything stored (the server plans it: keywords, dates, sender, category).
async function ask() {
    const question = $('inboxSearch').value.trim();
    if (question.length < 2) { toast('Type a question first, for example: the invoice from last summer.', 'info', 3500); return; }
    clearTimeout(typingTimer);
    const button = $('inboxAsk');
    button.disabled = true;
    try {
        const params = { q: question, ...(state.accountFilter !== null ? { account_id: state.accountFilter } : {}) };
        const result = await api('/api/search', { params });
        view.ask = { question, plan: result.plan, semantic: result.semantic, items: result.items };
        view.query = '';
        refresh();
    } catch (err) {
        toast(err.message, 'error');
    } finally {
        button.disabled = false;
    }
}

async function openHit(emailId) {
    if (state.emails.some((e) => e.id === emailId)) return hooks.openEmail(emailId);
    try {
        return hooks.openEmail(emailId, await api(`/api/emails/${encodeURIComponent(emailId)}`)); // older than this timeframe
    } catch (err) {
        return toast(err.message, 'error');
    }
}

async function addTask(emailId) {
    const email = state.emails.find((e) => e.id === emailId) || (view.ask && view.ask.items.find((e) => e.id === emailId));
    if (!email) return;
    try {
        await api('/api/activities', {
            method: 'POST',
            body: { title: email.subject.slice(0, 255), notes: `From ${email.sender}` },
        });
        toast('Added to your tasks. Give it a date in Activity.', 'success', 3500);
        document.dispatchEvent(new CustomEvent('taskschanged'));
    } catch (err) {
        toast(err.message, 'error');
    }
}

export function initExplorer(wiring = {}) {
    hooks = { ...hooks, ...wiring };
    const card = $('card-inbox');

    card.addEventListener('click', (event) => {
        const target = event.target;
        const priority = target.closest('[data-priority]');
        if (priority) {
            const id = priority.dataset.priority || null;
            view.priority = view.priority === id ? null : id;
            return refresh();
        }
        const bar = target.closest('[data-bucket]');
        if (bar) {
            const { start } = timelineData.rows[Number(bar.dataset.bucket)].bucket;
            view.bucket = view.bucket && view.bucket.start.getTime() === start.getTime() ? null : timelineData.rows[Number(bar.dataset.bucket)].bucket;
            return refresh();
        }
        const drop = target.closest('[data-drop]');
        if (drop) {
            const key = drop.dataset.drop;
            view[key] = key === 'query' ? '' : null; // 'ask' is dropped the same way
            if (key === 'query') $('inboxSearch').value = '';
            return refresh();
        }
        if (target.closest('[data-clear]')) {
            Object.assign(view, { priority: null, bucket: null, sender: null, query: '', ask: null });
            $('inboxSearch').value = '';
            return refresh();
        }
        const sort = target.closest('[data-sort]');
        if (sort) { view.sort = sort.dataset.sort; return refresh(); }
        if (target.closest('#inboxMore')) { view.shown += PAGE_SIZE; return renderList(); }

        const add = target.closest('[data-addtask]');
        if (add) return addTask(add.dataset.addtask);
        const item = target.closest('[data-email]');
        if (item) openHit(item.dataset.email);
    });

    card.addEventListener('keydown', (event) => {
        const item = event.target.closest('[data-email]');
        if (item && event.target === item && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault();
            openHit(item.dataset.email);
        }
    });

    // Pointing at a bar describes it (a tap selects it, and the caption then keeps describing the selection).
    $('timelineBars').addEventListener('mouseover', (event) => {
        const bar = event.target.closest('[data-bucket]');
        if (bar) setCaption(Number(bar.dataset.bucket));
    });
    $('timelineBars').addEventListener('mouseleave', () => {
        setCaption(view.bucket ? timelineData.rows.findIndex((r) => r.bucket.start.getTime() === view.bucket.start.getTime()) : -1);
    });

    $('inboxAsk').addEventListener('click', ask);
    $('inboxSearch').addEventListener('keydown', (event) => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) ask(); });

    $('inboxSearch').addEventListener('input', (event) => {
        clearTimeout(typingTimer);
        typingTimer = setTimeout(() => { view.query = event.target.value; view.ask = null; refresh(); }, 150);
    });

    document.addEventListener('themechange', renderExplorer); // bar colours come from the theme
}
