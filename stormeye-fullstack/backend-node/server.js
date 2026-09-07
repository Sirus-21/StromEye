// StormEye AI — Node/Express backend
// Serves live tropical cyclone data from the US National Hurricane Center.
//
// NOTE ON API KEYS: none are needed here. NHC's CurrentStorms.json is a free,
// public, unauthenticated government feed. There is no official public API
// for JTWC (Western Pacific / Indian Ocean / Southern Hemisphere storms) —
// no key exists to request. This backend is honest about that limitation:
// storms outside NHC's coverage (Atlantic + Eastern/Central Pacific) are
// simply not returned by /api/storms.

const express = require('express');
const cors = require('cors');
require('dotenv').config();

const app = express();
const PORT = process.env.PORT || 5000;
const CORS_ORIGIN = process.env.CORS_ORIGIN || '*';
const CACHE_TTL_MS = Number(process.env.CACHE_TTL_MS || 5 * 60 * 1000); // 5 min
const NHC_URL = 'https://www.nhc.noaa.gov/CurrentStorms.json';

app.use(cors({ origin: CORS_ORIGIN }));

// ---- tiny in-memory cache (avoids hammering NHC on every page load) ----
let cache = { data: null, fetchedAt: 0 };

// ---- Saffir-Simpson-ish category classifier ----
// classification codes from NHC: TD, TS, HU, PTC, SD, SS, EX, LO, DB, WV
function toCategory(classification, windKt) {
  const c = (classification || '').toUpperCase();
  if (['TD', 'LO', 'DB', 'WV'].includes(c)) return 'TD';
  if (['TS', 'SS', 'STS'].includes(c)) return 'TS';

  // HU, PTC, EX or unknown — fall back to wind-speed thresholds (knots)
  if (windKt >= 137) return 5;
  if (windKt >= 113) return 4;
  if (windKt >= 96) return 3;
  if (windKt >= 83) return 2;
  if (windKt >= 64) return 1;
  if (windKt >= 34) return 'TS';
  return 'TD';
}

function basinFromId(id) {
  const prefix = (id || '').slice(0, 2).toLowerCase();
  if (prefix === 'al') return 'Atlantic';
  if (prefix === 'ep') return 'Eastern Pacific';
  if (prefix === 'cp') return 'Central Pacific';
  return 'Unknown basin';
}

function normalizeStorm(raw) {
  const windKt = Number(raw.intensity) || 0;
  const windKmh = Math.round(windKt * 1.852);
  const pressure = Number(raw.pressure) || null;
  const cat = toCategory(raw.classification, windKt);

  return {
    id: raw.id,
    name: raw.name,
    lat: raw.latitudeNumeric,
    lng: raw.longitudeNumeric,
    cat,
    wind: windKmh,
    windKt,
    pressure,
    basin: basinFromId(raw.id),
    classification: raw.classification,
    movementDir: raw.movementDir,
    movementSpeedKmh: raw.movementSpeed != null ? Math.round(raw.movementSpeed * 1.852) : null,
    lastUpdate: raw.lastUpdate,
    advisoryUrl: raw.publicAdvisory ? raw.publicAdvisory.url : null,
    source: 'NHC'
  };
}

async function fetchStorms() {
  const now = Date.now();
  if (cache.data && now - cache.fetchedAt < CACHE_TTL_MS) {
    return { storms: cache.data, cached: true, fetchedAt: cache.fetchedAt };
  }

  const res = await fetch(NHC_URL, {
    headers: { 'User-Agent': 'StormEyeAI/1.0 (educational project)' }
  });
  if (!res.ok) throw new Error(`NHC feed returned ${res.status}`);
  const json = await res.json();

  const storms = (json.activeStorms || [])
    .filter(s => s.latitudeNumeric != null && s.longitudeNumeric != null)
    .map(normalizeStorm);

  cache = { data: storms, fetchedAt: now };
  return { storms, cached: false, fetchedAt: now };
}

app.get('/api/storms', async (req, res) => {
  try {
    const { storms, cached, fetchedAt } = await fetchStorms();
    res.json({
      ok: true,
      count: storms.length,
      cached,
      fetchedAt: new Date(fetchedAt).toISOString(),
      coverage: 'NHC: Atlantic + Eastern/Central Pacific only',
      storms
    });
  } catch (err) {
    res.status(502).json({
      ok: false,
      error: 'Could not reach the NHC feed.',
      detail: err.message
    });
  }
});

app.get('/health', (req, res) => res.json({ ok: true }));

app.listen(PORT, () => {
  console.log(`StormEye backend (Node) listening on http://localhost:${PORT}`);
  console.log(`  -> GET /api/storms`);
});
