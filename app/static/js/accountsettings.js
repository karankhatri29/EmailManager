// The "Account" part of the Settings dialog. Unlike the appearance settings (saved on this device), these
// live on the server because the server acts on them while you are away: the morning briefing, the evening
// promotions digest, reminder emails, follow-up nudges and the automatic newsletter clean-up.

import { api } from './api.js';
import { $, toast } from './util.js';

const FIELDS = {
    timezone: { el: 'setTz', type: 'text' },
    briefing_enabled: { el: 'setBriefing', type: 'bool' },
    briefing_hour: { el: 'setBriefingHour', type: 'int' },
    digest_enabled: { el: 'setDigest', type: 'bool' },
    digest_hour: { el: 'setDigestHour', type: 'int' },
    urgent_alerts: { el: 'setAlerts', type: 'bool' },
    reminder_emails: { el: 'setReminders', type: 'bool' },
    followup_days: { el: 'setFollowup', type: 'int' },
    auto_cleanup: { el: 'setCleanup', type: 'bool' },
    cleanup_months: { el: 'setCleanupMonths', type: 'int' },
};

const hourLabel = (h) => `${String(h).padStart(2, '0')}:00`;

function fillOptions() {
    for (const id of ['setBriefingHour', 'setDigestHour']) {
        $(id).innerHTML = Array.from({ length: 24 }, (_, h) => `<option value="${h}">${hourLabel(h)}</option>`).join('');
    }
    const zones = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : ['UTC'];
    $('tzList').innerHTML = zones.map((z) => `<option value="${z}"></option>`).join('');
}

function show(settings) {
    for (const [name, { el, type }] of Object.entries(FIELDS)) {
        const input = $(el);
        if (type === 'bool') input.checked = Boolean(settings[name]);
        else input.value = settings[name];
    }
    $('setEmailNote').classList.toggle('hidden', settings.email_configured);
}

export async function loadAccountSettings() {
    try {
        show(await api('/api/settings'));
        $('accountSettings').classList.remove('hidden');
    } catch {
        $('accountSettings').classList.add('hidden'); // signed out
    }
}

async function save(name) {
    const { el, type } = FIELDS[name];
    const input = $(el);
    const value = type === 'bool' ? input.checked : type === 'int' ? Number(input.value) : input.value.trim();
    const status = $('setStatus');
    try {
        show(await api('/api/settings', { method: 'PUT', body: { [name]: value } }));
        status.textContent = 'Saved.';
        setTimeout(() => { if (status.textContent === 'Saved.') status.textContent = ''; }, 2000);
    } catch (err) {
        status.textContent = '';
        toast(err.message, 'error');
        loadAccountSettings(); // put the control back to what the server has
    }
}

export function initAccountSettings() {
    fillOptions();
    for (const name of Object.keys(FIELDS)) $(FIELDS[name].el).addEventListener('change', () => save(name));
    $('setTzDetect').addEventListener('click', () => {
        $('setTz').value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
        save('timezone');
    });
    document.addEventListener('settingsopen', loadAccountSettings);
}
