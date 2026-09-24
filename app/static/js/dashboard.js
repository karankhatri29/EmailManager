import { api } from './api.js';
import { CATEGORY_COLORS, state } from './state.js';
import { providerLabel } from './accounts.js';
import { $, esc, senderName } from './util.js';

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

export async function renderDashboard() {
    $('kpi-total').textContent = state.emails.length;
    $('kpi-urgent').textContent = state.emails.filter((e) => e.category === URGENT).length;
    $('kpi-important').textContent = state.emails.filter((e) => e.category === 'Important').length;

    await renderTasks();
    renderCharts();
}

async function renderTasks() {
    const list = $('taskList');
    let tasks = [];
    try {
        tasks = await api('/api/scheduler', { params: inboxParams() });
    } catch (err) {
        list.innerHTML = `<div class="p-4 text-xs text-red-400 text-center">${esc(err.message)}</div>`;
        return;
    }

    if (!tasks.length) {
        const message = state.accounts.length
            ? 'Nothing needs action in this period. ✨'
            : 'Connect a mailbox to see your action items.';
        list.innerHTML = `<div class="flex items-center justify-center h-full text-slate-500 text-sm p-4 text-center">${message}</div>`;
        return;
    }

    // Which mailbox a task came from only matters when there is more than one.
    const accountOf = (emailId) => state.accounts.find((a) => a.id === Number(emailId.split(':')[0]));
    const showMailbox = state.accounts.length > 1 && state.accountFilter === null;

    list.innerHTML = tasks.map((t) => {
        const hot = t.sort_tier <= 2;
        const mailbox = showMailbox ? accountOf(t.id) : null;
        return `
        <div data-email="${esc(t.id)}" class="p-4 bg-slate-800/40 hover:bg-slate-700/50 rounded-xl cursor-pointer border border-slate-700/50 hover:border-blue-500/30 transition flex flex-col gap-2 group shadow-sm mb-2">
            <div class="flex justify-between items-center text-[11px]">
                <span class="font-bold text-slate-400 tracking-wide truncate max-w-[70%]">👤 ${esc(senderName(t.sender))}</span>
                <span class="px-2 py-0.5 rounded-full font-mono text-[9px] ${hot ? 'bg-red-500/10 border border-red-500/20 text-red-400' : 'bg-slate-700/30 border border-slate-600/30 text-slate-400'}">Tier ${t.sort_tier}</span>
            </div>
            <p class="text-slate-100 font-semibold text-sm leading-snug group-hover:text-blue-400 transition-colors">⚡ ${esc(t.task)}</p>
            <div class="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs pt-1 border-t border-slate-800/60">
                <span class="text-slate-500 whitespace-nowrap">⏱️ Deadline:</span>
                <span class="font-medium ${hot ? 'text-amber-400' : 'text-slate-400'}">${esc(t.deadline)}</span>
                ${mailbox ? `<span class="ml-auto text-[10px] text-slate-500 truncate max-w-[50%]" title="${esc(mailbox.email_address)}">${esc(providerLabel(mailbox))} · ${esc(mailbox.email_address)}</span>` : ''}
            </div>
        </div>`;
    }).join('');
}

