import { api } from './api.js';
import { state } from './state.js';
import { $, esc, toast } from './util.js';

let onChange = () => {};

let onFilterChange = () => {};

const PROVIDER_LABEL = { google: 'Gmail', microsoft: 'Outlook' };
export const providerLabel = (account) => PROVIDER_LABEL[account.provider] || account.provider;

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
            <div class="text-[10px] text-slate-500 mt-1 ml-4">${esc(providerLabel(a))}</div>
            ${a.status === 'needs_reauth' ? `
                <div class="mt-2 ml-4 text-[11px] text-red-300">Access expired or revoked.
                    <button data-reconnect="${esc(a.provider)}" class="underline hover:text-white cursor-pointer">Reconnect</button>
                </div>` : ''}
        </div>`).join('');
}

// "All mailboxes" plus one chip per mailbox; only shown once there is something to choose between.
export function renderAccountFilter() {
    const bar = $('accountFilter');
    const show = state.accounts.length > 1;
    bar.classList.toggle('hidden', !show);
    bar.classList.toggle('flex', show);
    if (!show) return;

    const chip = (id, label, title) => {
        const active = state.accountFilter === id;
        return `<button data-account-filter="${id ?? ''}" aria-pressed="${active}" title="${esc(title)}"
            class="px-3 py-1.5 rounded-full text-xs font-medium border transition cursor-pointer ${active
                ? 'bg-blue-600 border-blue-500 text-white'
                : 'bg-slate-800/60 border-slate-700 text-slate-300 hover:bg-slate-700'}">${esc(label)}</button>`;
    };
    bar.innerHTML = chip(null, 'All mailboxes', 'Show mail from every mailbox') + state.accounts
        .map((a) => chip(a.id, a.email_address, `${providerLabel(a)} mailbox`)).join('');
}

export async function loadAccounts() {
    state.accounts = await api('/api/accounts');
    // A disconnected mailbox can no longer be the active filter.
    if (state.accountFilter !== null && !state.accounts.some((a) => a.id === state.accountFilter)) {
        state.accountFilter = null;
    }
    renderAccounts();
    renderAccountFilter();
    $('connectPrompt').classList.toggle('hidden', state.accounts.length > 0);
}

// Full-page redirect: the provider's consent screen, then back to /?connected=...
const CONNECT_URL = { google: '/api/accounts/connect/google', microsoft: '/api/accounts/connect/microsoft' };
const connect = (provider) => { window.location.href = CONNECT_URL[provider]; };

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

export function initAccounts(changeCallback, filterCallback) {
    onChange = changeCallback;
    onFilterChange = filterCallback;
    $('connectGmail').addEventListener('click', () => connect('google'));
    $('connectGmailCta').addEventListener('click', () => connect('google'));
    $('connectOutlook').addEventListener('click', () => connect('microsoft'));
    $('connectOutlookCta').addEventListener('click', () => connect('microsoft'));
    $('accountFilter').addEventListener('click', (event) => {
        const chip = event.target.closest('[data-account-filter]');
        if (!chip) return;
        state.accountFilter = chip.dataset.accountFilter === '' ? null : Number(chip.dataset.accountFilter);
        renderAccountFilter();
        onFilterChange();
    });
    $('accountList').addEventListener('click', (event) => {
        const remove = event.target.closest('[data-remove]');
        if (remove) disconnect(Number(remove.dataset.remove));
        const reconnect = event.target.closest('[data-reconnect]');
        if (reconnect) connect(reconnect.dataset.reconnect);
    });
}
