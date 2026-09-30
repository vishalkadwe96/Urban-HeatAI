// ============================================================
// Urban Heat AI v2 — Auth (login page + dashboard user chip)
// Session = token in localStorage ('uha_token') + cached user ('uha_user').
// Works on login.html (form logic) and dashboard.html (name + ID chip).
// ============================================================

(function () {
  const AUTH_BASE = window.location.protocol === 'file:' ? 'http://localhost:8000' : '';
  const TOKEN_KEY = 'uha_token', USER_KEY = 'uha_user';

  const getToken = () => localStorage.getItem(TOKEN_KEY);
  function getUser() { try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch (e) { return null; } }
  function saveSession(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  }
  function clearSession() { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); }

  async function authFetch(path, opts) {
    opts = opts || {};
    const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    if (opts.auth && getToken()) headers['Authorization'] = 'Bearer ' + getToken();
    let res;
    try {
      res = await fetch(AUTH_BASE + path, { method: opts.method || 'GET', headers, body: opts.body });
    } catch (e) {
      throw new Error('Cannot reach the server. Is the backend running?');
    }
    let data = null;
    try { data = await res.json(); } catch (e) {}
    if (!res.ok) throw new Error((data && data.detail && typeof data.detail === 'string') ? data.detail : 'Something went wrong (HTTP ' + res.status + ')');
    return data;
  }

  // Verifies the stored token with the server; returns the user or null.
  async function currentUser() {
    if (!getToken()) return null;
    try {
      const data = await authFetch('/api/auth/me', { auth: true });
      localStorage.setItem(USER_KEY, JSON.stringify(data.user));
      return data.user;
    } catch (e) {
      clearSession();
      return null;
    }
  }

  async function logout() {
    try { await authFetch('/api/auth/logout', { method: 'POST', auth: true }); } catch (e) {}
    clearSession();
    window.location.href = 'login.html';
  }

  window.UHAuth = { getToken, getUser, currentUser, logout };

  // ─────────────────────────── Dashboard: require login ───────────────────────────
  // Dashboard is gated: no valid session -> straight to the login page.
  function guardDashboard() {
    if (!document.getElementById('user-chip')) return; // not the dashboard page
    if (!getToken()) { window.location.href = 'login.html'; return; }
    currentUser().then(user => { if (!user) window.location.href = 'login.html'; });
  }

  // ─────────────────────────── Dashboard: user chip ───────────────────────────
  function initUserChip() {
    const slot = document.getElementById('user-chip');
    if (!slot) return;

    function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c])); }

    function render(user) {
      if (!user) {
        slot.innerHTML = '<a href="login.html" class="user-login-link">Login / Register</a>';
        return;
      }
      slot.innerHTML =
        '<div class="user-avatar">' + esc((user.name || '?').trim().charAt(0).toUpperCase()) + '</div>' +
        '<div class="user-meta"><span class="user-name">' + esc(user.name) + '</span>' +
        '<span class="user-id">ID: ' + esc(user.user_id) + '</span></div>' +
        '<button type="button" class="user-logout" id="user-logout-btn" title="Log out">Logout</button>';
      document.getElementById('user-logout-btn').addEventListener('click', logout);
    }

    render(getUser());                       // instant render from cache
    currentUser().then(render);              // then confirm with the server
  }

  // ─────────────────────────── Login page: form logic ───────────────────────────
  function initLoginPage() {
    const form = document.getElementById('auth-form');
    if (!form) return;

    const $ = id => document.getElementById(id);
    const nameField = $('field-name'), nameInput = $('auth-name');
    const emailInput = $('auth-email'), pwInput = $('auth-password');
    const errBox = $('auth-error'), submitBtn = $('auth-submit');
    const subEl = $('auth-sub'), switchEl = $('auth-switch'), pwHint = $('pw-hint');
    let mode = 'login';

    function showError(msg, info) {
      errBox.textContent = msg;
      errBox.classList.toggle('info', !!info);
      errBox.hidden = false;
    }
    function clearError() { errBox.hidden = true; errBox.textContent = ''; }

    function setMode(m) {
      mode = m; clearError();
      const reg = m === 'register';
      $('tab-login').classList.toggle('active', !reg);
      $('tab-register').classList.toggle('active', reg);
      nameField.hidden = !reg;
      pwHint.hidden = !reg;
      pwInput.autocomplete = reg ? 'new-password' : 'current-password';
      pwInput.placeholder = reg ? 'Create a password' : 'Enter your password';
      subEl.textContent = reg ? 'Create your new account to get started.' : 'Enter your email and password to continue.';
      submitBtn.textContent = reg ? 'Create account →' : 'Login →';
      switchEl.innerHTML = reg
        ? 'Already have an account? <a href="#" id="switch-link">Login</a>'
        : 'New here? <a href="#" id="switch-link">Register — create your new account</a>';
      $('switch-link').addEventListener('click', e => { e.preventDefault(); setMode(reg ? 'login' : 'register'); });
    }

    $('tab-login').addEventListener('click', () => setMode('login'));
    $('tab-register').addEventListener('click', () => setMode('register'));
    $('switch-link').addEventListener('click', e => { e.preventDefault(); setMode('register'); });

    $('pw-toggle').addEventListener('click', () => {
      const show = pwInput.type === 'password';
      pwInput.type = show ? 'text' : 'password';
      $('pw-toggle').textContent = show ? 'Hide' : 'Show';
    });

    function goToHome() { window.location.href = 'index.html'; }

    form.addEventListener('submit', async e => {
      e.preventDefault(); clearError();
      [nameInput, emailInput, pwInput].forEach(i => i.classList.remove('invalid'));

      const email = emailInput.value.trim(), pw = pwInput.value, name = nameInput.value.trim();
      if (mode === 'register' && name.length < 2) { nameInput.classList.add('invalid'); return showError('Please enter your name.'); }
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { emailInput.classList.add('invalid'); return showError('Please enter a valid email address.'); }
      if (!pw) { pwInput.classList.add('invalid'); return showError('Please enter your password.'); }
      if (mode === 'register' && pw.length < 8) { pwInput.classList.add('invalid'); return showError('Password must be at least 8 characters.'); }

      submitBtn.disabled = true;
      const label = submitBtn.textContent;
      submitBtn.textContent = 'Please wait…';
      try {
        const payload = mode === 'register' ? { name, email, password: pw } : { email, password: pw };
        const data = await authFetch('/api/auth/' + mode, { method: 'POST', body: JSON.stringify(payload) });
        saveSession(data.token, data.user);
        goToHome();
      } catch (err) {
        showError(err.message);
        submitBtn.disabled = false;
        submitBtn.textContent = label;
      }
    });

    // ── Continue with Google ──
    async function handleGoogleCredential(resp) {
      clearError();
      try {
        const data = await authFetch('/api/auth/google', { method: 'POST', body: JSON.stringify({ credential: resp.credential }) });
        saveSession(data.token, data.user);
        goToHome();
      } catch (err) { showError(err.message); }
    }

    function initGoogle(clientId) {
      const slot = $('google-btn-slot');
      const s = document.createElement('script');
      s.src = 'https://accounts.google.com/gsi/client';
      s.async = true; s.defer = true;
      s.onload = () => {
        google.accounts.id.initialize({ client_id: clientId, callback: handleGoogleCredential });
        slot.innerHTML = '';
        google.accounts.id.renderButton(slot, {
          theme: 'filled_black', size: 'large', shape: 'pill', text: 'continue_with',
          width: Math.min(slot.clientWidth || 366, 400)
        });
      };
      document.head.appendChild(s);   // if it fails to load, the fallback button stays
    }

    $('google-fallback').addEventListener('click', () =>
      showError('Google sign-in is not set up yet. Add GOOGLE_CLIENT_ID to the .env file (see .env.example) and restart the server.', true));

    authFetch('/api/auth/config').then(cfg => { if (cfg.google_client_id) initGoogle(cfg.google_client_id); }).catch(() => {});

    // Already logged in? Skip straight to the dashboard.
    currentUser().then(u => { if (u) goToHome(); });
  }

  document.addEventListener('DOMContentLoaded', () => { guardDashboard(); initUserChip(); initLoginPage(); });
})();
