// Small helpers shared by every view.

export const $ = (id) => document.getElementById(id);

// Everything that comes from an email or a user goes through esc() before it touches innerHTML.
export function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
}

export const pad = (n) => String(n).padStart(2, '0');

// YYYY-MM-DD in the browser's local time zone
export const localYmd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

export const timeOf = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

export function senderName(sender) {
    return String(sender || '').split('<')[0].trim().replace(/^"|"$/g, '') || sender;
}

const TOAST_STYLES = {
    info: 'border-blue-500/40 text-blue-200',
    success: 'border-emerald-500/40 text-emerald-200',
    error: 'border-red-500/40 text-red-200',
};

export function toast(message, kind = 'info', ms = 5000) {
    const el = document.createElement('div');
    el.className = `px-4 py-3 rounded-lg bg-slate-800 border text-sm shadow-xl max-w-sm ${TOAST_STYLES[kind] || TOAST_STYLES.info}`;
    el.textContent = message;
    $('toasts').appendChild(el);
    setTimeout(() => el.remove(), ms);
}

export function show(el, visible, displayClass = 'flex') {
    el.classList.toggle('hidden', !visible);
    if (displayClass) el.classList.toggle(displayClass, visible);
}
