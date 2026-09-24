// The single source of truth the views render from.

// Filled from the theme's CSS variables by js/settings.js (they differ between light and dark).
export const CATEGORY_COLORS = {
    'Urgent / Action Required': '#ff6b81',
    'Important': '#34e0a1',
    'Promotional': '#948fbd',
    'General': '#a78bfa',
};

export const state = {
    user: null,
    accounts: [],
    accountFilter: null, // null = every mailbox, otherwise one account id
    emails: [],
    tasks: [], // action items from /api/scheduler, most urgent first
    dashActivities: null, // scheduled tasks around today for the dashboard numbers (null = could not load)
    activities: [],
    selectedEmailId: null,
    calAnchor: new Date(), // any date inside the range the calendar is showing (day / week / month)
    calViewOverride: null, // null = the view is chosen from the screen size; else 'day' | 'week' | 'month'
    timeframe: 'Last 1 Day',
};