export function renderCharts() {
    if (!state.emails.length || $('view-dashboard').classList.contains('hidden') || !window.Plotly) return;

    const layoutBase = {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#94a3b8', family: 'Inter, sans-serif' },
        margin: { t: 20, b: 30, l: 40, r: 15 },
        showlegend: false,
    };

    // Pie: priority distribution
    const counts = {};
    state.emails.forEach((e) => { counts[e.category] = (counts[e.category] || 0) + 1; });
    Plotly.newPlot('pieChart', [{
        values: Object.values(counts),
        labels: Object.keys(counts),
        type: 'pie',
        hole: 0.6,
        marker: { colors: Object.keys(counts).map((c) => CATEGORY_COLORS[c] || '#888') },
        textinfo: 'percent',
        textposition: 'inside',
    }], { ...layoutBase, margin: { t: 0, b: 20, l: 30, r: 10 } }, { displayModeBar: false, responsive: true });

    // Timeline: hourly for one day, daily otherwise
    const hourly = state.timeframe === 'Last 1 Day';
    const categories = Object.keys(CATEGORY_COLORS);
    const buckets = {};
    state.emails.forEach((email) => {
        const d = new Date(email.date);
        const key = hourly
            ? `${String(d.getHours()).padStart(2, '0')}:00`
            : d.toLocaleDateString('en-US', { month: 'short', day: '2-digit' });
        buckets[key] = buckets[key] || Object.fromEntries(categories.map((c) => [c, 0]));
        buckets[key][email.category] += 1;
    });
    const keys = Object.keys(buckets).sort((a, b) => (hourly ? a.localeCompare(b) : new Date(a) - new Date(b)));

    const traces = categories.map((cat) => ({
        x: keys,
        y: keys.map((k) => buckets[k][cat]),
        type: 'scatter',
        mode: 'lines+markers',
        name: cat,
        line: { color: CATEGORY_COLORS[cat], width: 3, shape: 'spline' },
        marker: { size: 6 },
        hovertemplate: `<b>${cat}</b><br>%{x}<br>Count: %{y}<extra></extra>`,
    }));

    // Show only as many x labels as fit (about 56px each) so they never collide on narrow screens.
    const fits = Math.max(2, Math.floor($('lineChart').clientWidth / 56));
    const step = Math.ceil(keys.length / fits);

    Plotly.newPlot('lineChart', traces, {
        ...layoutBase,
        margin: { t: 20, b: 40, l: 36, r: 12 },
        xaxis: { showgrid: false, tickmode: 'array', tickvals: keys.filter((_, i) => i % step === 0), tickangle: 0 },
        yaxis: { showgrid: true, gridcolor: '#1e293b', zeroline: false },
        hovermode: 'closest',
    }, { displayModeBar: false, responsive: true });
}

// --- email drawer (read one email: summary + original) ------------------------------------------

export function openEmailDrawer(emailId) {
    const email = state.emails.find((e) => e.id === emailId);
    if (!email) return;
    state.selectedEmailId = emailId;

    const account = state.accounts.find((a) => a.id === email.account_id);
    const messageId = email.id.split(':').slice(1).join(':');
    const gmailLink = account && account.provider === 'google'
        ? `https://mail.google.com/mail/u/${encodeURIComponent(account.email_address)}/#all/${encodeURIComponent(messageId)}`
        : null;

    $('drawerContent').innerHTML = `
        <div class="p-6 border-b border-slate-700/50 bg-slate-800/30 shrink-0">
            <h2 class="text-lg font-bold text-white leading-tight mb-2">${esc(email.subject)}</h2>
            <p class="text-slate-400 text-sm"><strong>From:</strong> ${esc(email.sender)}</p>
            <p class="text-slate-500 text-xs mt-1">${esc(new Date(email.date).toLocaleString())}${account ? ` · ${esc(providerLabel(account))} · ${esc(account.email_address)}` : ''}</p>
            <div class="mt-3 flex items-center gap-2 text-[11px]">
                <span class="px-2 py-0.5 rounded-full border border-slate-600/50 text-slate-300">${esc(email.category)}</span>
                ${gmailLink ? `<a href="${esc(gmailLink)}" target="_blank" rel="noopener noreferrer" class="text-blue-400 hover:text-blue-300 underline">Open in Gmail ↗</a>` : ''}
            </div>
        </div>
        <div class="flex-1 overflow-y-auto p-6 space-y-5">
            <div class="bg-blue-900/20 border border-blue-500/30 rounded-lg p-4">
                ${email.summary
                    ? `<div class="space-y-1">${email.summary}</div>`
                    : '<span class="text-slate-400 text-sm">No AI summary for this email (yet).</span>'}
            </div>
            <div class="border border-slate-700/50 rounded-lg bg-slate-900/50">
                <div class="p-3 bg-slate-800/50 border-b border-slate-700/50 rounded-t-lg">
                    <span class="text-xs font-bold text-slate-400 uppercase">📄 Original message</span>
                </div>
                <div class="p-4 text-sm text-slate-300 font-mono whitespace-pre-wrap break-words">${esc(email.body)}</div>
            </div>
        </div>`;

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
    window.addEventListener('resize', renderCharts);
}
