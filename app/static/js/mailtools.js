// What you can do to one email, shown at the top of the email drawer: why it was ranked the way it was,
// correct its category, mark it done, snooze it, unsubscribe, and read a three-bullet summary of its thread.

import { api } from './api.js';
import { icon } from './icons.js';
import { $, esc, toast } from './util.js';

const CATEGORIES = ['Urgent / Action Required', 'Important', 'General', 'Promotional'];
const SNOOZE_DAYS = [1, 3, 7, 14];
const SOURCE_LABEL = { auto: 'Chosen automatically', rule: 'Chosen by one of your rules', user: 'Set by you' };

const btn = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-600/60 bg-slate-800/60 text-slate-200 hover:bg-slate-700/60 cursor-pointer';

export function mailToolsHtml(email) {
    const snoozed = email.snoozed_until && new Date(email.snoozed_until) > new Date();
    return `
    <div id="mailTools" class="px-6 py-4 border-b border-slate-700/50 space-y-3 shrink-0">
        <p class="text-xs text-slate-400" data-why>
            <span class="font-semibold text-slate-300">Why ${esc(email.category)}?</span>
            ${esc(email.reason || 'No reason was recorded for this email.')}
            <span class="text-slate-500">(${esc(SOURCE_LABEL[email.category_source] || SOURCE_LABEL.auto)})</span>
        </p>
        <div class="flex flex-wrap items-center gap-2">
            <button type="button" class="${btn}" data-act="done">${icon('check')}${email.is_done ? 'Reopen' : 'Mark done'}</button>
            <label class="${btn} !cursor-default">${icon('clock')}<span>${snoozed ? 'Snoozed until ' + esc(new Date(email.snoozed_until).toLocaleDateString()) : 'Snooze'}</span>
                <select data-act="snooze" class="bg-transparent text-xs outline-none cursor-pointer" aria-label="Snooze for">
                    <option value="">${snoozed ? 'Change' : 'for'}</option>
                    ${SNOOZE_DAYS.map((d) => `<option value="${d}">${d} day${d > 1 ? 's' : ''}</option>`).join('')}
                    ${snoozed ? '<option value="0">Wake now</option>' : ''}
                </select>
            </label>
            ${email.unsubscribe_url ? `<button type="button" class="${btn}" data-act="unsubscribe">${icon('x')}Unsubscribe and mute</button>` : ''}
        </div>
        <div class="flex flex-wrap items-center gap-2">
            <label class="text-xs text-slate-400" for="mailCategory">Wrong category?</label>
            <select id="mailCategory" class="field !w-auto !py-1.5 text-xs">
                ${CATEGORIES.map((c) => `<option ${c === email.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}
            </select>
            <label class="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
                <input id="mailCategorySender" type="checkbox" class="accent-[hsl(var(--brand-h)_92%_60%)]"> Always, for this sender
            </label>
            <button type="button" class="${btn}" data-act="category">Apply</button>
        </div>
        ${email.thread_id ? `
        <div class="rounded-lg border border-slate-700/50 bg-slate-900/40 p-3" id="mailThread">
            <div class="flex items-center gap-2">
                <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">Conversation</span>
                <span class="text-xs text-slate-500" data-thread-count></span>
                <button type="button" class="${btn} ml-auto hidden" data-act="thread">${icon('sparkle')}<span>Summarise in 3 points</span></button>
            </div>
            <div class="mt-2 space-y-1" data-thread-summary></div>
        </div>` : ''}
    </div>`;
}

async function patch(email, body) {
    return api(`/api/emails/${encodeURIComponent(email.id)}`, { method: 'PATCH', body });
}

async function loadThread(root, email) {
    const box = root.querySelector('#mailThread');
    if (!box) return;
    const button = box.querySelector('[data-act="thread"]');
    const count = box.querySelector('[data-thread-count]');
    const out = box.querySelector('[data-thread-summary]');
    const paint = (thread) => {
        count.textContent = `${thread.message_count} message${thread.message_count === 1 ? '' : 's'}`;
        // The summary HTML is built on the server from escaped text.
        out.innerHTML = thread.summary
            ? thread.summary + (thread.summary_current ? '' : '<p class="text-[11px] text-slate-500 mt-1">The conversation has new messages since this summary.</p>')
            : '';
        button.classList.toggle('hidden', thread.message_count < 2);
        button.querySelector('span').textContent = thread.summary ? 'Refresh summary' : 'Summarise in 3 points';
    };
    try {
        paint(await api(`/api/emails/${encodeURIComponent(email.id)}/thread`));
    } catch {
        box.classList.add('hidden');
        return;
    }
    button.addEventListener('click', async () => {
        button.disabled = true;
        button.querySelector('span').textContent = 'Summarising...';
        try {
            paint(await api(`/api/emails/${encodeURIComponent(email.id)}/thread/summary`, { method: 'POST' }));
        } catch (err) {
            toast(err.message, 'error');
            button.querySelector('span').textContent = 'Summarise in 3 points';
        } finally {
            button.disabled = false;
        }
    });
}

// `changed(updatedEmail)` is called after the server accepted a change, so the caller can redraw.
export function bindMailTools(root, email, changed) {
    const tools = root.querySelector('#mailTools');
    if (!tools) return;

    tools.addEventListener('click', async (event) => {
        const action = event.target.closest('[data-act]');
        if (!action || action.tagName === 'SELECT') return;
        try {
            if (action.dataset.act === 'done') {
                changed(await patch(email, { is_done: !email.is_done }), email.is_done ? 'Reopened.' : 'Marked done.');
            } else if (action.dataset.act === 'category') {
                const category = $('mailCategory').value;
                const everyTime = $('mailCategorySender').checked;
                changed(await patch(email, { category, apply_to_sender: everyTime }),
                    everyTime ? `Moved to ${category}, and remembered for this sender.` : `Moved to ${category}.`);
            } else if (action.dataset.act === 'unsubscribe') {
                const result = await api('/api/newsletters/unsubscribe', { method: 'POST', body: { sender_address: email.sender_address, mute: true } });
                if (result.method === 'link' && result.url) window.open(result.url, '_blank', 'noopener,noreferrer');
                toast(result.detail || 'Done.', result.ok ? 'success' : 'info');
                changed(await api(`/api/emails/${encodeURIComponent(email.id)}`));
            }
        } catch (err) {
            toast(err.message, 'error');
        }
    });

    tools.querySelector('[data-act="snooze"]').addEventListener('change', async (event) => {
        const days = Number(event.target.value);
        if (event.target.value === '') return;
        const until = days ? new Date(Date.now() + days * 86400000).toISOString() : null;
        try {
            changed(await patch(email, { snoozed_until: until }), days ? `Snoozed for ${days} day${days > 1 ? 's' : ''}.` : 'Woken up.');
        } catch (err) {
            toast(err.message, 'error');
        }
    });

    loadThread(root, email);
}
