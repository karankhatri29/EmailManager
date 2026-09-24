// Thin fetch wrapper: JSON in/out, readable errors, and a hook for "session expired".

export class ApiError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}

let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

// FastAPI validation errors are arrays of {msg, loc}; everything else is a string.
function readableDetail(detail) {
    if (Array.isArray(detail)) {
        return detail.map((d) => String(d.msg || '').replace(/^Value error, /, '')).join('; ');
    }
    return typeof detail === 'string' ? detail : '';
}

export async function api(path, { method = 'GET', body, params } = {}) {
    const query = params ? `?${new URLSearchParams(params)}` : '';
    const res = await fetch(path + query, {
        method,
        credentials: 'same-origin',
        headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    if (res.status === 401 && !path.startsWith('/api/auth/')) {
        onUnauthorized();
        throw new ApiError(401, 'Please sign in again.');
    }
    if (res.status === 204) return null;

    const data = await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(res.status, readableDetail(data && data.detail) || res.statusText);
    return data;
}
