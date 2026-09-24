// Everything the home dashboard can show. Settings lists these as switches; kpis.js renders the ones that are on.
//
// The defaults follow the usual dashboard guidance: a handful (5 to 9) of numbers that each lead to an
// action, the most important one first, and thresholds that colour a number good / warning / bad.
//
// Only metrics that can be computed from data we already store are listed. Response time and
// "awaiting a reply" need sent-mail tracking on the server and are not offered yet.

export const KPI_CATALOG = [
    { id: 'needsAction', label: 'Needs action', hint: 'Emails ranked as urgent', default: true },
    { id: 'overdue', label: 'Overdue', hint: 'Scheduled tasks past their date and not done', default: true },
    { id: 'dueToday', label: 'Due today', hint: 'Tasks scheduled for today that are still open', default: true },
    { id: 'next7', label: 'Next 7 days', hint: 'Open tasks coming up this week', default: true },
    { id: 'completion', label: 'On-time completion', hint: 'Share of this week\'s scheduled tasks you finished', default: true },
    { id: 'emails', label: 'Emails received', hint: 'In the chosen timeframe, with a daily average', default: true },
    { id: 'important', label: 'Important mail', hint: 'Emails ranked as important', default: false },
    { id: 'unscheduled', label: 'Unscheduled backlog', hint: 'Tasks that still need a date', default: false },
    { id: 'promo', label: 'Promotional share', hint: 'How much of your mail is noise', default: false },
    { id: 'busiestHour', label: 'Busiest hour', hint: 'When most of your mail arrives', default: false },
];

export const WIDGET_CATALOG = [
    { id: 'focus', label: 'Do this first', hint: 'The one thing to do next, with Open and Done', default: true },
    { id: 'trips', label: 'Trips and tickets', hint: 'Your next flights, trains, shows and hotel stays, found in your mail', default: true },
    { id: 'inbox', label: 'Inbox explorer', hint: 'Filter your mail by priority, time, sender and search', default: true },
    { id: 'senders', label: 'Top senders', hint: 'Who fills your inbox the most (tap one to filter)', default: true },
    { id: 'mailboxes', label: 'Mailbox breakdown', hint: 'Compare your connected mailboxes (needs 2 or more)', default: true },
];

export const defaultIds = (catalog) => catalog.filter((item) => item.default).map((item) => item.id);
