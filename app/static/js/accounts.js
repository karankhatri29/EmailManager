import { api } from './api.js';
import { state } from './state.js';
import { $, esc, toast } from './util.js';

let onChange = () => {};

const PROVIDER_LABEL = { google: 'Gmail' };

function statusDot(account) {
    if (account.status === 'needs_reauth') return '<span class="w-2 h-2 rounded-full bg-red-500 shrink-0" title="Needs reconnecting"></span>';
    if (account.syncing) return '<span class="w-2 h-2 rounded-full bg-blue-400 animate-pulse shrink-0" title="Syncing"></span>';
    return '<span class="w-2 h-2 rounded-full bg-emerald-500 shrink-0" title="Connected"></span>';
}

export function renderAccounts() {
    const list = $('accountList');
    if (!state.accounts.length) {
        list.innerHTML = '<p class="text-xs text-slate-500 px-1">No mailbox connected yet.</p>';
        return;
    }
    list.innerHTML = state.accounts.map((a) => `
        <div class="rounded-lg bg-slate-800/50 border border-slate-700/50 p-2.5">
            <div class="flex items-center gap-2">
                ${statusDot(a)}
                <span class="text-xs text-slate-200 truncate flex-1" title="${esc(a.email_address)}">${esc(a.email_address)}</span>
                <button data-remove="${a.id}" class="text-slate-500 hover:text-red-400 text-xs cursor-pointer" title="Disconnect">✕</button>
            </div>
            <div class="text-[10px] text-slate-500 mt-1 ml-4">${esc(PROVIDER_LABEL[a.provider] || a.provider)}</div>
            ${a.status === 'needs_reauth' ? `
                <div class="mt-2 ml-4 text-[11px] text-red-300">Access expired or revoked.
                    <button data-reconnect class="underline hover:text-white cursor-pointer">Reconnect</button>
                </div>` : ''}
        </div>`).join('');
}

export async function loadAccounts() {
    state.accounts = await api('/api/accounts');
    renderAccounts();
    $('connectPrompt').classList.toggle('hidden', state.accounts.length > 0);
}

// Full-page redirect: Google's consent screen, then back to /?connected=...
const connectGmail = () => { window.location.href = '/api/accounts/connect/google'; };

async function disconnect(id) {
    const account = state.accounts.find((a) => a.id === id);
    const ok = window.confirm(
        `Disconnect ${account.email_address}?\n\nIts stored emails and the calendar items created from them will be deleted.`,
    );
    if (!ok) return;
    try {
        await api(`/api/accounts/${id}`, { method: 'DELETE' });
        toast(`Disconnected ${account.email_address}`, 'success');
        await loadAccounts();
        await onChange();
    } catch (err) {
        toast(err.message, 'error');
    }
}

export function initAccounts(changeCallback) {
    onChange = changeCallback;
    $('connectGmail').addEventListener('click', connectGmail);
    $('connectGmailCta').addEventListener('click', connectGmail);
    $('accountList').addEventListener('click', (event) => {
        const remove = event.target.closest('[data-remove]');
        if (remove) disconnect(Number(remove.dataset.remove));
        if (event.target.closest('[data-reconnect]')) connectGmail();
    });
}
