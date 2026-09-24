// App settings: theme (follows the device unless overridden), accent colour, text size, motion and the
// default timeframe. They live in this browser's localStorage (per device, no account needed).
//
// The inline script in index.html applies the saved settings before first paint (no flash of the wrong
// theme); this module keeps them in sync afterwards and powers the Settings dialog.

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
let prefs = { ...DEFAULTS };
let onTimeframe = () => {};

// --- storage ---------------------------------------------------------------------------------------

function load() {
    try {
        const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
        for (const [key, allowed] of Object.entries(OPTIONS)) {
            if (allowed.includes(saved[key])) prefs[key] = saved[key];
        }
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
        prefs = { ...DEFAULTS, timeframe: keepTimeframe };
        save();
        apply();
        syncControls();
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
