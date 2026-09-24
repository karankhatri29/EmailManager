// App settings: theme (follows the device unless overridden), accent colour, text size, motion, the
// default timeframe and which numbers / widgets the dashboard shows. They live in this browser's localStorage (per device, no account needed).
//
// The inline script in index.html applies the saved settings before first paint (no flash of the wrong
// theme); this module keeps them in sync afterwards and powers the Settings dialog.

import { KPI_CATALOG, WIDGET_CATALOG, defaultIds } from './dashboard-catalog.js';
import { CATEGORY_COLORS } from './state.js';
import { $, show } from './util.js';

const STORAGE_KEY = 'ep-prefs-v1';

const OPTIONS = {
    theme: ['system', 'light', 'dark'],
    accent: ['violet', 'pink', 'blue', 'mint', 'sunset'],
    density: ['compact', 'comfortable', 'roomy'],
    motion: ['auto', 'full', 'reduced'],
    timeframe: ['Last 1 Day', 'Last 1 Week', 'Last 1 Month'],
};

export const DEFAULTS = {
    theme: 'system',
    accent: 'violet',
    density: 'comfortable',
    motion: 'auto',
    timeframe: 'Last 1 Day',
};

const CATEGORY_VARS = {
    'Urgent / Action Required': '--cat-urgent',
    'Important': '--cat-important',
    'Promotional': '--cat-promotional',
    'General': '--cat-general',
};

const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
let prefs = { ...DEFAULTS, kpis: defaultIds(KPI_CATALOG), widgets: defaultIds(WIDGET_CATALOG) };
let onTimeframe = () => {};

// --- storage ---------------------------------------------------------------------------------------

function load() {
    try {
        const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
        for (const [key, allowed] of Object.entries(OPTIONS)) {
            if (allowed.includes(saved[key])) prefs[key] = saved[key];
        }
        // dashboard switches: keep only ids that still exist, in catalog order
        const keep = (catalog, ids) => catalog.filter((item) => ids.includes(item.id)).map((item) => item.id);
        if (Array.isArray(saved.kpis)) prefs.kpis = keep(KPI_CATALOG, saved.kpis);
        if (Array.isArray(saved.widgets)) prefs.widgets = keep(WIDGET_CATALOG, saved.widgets);
    } catch {
        // storage blocked or corrupted: fall back to the defaults
    }
}

function save() {
    try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
    } catch {
        // private mode / storage full: the settings still apply for this visit
    }
}

export const getPrefs = () => ({ ...prefs });
export const getDashPrefs = () => ({ kpis: [...prefs.kpis], widgets: [...prefs.widgets] });

// --- applying --------------------------------------------------------------------------------------

export const resolvedTheme = () => (prefs.theme === 'system' ? (darkQuery.matches ? 'dark' : 'light') : prefs.theme);

export const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function apply() {
    const root = document.documentElement;
    root.dataset.theme = resolvedTheme();
    root.dataset.themePref = prefs.theme;
    root.dataset.accent = prefs.accent;
    root.dataset.density = prefs.density;
    root.dataset.motion = prefs.motion;

    // Phone browser chrome follows the page colour.
    const [r, g, b] = cssVar('--slate-900').split(/\s+/).map(Number);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta && [r, g, b].every(Number.isFinite)) {
        meta.content = `#${[r, g, b].map((n) => n.toString(16).padStart(2, '0')).join('')}`;
    }

    // Charts read their colours from here.
    for (const [category, variable] of Object.entries(CATEGORY_VARS)) {
        const value = cssVar(variable);
        if (value) CATEGORY_COLORS[category] = value;
    }
    document.dispatchEvent(new CustomEvent('themechange'));
}

function setPref(key, value) {
    if (!OPTIONS[key].includes(value) || prefs[key] === value) return;
    prefs[key] = value;
    save();
    apply();
    syncControls();
    if (key === 'timeframe') onTimeframe(value);
}

// --- dashboard switches ----------------------------------------------------------------------------

function setDashItem(kind, id, on) {
    const [key, catalog] = kind === 'kpi' ? ['kpis', KPI_CATALOG] : ['widgets', WIDGET_CATALOG];
    const next = new Set(prefs[key]);
    if (on) next.add(id); else next.delete(id);
    prefs[key] = catalog.filter((item) => next.has(item.id)).map((item) => item.id);
    save();
    syncDashPrefs();
    document.dispatchEvent(new CustomEvent('dashchange'));
}

