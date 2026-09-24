import { api } from './api.js';
import { $ } from './util.js';

let mode = 'login';
let onAuthenticated = () => {};

function render() {
    const login = mode === 'login';
    $('authTitle').textContent = login ? 'Welcome back' : 'Create your account';
    $('authSubmit').textContent = login ? 'Sign in' : 'Create account';
    $('authSwitchText').textContent = login ? "Don't have an account?" : 'Already have an account?';
    $('authSwitch').textContent = login ? 'Create one' : 'Sign in';
    $('authPassword').autocomplete = login ? 'current-password' : 'new-password';
    $('authHint').classList.toggle('hidden', login);
    $('authError').classList.add('hidden');
}

async function submit(event) {
    event.preventDefault();
    const button = $('authSubmit');
    const errorEl = $('authError');
    errorEl.classList.add('hidden');
    button.disabled = true;

    try {
        const user = await api(`/api/auth/${mode === 'login' ? 'login' : 'register'}`, {
            method: 'POST',
            body: { email: $('authEmail').value.trim(), password: $('authPassword').value },
        });
        $('authPassword').value = '';
        await onAuthenticated(user);
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove('hidden');
    } finally {
        button.disabled = false;
    }
}

export function initAuth(callback) {
    onAuthenticated = callback;
    $('authForm').addEventListener('submit', submit);
    $('authSwitch').addEventListener('click', () => {
        mode = mode === 'login' ? 'register' : 'login';
        render();
    });
    render();
}

export async function logout() {
    await api('/api/auth/logout', { method: 'POST' });
}
