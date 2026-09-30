// ============================================================
// Urban Heat AI v2 — 10-Day Forecast (NEW feature, additive)
// Uses currentCity (set in dashboard.js) + API_BASE (api.js).
// Renders: LST line chart (Chart.js, already loaded) + a table with
// every dashboard feature projected forward: LST, NDVI, heat risk,
// rainfall, landslide risk.
// ============================================================

let forecastChart = null;

function _riskBadgeClass(level) {
  return 'forecast-risk-badge ' + String(level || '').toLowerCase().replace(/\s+/g, '-');
}

function _localISO(d) {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function _todayISO() {
  return _localISO(new Date());
}

function _maxStartISO(daysAhead) {
  const d = new Date();
  d.setDate(d.getDate() + daysAhead);
  return _localISO(d);
}

function initForecastDatePicker() {
  const input = document.getElementById('forecast-start-date');
  if (!input) return;
  input.min = _todayISO();
  input.max = _maxStartISO(6); // backend caps start_date at today+6
  if (!input.value) input.value = _todayISO();
}

async function runForecast() {
  const city = window.currentCity || currentCity;
  const statusEl = document.getElementById('forecast-status');
  const cityLabelEl = document.getElementById('forecast-modal-city');
  const chartWrap = document.getElementById('forecast-chart-wrap');
  const tableWrap = document.getElementById('forecast-table-wrap');
  const noteEl = document.getElementById('forecast-model-note');

  if (cityLabelEl) cityLabelEl.textContent = (typeof cityDisplayName === 'function') ? cityDisplayName(city) : city;

  const startDate = document.getElementById('forecast-start-date')?.value || '';
  const days = document.getElementById('forecast-days')?.value || '10';

  statusEl.className = 'forecast-status';
  statusEl.textContent = '⏳ Running 10-day forecast model…';
  chartWrap.style.display = 'none';
  tableWrap.innerHTML = '';
  noteEl.textContent = '';

  const qs = new URLSearchParams({ city, days });
  if (startDate) qs.set('start_date', startDate);

  let data = null;
  try {
    const res = await fetch(API_BASE + '/forecast/predict?' + qs.toString());
    data = await res.json();
  } catch (e) {
    statusEl.className = 'forecast-status err';
    statusEl.textContent = '⚠️ Could not reach the forecast service. Is the backend running?';
    return;
  }

  if (!data || data.error) {
    statusEl.className = 'forecast-status err';
    statusEl.textContent = '⚠️ ' + (data?.error || 'Forecast failed — please try again.');
    return;
  }

  const rows = data.forecast || [];
  if (!rows.length) {
    statusEl.className = 'forecast-status err';
    statusEl.textContent = '⚠️ No forecast data returned for this city/date range.';
    return;
  }

  statusEl.textContent = data.model_ready
    ? `✅ ML forecast (RandomForest, 5-fold validated) · anchored to today's live LST ${data.generated_from?.today_lst_c}°C (${data.generated_from?.calibration_source || 'baseline'})`
    : `ℹ️ Using seasonal-formula fallback (ML model files not loaded on this server) · anchored to today's LST ${data.generated_from?.today_lst_c}°C`;

  // ---- Chart: LST trend across the forecast window ----
  chartWrap.style.display = 'block';
  const ctx = document.getElementById('forecast-chart').getContext('2d');
  const labels = rows.map(r => r.date.slice(5)); // MM-DD
  const lstData = rows.map(r => r.lst_c);
  const rainData = rows.map(r => r.rainfall_mm);

  if (forecastChart) forecastChart.destroy();
  forecastChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Predicted LST (°C)',
          data: lstData,
          borderColor: '#f97316',
          backgroundColor: 'rgba(249,115,22,0.12)',
          fill: true,
          tension: 0.35,
          yAxisID: 'y',
          pointRadius: 3,
        },
        {
          label: 'Rainfall (mm)',
          data: rainData,
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56,189,248,0.08)',
          fill: false,
          tension: 0.35,
          yAxisID: 'y1',
          pointRadius: 2,
          borderDash: [4, 3],
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: '#c9d1e3' } } },
      scales: {
        x: { ticks: { color: '#8891a5' }, grid: { color: 'rgba(255,255,255,0.05)' } },
        y: {
          position: 'left', ticks: { color: '#f97316' },
          grid: { color: 'rgba(255,255,255,0.05)' }, title: { display: true, text: '°C', color: '#f97316' },
        },
        y1: {
          position: 'right', ticks: { color: '#38bdf8' }, grid: { drawOnChartArea: false },
          title: { display: true, text: 'mm', color: '#38bdf8' },
        },
      },
    },
  });

  // ---- Table: every dashboard feature, per day ----
  const tableRows = rows.map(r => `
    <tr>
      <td>${r.date}</td>
      <td>Day +${r.day_offset}</td>
      <td>${r.lst_c}°C</td>
      <td>${r.ndvi}</td>
      <td><span class="${_riskBadgeClass(r.heat_risk_level)}">${r.heat_risk_level}</span></td>
      <td>${r.rainfall_mm} mm</td>
      <td><span class="${_riskBadgeClass(r.landslide_risk_level)}">${r.landslide_risk_level}</span></td>
    </tr>
  `).join('');

  tableWrap.innerHTML = `
    <table class="forecast-table">
      <thead>
        <tr>
          <th>Date</th><th>Horizon</th><th>LST</th><th>NDVI</th>
          <th>Heat Risk</th><th>Rainfall</th><th>Landslide Risk</th>
        </tr>
      </thead>
      <tbody>${tableRows}</tbody>
    </table>
  `;

  noteEl.textContent = '🔬 LST / NDVI / Rainfall are predicted by a trained ML model (RandomForest, ' +
    '5-fold walk-forward validated). Heat Risk and Landslide Risk are derived from those predictions ' +
    'using the same formulas the live dashboard uses today, so risk levels stay consistent.';

  if (typeof logActivity === 'function') {
    logActivity('forecast', `10-day forecast run for ${cityDisplayName ? cityDisplayName(city) : city}`, startDate + ' / ' + days + 'd');
  }
}

function openForecastModal() {
  initForecastDatePicker();
  document.getElementById('forecast-modal')?.classList.add('open');
  runForecast();
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('forecast-nav-btn')?.addEventListener('click', openForecastModal);
  document.getElementById('forecast-modal-close')?.addEventListener('click', () =>
    document.getElementById('forecast-modal')?.classList.remove('open'));
  document.getElementById('forecast-modal')?.addEventListener('click', (e) => {
    if (e.target.id === 'forecast-modal') e.target.classList.remove('open');
  });
  document.getElementById('forecast-run-btn')?.addEventListener('click', runForecast);
  initForecastDatePicker();
});