function dashGroup(title, kind, catalog, selected) {
    const rows = catalog.map((item) => `
        <label class="pref-row">
            <input type="checkbox" data-dash="${kind}" data-id="${item.id}" ${selected.includes(item.id) ? 'checked' : ''}>
            <span><span class="block text-sm font-semibold text-fg">${item.label}</span>
            <span class="block text-xs text-slate-400">${item.hint}</span></span>
        </label>`).join('');
    return `<div><p class="text-sm font-bold text-fg mb-1">${title}</p>${rows}</div>`;
}

function buildDashPrefs() {
    const host = $('dashPrefs');
    if (!host) return;
    host.innerHTML = dashGroup('Key numbers', 'kpi', KPI_CATALOG, prefs.kpis)
        + dashGroup('Widgets', 'widget', WIDGET_CATALOG, prefs.widgets);
    syncDashPrefs();
}

function syncDashPrefs() {
    document.querySelectorAll('[data-dash]').forEach((box) => {
        const list = box.dataset.dash === 'kpi' ? prefs.kpis : prefs.widgets;
        box.checked = list.includes(box.dataset.id);
    });
    const note = $('kpiCount');
    if (note) {
        const n = prefs.kpis.length;
        note.textContent = n > 9 ? `${n} numbers is a lot. 5 to 9 is easiest to take in at a glance.` : `${n} key number${n === 1 ? '' : 's'} shown.`;
    }
}

// --- dialog ----------------------------------------------------------------------------------------

function syncControls() {
    document.querySelectorAll('[data-pref]').forEach((button) => {
        button.setAttribute('aria-pressed', String(prefs[button.dataset.pref] === button.dataset.value));
    });
    const select = $('prefTimeframe');
    if (select) select.value = prefs.timeframe;
    const hint = $('themeHint');
    if (hint) {
        hint.textContent = prefs.theme === 'system'
            ? `Following your device (currently ${resolvedTheme()}).`
            : `Always ${prefs.theme}, whatever your device is set to.`;
    }
}

function openSettings() {
    syncControls();
    show($('settingsModal'), true);
    $('settingsClose').focus();
}

function closeSettings() {
    show($('settingsModal'), false);
}

export function initSettings({ onTimeframeChange } = {}) {
    if (onTimeframeChange) onTimeframe = onTimeframeChange;

    buildDashPrefs();
    document.addEventListener('change', (event) => {
        const box = event.target.closest('[data-dash]');
        if (box) setDashItem(box.dataset.dash, box.dataset.id, box.checked);
    });

    document.addEventListener('click', (event) => {
        if (event.target.closest('[data-open-settings]')) return openSettings();
        const choice = event.target.closest('[data-pref]');
        if (choice) setPref(choice.dataset.pref, choice.dataset.value);
    });

    // The quick toggle flips between light and dark. "System" (follow the device) is set in Settings.
    $('themeToggle').addEventListener('click', () => setPref('theme', resolvedTheme() === 'dark' ? 'light' : 'dark'));

    $('prefTimeframe').addEventListener('change', (event) => setPref('timeframe', event.target.value));
    $('settingsClose').addEventListener('click', closeSettings);
    $('settingsDone').addEventListener('click', closeSettings);
    $('settingsModal').addEventListener('click', (event) => { if (event.target === $('settingsModal')) closeSettings(); });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeSettings(); });
    $('settingsReset').addEventListener('click', () => {
        const keepTimeframe = prefs.timeframe;
        prefs = { ...DEFAULTS, timeframe: keepTimeframe, kpis: defaultIds(KPI_CATALOG), widgets: defaultIds(WIDGET_CATALOG) };
        save();
        apply();
        syncControls();
        syncDashPrefs();
        document.dispatchEvent(new CustomEvent('dashchange'));
    });

    // Follow the device live while the preference is "system".
    darkQuery.addEventListener('change', () => {
        if (prefs.theme === 'system') {
            apply();
            syncControls();
        }
    });

    syncControls();
}

// Runs as soon as the module loads, before anything renders.
load();
apply();
