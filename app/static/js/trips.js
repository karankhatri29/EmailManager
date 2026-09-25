// Trips & tickets: flights, trains, buses, movies, events and hotel stays that the server finds in the user's mail
// (see app/services/bookings.py). This module renders the Trips view (boarding-pass style cards with filters) and
// the "Coming up" card on the home dashboard, and runs the mailbox scan.
//
// Times on tickets are local wall-clock times with no time zone, so they are shown exactly as written and
// counted down against the browser's clock.

import { api } from './api.js';
import { state } from './state.js';
import { $, esc, toast } from './util.js';

const icon = (paths, cls = 'w-6 h-6') =>
    `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.7" stroke="currentColor" class="${cls}" aria-hidden="true">${paths.map((d) => `<path stroke-linecap="round" stroke-linejoin="round" d="${d}" />`).join('')}</svg>`;

const KINDS = {
    flight: { label: 'Flights', one: 'Flight', tone: 'info', paths: ['M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5'] },
    train: { label: 'Trains', one: 'Train', tone: 'warn', paths: ['M7 3h10a2 2 0 0 1 2 2v9a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3V5a2 2 0 0 1 2-2Z', 'M5 11h14', 'M8 21l2-4', 'M16 21l-2-4', 'M9 14h.01', 'M15 14h.01'] },
    bus: { label: 'Buses', one: 'Bus', tone: 'good', paths: ['M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6Z', 'M4 11h16', 'M7.5 18v2', 'M16.5 18v2', 'M8 14.5h.01', 'M16 14.5h.01'] },
    movie: { label: 'Movies', one: 'Movie', tone: 'pink', paths: ['M4 7h16v12H4z', 'M4 7l2-3h3l-2 3', 'M11 7l2-3h3l-2 3', 'M17 7l2-3h1v3'] },
    event: { label: 'Events', one: 'Event', tone: 'violet', paths: ['M16.5 6v.75m0 3v.75m0 3v.75m0 3V18m-9-5.25h5.25M7.5 15h3M3.375 5.25c-.621 0-1.125.504-1.125 1.125v3.026a2.999 2.999 0 0 1 0 5.198v3.026c0 .621.504 1.125 1.125 1.125h17.25c.621 0 1.125-.504 1.125-1.125v-3.026a2.999 2.999 0 0 1 0-5.198V6.375c0-.621-.504-1.125-1.125-1.125H3.375Z'] },
    hotel: { label: 'Hotels', one: 'Hotel', tone: 'teal', paths: ['M3 18v-8a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v8', 'M3 15h18', 'M7 12h4v-2H7z'] },
};
const KIND_ORDER = Object.keys(KINDS);

const view = { kind: null, scope: 'upcoming' };
let hooks = { openEmail() {}, goTrips() {} };
let pollTimer = null;
const added = new Set(); // bookings already put on the Activity calendar this visit
const expanded = new Set(); // rows the user opened; kept across re-renders

// --- dates ---------------------------------------------------------------------------------------

const DAY_MS = 86400000;
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
// "2026-10-12" -> local midnight; "2026-10-12T19:30:00" -> that wall-clock time in the browser's zone.
const parseLocal = (iso) => {
    if (!iso) return null;
    if (iso.length === 10) {
        const [y, m, d] = iso.split('-').map(Number);
        return new Date(y, m - 1, d);
    }
    return new Date(iso);
};

// When the trip is over for "upcoming / past" purposes: a timed event a couple of hours after it starts, an
// all-day one at the end of its (last) day.
function endsAt(b) {
    const start = parseLocal(b.start_at);
    if (!start) return null;
    if (b.all_day) {
        const last = parseLocal(b.end_at) || start;
        return new Date(last.getFullYear(), last.getMonth(), last.getDate(), 23, 59, 59);
    }
    return new Date(start.getTime() + 2 * 3600 * 1000);
}

// Mail with no date we could read: recent ones stay in "upcoming" so they are not lost, older ones go to "past".
function isUpcoming(b) {
    const end = endsAt(b);
    if (end) return end >= new Date();
    return b.email_date ? Date.now() - new Date(b.email_date).getTime() < 30 * DAY_MS : false;
}

