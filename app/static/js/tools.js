// Inbox tools: everything that keeps the mailbox tidy, on one screen with four panels.
//
//   Briefing     today's briefing and the promotions digest, with "email me this now"
//   Newsletters  senders that keep mailing you, and the ones you never open (unsubscribe, mute, archive)
//   Follow-ups   conversations where you wrote last and nobody answered
//   Rules        your own sender / domain / keyword rules, and how to undo them

import { api } from './api.js';
import { hydrateIcons, icon } from './icons.js';
import { $, esc, senderName, toast } from './util.js';

const PANELS = [
    { id: 'briefing', label: 'Briefing' },
    { id: 'newsletters', label: 'Newsletters' },
    { id: 'followups', label: 'Follow-ups' },
    { id: 'rules', label: 'Rules' },
];
const CATEGORIES = ['Urgent / Action Required', 'Important', 'General', 'Promotional'];
let openEmail = () => {};
let current = 'briefing';

const card = 'glass-card p-4 sm:p-5';
const heading = (text, sub = '') => `
    <div class="mb-3"><h3 class="text-sm font-extrabold text-fg tracking-tight">${esc(text)}</h3>${sub ? `<p class="text-xs text-slate-400 mt-0.5">${esc(sub)}</p>` : ''}</div>`;
const small = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-600/60 bg-slate-800/60 text-slate-200 hover:bg-slate-700/60 cursor-pointer disabled:opacity-50';
const empty = (text) => `<p class="text-sm text-slate-500 py-6 text-center">${esc(text)}</p>`;
const when = (iso) => new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });

function shell() {
    const root = $('view-tools');
    root.className = 'flex-1 p-3 sm:p-6 flex-col gap-4 overflow-y-auto min-h-0 hidden';
    root.innerHTML = `
        <div class="flex flex-wrap items-end justify-between gap-3">
            <div>
                <h2 class="text-2xl sm:text-3xl font-extrabold tracking-tight text-fg">Inbox tools</h2>
                <p class="text-sm text-slate-300 mt-1">Keep your mail under control without reading all of it.</p>
            </div>
            <div class="seg" role="tablist" aria-label="Tools">
                ${PANELS.map((p) => `<button type="button" role="tab" data-panel="${p.id}" aria-pressed="${p.id === current}">${p.label}</button>`).join('')}
            </div>
        </div>
        <div id="toolsBody" class="flex flex-col gap-4 max-w-4xl w-full"></div>`;
    root.querySelector('[role="tablist"]').addEventListener('click', (event) => {
        const tab = event.target.closest('[data-panel]');
        if (!tab) return;
        current = tab.dataset.panel;
        root.querySelectorAll('[data-panel]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.panel === current)));
        render();
    });
    $('toolsBody').addEventListener('click', onClick);
}

const running = async (button, work) => {
    if (button) button.disabled = true;
    try { await work(); } catch (err) { toast(err.message, 'error'); } finally { if (button) button.disabled = false; }
};

// --- briefing ------------------------------------------------------------------------------------

const itemLine = (i) => `<li class="flex justify-between gap-3 py-1.5"><span class="truncate">${esc(i.title)}</span><span class="text-xs text-slate-400 shrink-0">${i.days_overdue ? `${i.days_overdue}d overdue` : esc(i.time || '')}</span></li>`;

// Each group gets its own colour so the heading band is clearly not one of the mail rows under it.
const SECTION_TONES = {
    'Overdue': 'text-red-500 bg-red-500/10 border-red-500',
    'Classes today': 'text-teal-500 bg-teal-500/10 border-teal-500',
    'Due today': 'text-amber-500 bg-amber-500/10 border-amber-500',
    'Coming up': 'text-blue-500 bg-blue-500/10 border-blue-500',
    'Needs you': 'text-violet-500 bg-violet-500/10 border-violet-500',
    'Waiting for a reply': 'text-slate-400 bg-slate-500/10 border-slate-500',
};

function briefingSection(title, items, render = itemLine) {
    if (!items.length) return '';
    const tone = SECTION_TONES[title] || SECTION_TONES['Waiting for a reply'];
    return `<section>
        <h3 class="flex items-center justify-between gap-2 rounded-lg border-l-4 px-3 py-1.5 text-xs font-extrabold uppercase tracking-wider ${tone}"><span>${esc(title)}</span><span class="rounded-full bg-slate-500/20 px-2 py-0.5 text-[0.7rem] leading-none text-fg">${items.length}</span></h3>
        <ul class="mt-1 ml-3 pl-3 border-l border-slate-700/50 text-sm text-fg divide-y divide-slate-700/40">${items.map(render).join('')}</ul>
    </section>`;
}

async function renderBriefing(body) {
    const [b, digest] = await Promise.all([api('/api/briefing'), api('/api/digest/promotions')]);
    const parts = [
        briefingSection('Overdue', b.overdue),
        briefingSection('Classes today', b.classes, (c) => `<li class="flex justify-between gap-3 py-1"><span>${esc(c.title)}${c.room ? ` <span class="text-slate-500">(${esc(c.room)})</span>` : ''}</span><span class="text-xs text-slate-400">${esc(c.time)}</span></li>`),
        briefingSection('Due today', b.today),
        briefingSection('Coming up', b.upcoming),
        briefingSection('Needs you', b.top_emails, (e) => `<li class="py-1"><button type="button" data-open="${esc(e.id)}" class="text-left hover:text-blue-400 cursor-pointer"><span class="font-semibold">${esc(e.subject)}</span> <span class="text-xs text-slate-400">from ${esc(senderName(e.sender))}</span></button></li>`),
        briefingSection('Waiting for a reply', b.waiting, (w) => `<li class="flex justify-between gap-3 py-1"><span class="truncate">${esc(w.subject)}</span><span class="text-xs text-slate-400 shrink-0">${w.days}d, ${esc(senderName(w.recipient))}</span></li>`),
    ].filter(Boolean);
    body.innerHTML = `
        <div class="${card} space-y-5">
            <div class="flex flex-wrap items-center justify-between gap-2">${heading(`${b.greeting}, here is ${b.date}`, `${b.counts.emails} emails in the last 24 hours, ${b.counts.urgent} need action`)}
                <button type="button" class="${small}" data-send="briefing">${icon('mail')}Email me this</button></div>
            ${parts.length ? parts.join('') : empty('Nothing needs you today.')}
        </div>
        <div class="${card} space-y-2">
            <div class="flex flex-wrap items-center justify-between gap-2">${heading('Promotions digest', 'One summary instead of a day of promotional mail')}
                <button type="button" class="${small}" data-send="digest">${icon('mail')}Email me this</button></div>
            <p class="text-sm text-fg">${esc(digest.headline)}</p>
            ${digest.top_senders.length ? `<p class="text-xs text-slate-400">Most emails from: ${digest.top_senders.map((s) => `${esc(s.sender)} (${s.count})`).join(', ')}</p>` : ''}
            <p class="text-xs text-slate-500">Turn on the evening digest and the morning briefing email in Settings.</p>
        </div>`;
}

// --- newsletters ---------------------------------------------------------------------------------

async function renderNewsletters(body) {
    const [list, unopened] = await Promise.all([api('/api/newsletters'), api('/api/newsletters/unopened')]);
    const unopenedRows = unopened.map((n) => `
        <li class="flex flex-wrap items-center gap-2 py-2">
            <div class="min-w-0 flex-1"><p class="text-sm font-semibold text-fg truncate">${esc(senderName(n.sender))}</p>
                <p class="text-xs text-slate-400">${n.messages} emails since ${esc(when(n.first_date))}, none opened${n.one_click ? '' : ', unsubscribe needs a click'}</p></div>
            <button type="button" class="${small}" data-clean="${esc(n.sender_address)}">${icon('x')}Clean up</button>
        </li>`).join('');
    const rows = list.map((n) => `
        <li class="flex flex-wrap items-center gap-2 py-2">
            <div class="min-w-0 flex-1"><p class="text-sm font-semibold text-fg truncate">${esc(senderName(n.sender))} <span class="text-xs font-normal text-slate-400">${n.count} in 30 days</span></p>
                <p class="text-xs text-slate-500 truncate">Latest: ${esc(n.last_subject)}</p></div>
            ${n.muted ? '<span class="chip tone-neutral">Muted</span>' : ''}
            ${n.can_unsubscribe && !n.muted ? `<button type="button" class="${small}" data-unsub="${esc(n.sender_address)}">Unsubscribe</button>` : ''}
            ${!n.muted ? `<button type="button" class="${small}" data-mute="${esc(n.sender_address)}">Mute</button>` : ''}
        </li>`).join('');
    body.innerHTML = `
        <div class="${card}">
            <div class="flex flex-wrap items-center justify-between gap-2">${heading('Newsletters you never open', 'Unread for months. Cleaning up unsubscribes when the sender allows it, then mutes and archives them here.')}
                ${unopened.length > 1 ? `<button type="button" class="${small}" data-clean-all>${icon('sparkle')}Clean up all ${unopened.length}</button>` : ''}</div>
            ${unopened.length ? `<ul class="divide-y divide-slate-700/40">${unopenedRows}</ul>` : empty('Nothing here. Senders show up once you have not opened three or more of their emails over your cleanup period (Settings).')}
        </div>
        <div class="${card}">
            ${heading('Everyone who keeps mailing you', 'Promotions and anything with an unsubscribe link, busiest first')}
            ${list.length ? `<ul class="divide-y divide-slate-700/40">${rows}</ul>` : empty('No bulk senders in the last 30 days.')}
        </div>`;
}

// --- follow-ups ----------------------------------------------------------------------------------

async function renderFollowups(body) {
    const list = await api('/api/followups');
    body.innerHTML = `<div class="${card}">${heading('Waiting for a reply', 'You wrote last and nobody has answered')}
        ${list.length ? `<ul class="divide-y divide-slate-700/40">${list.map((f) => `
            <li class="flex flex-wrap items-center gap-2 py-2">
                <div class="min-w-0 flex-1"><p class="text-sm font-semibold text-fg truncate">${esc(f.subject)}</p>
                    <p class="text-xs text-slate-400">To ${esc(senderName(f.recipient))}, ${f.waiting_days} day${f.waiting_days === 1 ? '' : 's'} ago</p></div>
                <button type="button" class="${small}" data-fu="schedule" data-id="${f.id}">${icon('calendar')}Follow up tomorrow</button>
                <button type="button" class="${small}" data-fu="snooze" data-id="${f.id}">${icon('clock')}Ask again in 3 days</button>
                <button type="button" class="${small}" data-fu="dismiss" data-id="${f.id}">Dismiss</button>
            </li>`).join('')}</ul>` : empty('Nobody owes you a reply right now.')}</div>`;
}

// --- rules ---------------------------------------------------------------------------------------

const KIND_LABEL = { sender: 'From', domain: 'Domain', keyword: 'Word', search: 'Search' };
const PLACEHOLDER = {
    sender: 'news@shop.com',
    domain: 'shop.com',
    keyword: 'a whole word, e.g. invoice',
    search: 'invoice OR receipt -newsletter, "payment due", subject:urgent',
};

async function renderRules(body) {
    const rules = await api('/api/rules');
    body.innerHTML = `
        <div class="${card}">
            ${heading('Add a rule', 'Your rules always win over the automatic ranking. Mail you correct by hand is never overridden.')}
            <form id="ruleForm" class="grid grid-cols-1 sm:grid-cols-[8rem_1fr_12rem_auto] gap-2 items-center">
                <select name="kind" class="field" aria-label="Rule type">${Object.entries(KIND_LABEL).map(([k, l]) => `<option value="${k}">${l}</option>`).join('')}</select>
                <input name="pattern" required maxlength="320" class="field" placeholder="${esc(PLACEHOLDER.sender)}" aria-label="Pattern">
                <select name="category" class="field" aria-label="Category">${CATEGORIES.map((c) => `<option>${esc(c)}</option>`).join('')}</select>
                <button type="submit" class="bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium px-4 py-2 rounded-lg cursor-pointer">Add</button>
            </form>
            <p id="ruleHelp" class="text-xs text-slate-400 mt-2" hidden>Search finds mail that <em>contains</em> your words, ignoring case, accents and punctuation. Words must all appear; <b>OR</b> or <b>|</b> means either; <b>-word</b> or <b>NOT word</b> excludes; <b>"exact phrase"</b>; <b>(brackets)</b> to group; <b>subject:</b>, <b>from:</b>, <b>body:</b> to search one place; <b>=word</b> for a whole word only; <b>pay*ment</b> as a wildcard. Write OR, AND and NOT in capitals.</p>
        </div>
        <div class="${card}">
            ${heading('Your rules', 'Removing a rule sends its mail back to the automatic classifier')}
            ${rules.length ? `<ul class="divide-y divide-slate-700/40">${rules.map((r) => `
                <li class="flex items-center gap-2 py-2"><p class="text-sm text-fg min-w-0 flex-1 truncate"><span class="text-slate-400">${KIND_LABEL[r.kind] || esc(r.kind)}</span> ${esc(r.pattern)} <span class="text-slate-400">is</span> ${esc(r.category)}</p>
                <button type="button" class="${small}" data-rule-delete="${r.id}" aria-label="Remove rule">${icon('trash')}Remove</button></li>`).join('')}</ul>` : empty('No rules yet. Correct an email in its drawer and choose "Always, for this sender" to create one.')}
        </div>`;
    const kindSelect = $('ruleForm').elements.kind;
    kindSelect.addEventListener('change', () => {
        $('ruleForm').elements.pattern.placeholder = PLACEHOLDER[kindSelect.value];
        $('ruleHelp').hidden = kindSelect.value !== 'search';
    });
    $('ruleForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        const form = new FormData(event.target);
        await running(event.submitter, async () => {
            const made = await api('/api/rules', { method: 'POST', body: { kind: form.get('kind'), pattern: form.get('pattern'), category: form.get('category') } });
            toast(`Rule added. ${made.affected} stored email${made.affected === 1 ? '' : 's'} changed.`, 'success');
            document.dispatchEvent(new CustomEvent('emailchanged'));
            render();
        });
    });
}

