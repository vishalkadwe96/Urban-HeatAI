(() => {
  const TOKEN_KEY = 'uha_admin_token';
  const loginPanel = document.getElementById('admin-login-panel');
  const consolePanel = document.getElementById('admin-console');
  const loginForm = document.getElementById('admin-login-form');
  const loginError = document.getElementById('admin-login-error');
  const pageError = document.getElementById('admin-error');
  const loginButton = document.getElementById('admin-login-submit');

  const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);

  function showLoginError(message) {
    loginError.textContent = message;
    loginError.hidden = false;
  }

  function adminFetch(path, token, options = {}) {
    return fetch(path, {
      ...options,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), Authorization: `Bearer ${token}`, ...options.headers }
    }).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
      return data;
    });
  }

  function formatDate(timestamp, milliseconds = false) {
    const value = Number(timestamp);
    if (!value) return '—';
    return new Date(milliseconds ? value : value * 1000).toLocaleString();
  }

  async function loadConsole(token) {
    pageError.hidden = true;
    const [overview, users, activity] = await Promise.all([
      adminFetch('/api/admin/overview', token),
      adminFetch('/api/admin/users?limit=200', token),
      adminFetch('/api/admin/activity?limit=50', token)
    ]);

    document.getElementById('stat-users').textContent = overview.users.toLocaleString();
    document.getElementById('stat-sessions').textContent = overview.active_sessions.toLocaleString();
    document.getElementById('stat-events').textContent = overview.total.toLocaleString();
    document.getElementById('stat-simulations').textContent = overview.simulations.toLocaleString();
    document.getElementById('stat-alerts').textContent = overview.alerts.toLocaleString();
    document.getElementById('users-count').textContent = `${users.users.length} shown`;

    const usersBody = document.getElementById('admin-users');
    usersBody.innerHTML = users.users.length ? users.users.map(user => `
      <tr>
        <td>${escapeHTML(user.user_id)} · ${escapeHTML(user.name)}</td>
        <td>${escapeHTML(user.email)}</td>
        <td><span class="admin-role ${user.role === 'admin' ? '' : 'user'}">${escapeHTML(user.role)}</span></td>
        <td>${escapeHTML(formatDate(user.created_at))}</td>
      </tr>`).join('') : '<tr><td colspan="4" class="admin-empty">No users yet.</td></tr>';

    const activityBody = document.getElementById('admin-activity');
    activityBody.innerHTML = activity.events.length ? activity.events.map(event => `
      <tr>
        <td>${escapeHTML(formatDate(event.timestamp, true))}</td>
        <td>${escapeHTML(event.type)}</td>
        <td>${escapeHTML(event.activity)}</td>
        <td>${escapeHTML(event.city || '—')}</td>
      </tr>`).join('') : '<tr><td colspan="4" class="admin-empty">No activity logged yet.</td></tr>';

    loginPanel.hidden = true;
    consolePanel.hidden = false;
  }

  async function logout() {
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) await adminFetch('/api/auth/logout', token, { method: 'POST' }).catch(() => {});
    localStorage.removeItem(TOKEN_KEY);
    consolePanel.hidden = true;
    loginPanel.hidden = false;
  }

  loginForm.addEventListener('submit', async event => {
    event.preventDefault();
    loginError.hidden = true;
    loginButton.disabled = true;
    loginButton.textContent = 'Signing in…';
    let issuedToken = '';
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: document.getElementById('admin-email').value.trim(),
          password: document.getElementById('admin-password').value
        })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'Invalid admin ID or password');
      issuedToken = data.token;
      if (data.user.role !== 'admin') {
        await adminFetch('/api/auth/logout', issuedToken, { method: 'POST' }).catch(() => {});
        throw new Error('This account does not have admin access.');
      }
      localStorage.setItem(TOKEN_KEY, issuedToken);
      await loadConsole(issuedToken);
    } catch (error) {
      if (issuedToken) localStorage.removeItem(TOKEN_KEY);
      showLoginError(error.message);
    } finally {
      loginButton.disabled = false;
      loginButton.textContent = 'Sign in';
    }
  });

  document.getElementById('admin-refresh').addEventListener('click', () => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) loadConsole(token).catch(error => { pageError.textContent = error.message; pageError.hidden = false; });
  });
  document.getElementById('admin-logout').addEventListener('click', logout);

  const savedToken = localStorage.getItem(TOKEN_KEY);
  if (savedToken) {
    loadConsole(savedToken).catch(() => {
      localStorage.removeItem(TOKEN_KEY);
      loginPanel.hidden = false;
      consolePanel.hidden = true;
    });
  }
})();