function whenText(b) {
    const start = parseLocal(b.start_at);
    if (!start) return 'Date not found in the email';
    const day = start.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: start.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
    const time = b.all_day ? '' : ` · ${start.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`;
    if (b.kind === 'hotel' && b.end_at) {
        const out = parseLocal(b.end_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
        return `${day} → ${out}`;
    }
    return day + time;
}

function relative(b) {
    const start = parseLocal(b.start_at);
    if (!start) return null;
    const days = Math.round((startOfDay(start) - startOfDay(new Date())) / DAY_MS);
    if (days === 0) return { text: b.all_day ? 'Today' : `Today ${start.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`, tone: 'warn' };
    if (days === 1) return { text: 'Tomorrow', tone: 'warn' };
    if (days > 1) {
        const text = days < 14 ? `in ${days} days` : days < 60 ? `in ${Math.round(days / 7)} weeks` : `in ${Math.round(days / 30)} months`;
        return { text, tone: days <= 3 ? 'info' : 'neutral' };
    }
    if (days === -1) return { text: 'Yesterday', tone: 'neutral' };
    return { text: days > -60 ? `${-days} days ago` : `${Math.round(-days / 30)} months ago`, tone: 'neutral' };
}

// --- data ------------------------------------------------------------------------------------------

export const allTrips = () => state.trips || [];
export const upcomingTrips = () => allTrips()
    .filter((b) => b.status !== 'cancelled' && isUpcoming(b))
    .sort((a, b) => String(a.start_at || '9999').localeCompare(String(b.start_at || '9999')));

// Responses can arrive out of order (a slow earlier request answering after a later one), so only the newest
// request may update the screen. Starting a scan also supersedes anything still in flight.
let loadSeq = 0;

export async function loadTrips({ refresh = false } = {}) {
    const seq = ++loadSeq;
    const data = await api('/api/bookings', { params: refresh ? { refresh: true } : {} });
    if (seq !== loadSeq) return data;
    state.trips = data.items;
    state.tripsMeta = { scanning: data.scanning, lastScan: data.last_scan, error: data.error };
    render();
    document.dispatchEvent(new CustomEvent('tripschanged'));
    if (data.scanning) watchScan();
    return data;
}

// Polls the light status endpoint until the mailbox scan finishes, then reloads the trips.
function watchScan() {
    if (pollTimer) return;
    let tries = 0;
    pollTimer = setInterval(async () => {
        tries += 1;
        try {
            const status = await api('/api/bookings/status');
            state.tripsMeta = { scanning: status.scanning, lastScan: status.last_scan, error: status.error };
            if (!status.scanning || tries > 60) {
                clearInterval(pollTimer);
                pollTimer = null;
                await loadTrips();
            } else {
                renderStatus();
            }
        } catch {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }, 3000);
    renderStatus();
}

async function startScan() {
    loadSeq += 1; // an older, still-running load must not overwrite the "scanning" state below
    try {
        const status = await api('/api/bookings/scan', { method: 'POST' });
        state.tripsMeta = { scanning: status.scanning, lastScan: status.last_scan, error: null };
        if (!status.scanning && !state.accounts.length) toast('Connect a mailbox first, then scan for tickets.', 'info');
        renderStatus();
        watchScan();
    } catch (err) {
        toast(err.message, 'error');
    }
}

// --- rendering: the Trips view ---------------------------------------------------------------------

function inScope(b) {
    if (view.scope === 'all') return true;
    return view.scope === 'upcoming' ? b.status !== 'cancelled' && isUpcoming(b) : b.status === 'cancelled' || !isUpcoming(b);
}

function detailChips(b) {
    const chips = [];
    if (b.reference) {
        const label = b.kind === 'flight' || b.kind === 'train' ? 'PNR' : 'Booking ID';
        chips.push(`<button type="button" data-copy="${esc(b.reference)}" class="refchip" title="Copy ${label}"><span class="text-slate-400">${label}</span> <b>${esc(b.reference)}</b>
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-3.5 h-3.5" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 0 1-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75m9 10.5h3.375c.621 0 1.125-.504 1.125-1.125V3.375c0-.621-.504-1.125-1.125-1.125h-9.75c-.621 0-1.125.504-1.125 1.125v3.375m9 10.5H9.375" /></svg></button>`);
    }
    return chips.join('');
}

function card(b) {
    const meta = KINDS[b.kind];
    const rel = b.status === 'cancelled' ? { text: 'Cancelled', tone: 'urgent' } : b.status === 'changed' ? { text: 'Changed', tone: 'warn' } : relative(b);
    const details = b.details.map((d) => `<div class="min-w-0"><dt class="ticket-k">${esc(d.label)}</dt><dd class="ticket-v">${esc(d.value)}</dd></div>`).join('');
    const canCalendar = Boolean(b.start_at);
    const open = expanded.has(b.email_id);
    // One slim row shows only what the trip is and when. Everything else (provider, booking reference, passenger
    // and seat details, actions) stays hidden until the row is expanded.
    return `
    <article class="ticket tone-${meta.tone} ${b.status === 'cancelled' ? 'ticket-cancelled' : ''}" data-booking="${esc(b.email_id)}">
        <button type="button" data-toggle aria-expanded="${open}" class="ticket-row">
            <span class="ticket-ico">${icon(meta.paths, 'w-4 h-4')}</span>
            <span class="ticket-title">${esc(b.title)}</span>
            <span class="ticket-when">${esc(whenText(b))}</span>
            ${rel ? `<span class="chip tone-${rel.tone} ticket-rel">${esc(rel.text)}</span>` : ''}
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="ticket-chev w-4 h-4" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" /></svg>
        </button>
        <div class="ticket-more" ${open ? '' : 'hidden'}>
            <p class="ticket-provider">${meta.one} · ${esc(b.provider)}</p>
            ${b.subtitle ? `<p class="text-xs text-slate-400 mt-0.5 break-words">${esc(b.subtitle)}</p>` : ''}
            ${details ? `<dl class="ticket-details">${details}</dl>` : ''}
            <div class="flex flex-wrap items-center gap-2 mt-3">
                ${detailChips(b)}
            </div>
            <div class="ticket-actions">
                <button type="button" data-trip="open" class="tbtn">Open email</button>
                ${canCalendar ? `<a class="tbtn" href="/api/bookings/calendar?email_id=${encodeURIComponent(b.email_id)}" download>Add to calendar</a>` : ''}
                ${canCalendar && b.status !== 'cancelled' ? `<button type="button" data-trip="activity" class="tbtn" ${added.has(b.email_id) ? 'disabled' : ''}>${added.has(b.email_id) ? 'On your calendar' : 'Add to Activity'}</button>` : ''}
            </div>
        </div>
    </article>`;
}

function renderControls() {
    const pool = allTrips().filter(inScope);
    const counts = Object.fromEntries(KIND_ORDER.map((k) => [k, pool.filter((b) => b.kind === k).length]));
    const chip = (id, label, n) => `<button type="button" data-kind="${id ?? ''}" aria-pressed="${view.kind === id}" class="pchip"><span>${label}</span><span class="pchip-n">${n}</span></button>`;
    $('tripKinds').innerHTML = chip(null, 'All', pool.length)
        + KIND_ORDER.filter((k) => counts[k] || view.kind === k).map((k) => chip(k, KINDS[k].label, counts[k])).join('');
    document.querySelectorAll('[data-scope]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.scope === view.scope)));
}

function renderStatus() {
    const meta = state.tripsMeta || {};
    const scanBtn = $('tripsScan');
    scanBtn.disabled = Boolean(meta.scanning);
    scanBtn.classList.toggle('is-syncing', Boolean(meta.scanning));
    $('tripsScanLabel').textContent = meta.scanning ? 'Scanning your mail...' : 'Scan my mail';
    let text = '';
    if (meta.scanning) text = 'Looking through the last 6 months of mail for tickets and bookings. This can take a minute.';
    else if (meta.error) text = `The last scan hit a problem: ${meta.error}`;
    else if (meta.lastScan) text = `Last scanned ${new Date(meta.lastScan).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}.`;
    $('tripsStatus').textContent = text;
    $('tripsStatus').classList.toggle('text-red-400', Boolean(meta.error) && !meta.scanning);
}

function render() {
    if (!$('view-trips')) return;
    renderControls();
    renderStatus();

    const list = allTrips().filter(inScope).filter((b) => !view.kind || b.kind === view.kind);
    const grid = $('tripsGrid');
    const empty = $('tripsEmpty');
    const scanning = Boolean((state.tripsMeta || {}).scanning);

    if (list.length) {
        const ordered = view.scope === 'past' ? [...list].reverse() : list;
        grid.innerHTML = ordered.map(card).join('');
        empty.classList.add('hidden');
    } else {
        grid.innerHTML = '';
        empty.classList.remove('hidden');
        const none = !allTrips().length;
        $('tripsEmptyTitle').textContent = scanning ? 'Looking for your tickets...' : none ? 'No tickets found yet' : 'Nothing here';
        $('tripsEmptyText').textContent = scanning
            ? 'Flight, train, bus, movie, event and hotel confirmations will show up here as soon as they are found.'
            : none
                ? 'Connect a mailbox and scan it: confirmations from airlines, IRCTC, redBus, BookMyShow, PVR, hotels and more are picked up automatically.'
                : view.scope === 'upcoming' ? 'No upcoming bookings in this filter. Check Past or All.' : 'No bookings in this filter.';
    }
}

// --- rendering: the dashboard card -----------------------------------------------------------------

export function renderTripsCard() {
    const host = $('tripsCardList');
    if (!host) return;
    const next = upcomingTrips().slice(0, 3);
    $('tripsCardMore').textContent = upcomingTrips().length > 3 ? `See all ${upcomingTrips().length}` : 'Open trips';
    host.innerHTML = next.map((b) => {
        const meta = KINDS[b.kind];
        const rel = relative(b);
        return `
        <button type="button" data-open-trip="${esc(b.email_id)}" class="w-full text-left flex items-center gap-3 p-2.5 rounded-2xl hover:bg-slate-700/30 cursor-pointer transition-colors tone-${meta.tone}">
            <span class="focus-icon !w-10 !h-10 !rounded-xl">${icon(meta.paths, 'w-5 h-5')}</span>
            <span class="min-w-0 flex-1">
                <span class="block text-sm font-bold text-fg truncate">${esc(b.title)}</span>
                <span class="block text-xs text-slate-400 truncate">${esc(b.provider)} · ${esc(whenText(b))}</span>
            </span>
            ${rel ? `<span class="chip tone-${rel.tone} shrink-0">${esc(rel.text)}</span>` : ''}
        </button>`;
    }).join('');
}

// --- actions -----------------------------------------------------------------------------------------

async function addToActivity(bookingId) {
    const b = allTrips().find((x) => x.email_id === bookingId);
    if (!b || !b.start_at) return;
    const start = parseLocal(b.start_at);
    const notes = [b.subtitle, b.reference ? `Reference: ${b.reference}` : null, ...b.details.map((d) => `${d.label}: ${d.value}`)].filter(Boolean).join('\n');
    const body = {
        title: `${KINDS[b.kind].one}: ${b.title}`.slice(0, 255),
        notes: notes || null,
        all_day: b.all_day,
        start_at: b.all_day
            ? `${b.start_at}T00:00:00Z`
            : start.toISOString(),
    };
    try {
        await api('/api/activities', { method: 'POST', body });
        added.add(bookingId);
        toast('Added to your Activity calendar.', 'success', 3000);
        document.dispatchEvent(new CustomEvent('taskschanged'));
        render();
    } catch (err) {
        toast(err.message, 'error');
    }
}

// Ticket mails are often older than the inbox window, so the email is fetched by id.
async function openBookingEmail(emailId) {
    try {
        hooks.openEmail(emailId, await api('/api/bookings/email', { params: { email_id: emailId } }));
    } catch (err) {
        toast(err.message, 'error');
    }
}

async function copy(text) {
    try {
        await navigator.clipboard.writeText(text);
        toast(`Copied ${text}`, 'success', 2000);
    } catch {
        toast(`Reference: ${text}`, 'info', 6000);
    }
}

export function initTrips(wiring = {}) {
    hooks = { ...hooks, ...wiring };

    $('view-trips').addEventListener('click', (event) => {
        const kind = event.target.closest('[data-kind]');
        if (kind) { view.kind = kind.dataset.kind || null; return render(); }
        const scope = event.target.closest('[data-scope]');
        if (scope) { view.scope = scope.dataset.scope; view.kind = null; return render(); }
        if (event.target.closest('#tripsScan, [data-scan]')) return startScan();

        const copyBtn = event.target.closest('[data-copy]');
        if (copyBtn) return copy(copyBtn.dataset.copy);
        const cardEl = event.target.closest('[data-booking]');
        if (cardEl && event.target.closest('[data-toggle]')) {
            const id = cardEl.dataset.booking;
            const open = !expanded.has(id);
            if (open) expanded.add(id); else expanded.delete(id);
            cardEl.querySelector('[data-toggle]').setAttribute('aria-expanded', String(open));
            cardEl.querySelector('.ticket-more').hidden = !open;
            return;
        }
        const action = event.target.closest('[data-trip]');
        if (cardEl && action) {
            if (action.dataset.trip === 'open') openBookingEmail(cardEl.dataset.booking);
            if (action.dataset.trip === 'activity') addToActivity(cardEl.dataset.booking);
        }
    });

    // The dashboard "Coming up" card.
    $('card-trips').addEventListener('click', (event) => {
        if (event.target.closest('#tripsCardMore') || event.target.closest('[data-open-trip]')) hooks.goTrips();
    });
}
