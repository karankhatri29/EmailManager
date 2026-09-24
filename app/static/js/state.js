// The single source of truth the views render from.

export const CATEGORY_COLORS = {
    'Urgent / Action Required': '#ef4444',
    'Important': '#10b981',
    'Promotional': '#64748b',
    'General': '#3b82f6',
};

export const state = {
    user: null,
    accounts: [],
    accountFilter: null, // null = every mailbox, otherwise one account id
    emails: [],
    activities: [],
    selectedEmailId: null,
    month: new Date(new Date().getFullYear(), new Date().getMonth(), 1), // month shown in the calendar
    timeframe: 'Last 1 Day',
};
