// The notification bell: urgent mail, reminders, follow-up nudges, the briefing, the promotions digest and
// newsletter clean-ups all land here. A red dot shows how many are unread; the panel lists them.

import { api } from './api.js';
import { icon } from './icons.js';
import { $, esc, toast } from './util.js';

const POLL_MS = 60000;
const KIND = { urgent: 'Urgent mail', reminder: 'Reminder', followup: 'Follow-up', briefing: 'Briefing', digest: 'Digest', cleanup: 'Clean-up' };
let hooks = { goTools() {}, goActivity() {} };
let timer = null;

const ago = (iso) => {
    const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    if (minutes < 1440) return `${Math.round(minutes / 60)} h ago`;
    return `${Math.round(minutes / 1440)} d ago`;
};

function paint(data) {
    const badge = $('bellBadge');
    badge.textContent = data.unread > 99 ? '99+' : String(data.unread);
    badge.classList.toggle('hidden', data.unread === 0);
    $('bellBtn').setAttribute('aria-label', data.unread ? `Notifications, ${data.unread} unread` : 'Notifications');

    $('bellMarkAll').classList.toggle('hidden', data.unread === 0);
    $('bellList').innerHTML = data.items.length
        ? data.items.map((n) => `
            <li>
                <button type="button" data-note="${n.id}" data-kind="${esc(n.kind)}" class="w-full text-left px-4 py-3 hover:bg-slate-700/40 cursor-pointer ${n.read_at ? 'opacity-60' : ''}">
                    <span class="flex items-center gap-2 text-[0.68rem] font-bold uppercase tracking-wider text-slate-400">
                        ${n.read_at ? '' : '<span class="dot tone-info"></span>'}${esc(KIND[n.kind] || n.kind)}<span class="ml-auto font-normal normal-case tracking-normal text-slate-500">${esc(ago(n.created_at))}</span>
                    </span>
                    <span class="block text-sm font-semibold text-fg mt-0.5">${esc(n.title)}</span>
                    ${n.body ? `<span class="block text-xs text-slate-400 mt-0.5 whitespace-pre-line line-clamp-3">${esc(n.body)}</span>` : ''}
                </button>
            </li>`).join('')
        : '<li class="px-4 py-8 text-center text-sm text-slate-500">You are all caught up.</li>';
}

export async function loadNotifications() {
    try {
        paint(await api('/api/notifications', { params: { limit: 30 } }));
    } catch {
        // signed out or offline: the bell just keeps its last state
    }
}

function setOpen(open) {
    $('bellPanel').classList.toggle('hidden', !open);
    $('bellBtn').setAttribute('aria-expanded', String(open));
    if (open) loadNotifications();
}

export function initNotifications(wiring = {}) {
    hooks = { ...hooks, ...wiring };
    $('bellBtn').innerHTML = `${icon('bell', 'w-5 h-5')}<span id="bellBadge" class="hidden absolute -top-1 -right-1 min-w-[1.1rem] h-[1.1rem] px-1 rounded-full bg-red-500 text-white text-[10px] font-bold leading-[1.1rem] text-center"></span>`;

    $('bellBtn').addEventListener('click', (event) => {
        event.stopPropagation();
        setOpen($('bellPanel').classList.contains('hidden'));
    });
    document.addEventListener('click', (event) => {
        if (!event.target.closest('#bellPanel') && !event.target.closest('#bellBtn')) setOpen(false);
    });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') setOpen(false); });

    $('bellMarkAll').addEventListener('click', async () => {
        try {
            await api('/api/notifications/read', { method: 'POST', body: {} });
            loadNotifications();
        } catch (err) {
            toast(err.message, 'error');
        }
    });
    $('bellList').addEventListener('click', async (event) => {
        const note = event.target.closest('[data-note]');
        if (!note) return;
        try {
            await api('/api/notifications/read', { method: 'POST', body: { ids: [Number(note.dataset.note)] } });
        } catch {
            // marking read is best effort
        }
        setOpen(false);
        if (['followup', 'briefing', 'digest', 'cleanup'].includes(note.dataset.kind)) hooks.goTools(note.dataset.kind);
        else if (note.dataset.kind === 'reminder') hooks.goActivity();
        loadNotifications();
    });

    clearInterval(timer);
    timer = setInterval(loadNotifications, POLL_MS);
}