// --- events --------------------------------------------------------------------------------------

async function onClick(event) {
    const t = event.target;
    const button = t.closest('button');
    const open = t.closest('[data-open]');
    if (open) return openEmail(open.dataset.open);
    if (!button) return undefined;
    const d = button.dataset;
    return running(button, async () => {
        if (d.send) {
            const sent = await api(d.send === 'digest' ? '/api/digest/promotions/send' : '/api/briefing/send', { method: 'POST' });
            toast(`Sent to ${sent.sent_to}.`, 'success');
        } else if (d.unsub || d.mute) {
            const address = d.unsub || d.mute;
            const result = await api('/api/newsletters/unsubscribe', { method: 'POST', body: { sender_address: address, mute: true } });
            if (result.method === 'link' && result.url) window.open(result.url, '_blank', 'noopener,noreferrer');
            toast(d.mute ? 'Muted.' : result.detail || 'Done.', result.ok || d.mute ? 'success' : 'info');
            document.dispatchEvent(new CustomEvent('emailchanged'));
            render();
        } else if (d.clean !== undefined || d.cleanAll !== undefined) {
            const body = d.clean !== undefined ? { sender_addresses: [d.clean] } : {};
            const results = await api('/api/newsletters/unopened/cleanup', { method: 'POST', body });
            const opened = results.filter((r) => r.link);
            results.forEach((r) => { if (r.link) window.open(r.link, '_blank', 'noopener,noreferrer'); });
            toast(`Cleaned up ${results.length}: ${results.filter((r) => r.unsubscribed).length} unsubscribed${opened.length ? `, ${opened.length} need you to finish in the tab that opened` : ''}.`, 'success', 7000);
            document.dispatchEvent(new CustomEvent('emailchanged'));
            render();
        } else if (d.fu) {
            const path = `/api/followups/${d.id}/${d.fu}`;
            await api(path, { method: 'POST', ...(d.fu === 'snooze' ? { body: { days: 3 } } : {}) });
            if (d.fu === 'schedule') document.dispatchEvent(new CustomEvent('taskschanged'));
            render();
        } else if (d.ruleDelete) {
            await api(`/api/rules/${d.ruleDelete}`, { method: 'DELETE' });
            document.dispatchEvent(new CustomEvent('emailchanged'));
            render();
        }
    });
}

async function render() {
    const body = $('toolsBody');
    body.innerHTML = '<p class="text-sm text-slate-500 py-8 text-center">Loading...</p>';
    try {
        await { briefing: renderBriefing, newsletters: renderNewsletters, followups: renderFollowups, rules: renderRules }[current](body);
        hydrateIcons(body);
    } catch (err) {
        body.innerHTML = `<div class="${card} text-sm text-red-400">${esc(err.message)}</div>`;
    }
}

// Which panel opens next (used by the notification bell: a follow-up nudge opens Follow-ups).
export function showPanel(id) {
    if (PANELS.some((p) => p.id === id)) current = id;
    document.querySelectorAll('#view-tools [data-panel]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.panel === current)));
}

export function initTools({ openEmail: open } = {}) {
    if (open) openEmail = open;
    if ($('view-tools')) shell();
}

export async function loadTools() {
    if (!$('toolsBody')) shell();
    await render();
}

export { CATEGORIES };
