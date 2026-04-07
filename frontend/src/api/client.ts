const BASE = '/api';
const KEY_STORAGE = 'pmw_api_key';

export function getApiKey(): string {
  return localStorage.getItem(KEY_STORAGE) || '';
}

export function setApiKey(key: string): void {
  localStorage.setItem(KEY_STORAGE, key);
}

export function clearApiKey(): void {
  localStorage.removeItem(KEY_STORAGE);
}

function authHeaders(): Record<string, string> {
  const key = getApiKey();
  return key ? { 'X-API-Key': key } : {};
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    clearApiKey();
    window.location.reload();
    throw new Error('Unauthorized');
  }
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: { ...authHeaders() } });
  return handleResponse<T>(res);
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(res);
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(res);
}

// Types matching FastAPI response models
export interface Overview {
  total_pnl: number; daily_pnl: number; open_positions: number;
  total_exposure: number; trades_today: number; win_rate: number;
  bankroll: number; paper_mode: boolean; system_status: string;
}

export interface Opportunity {
  id: number; market_id: string; city: string | null; question: string | null;
  our_p: number; market_p: number; edge: number; direction: string;
  confidence: number; forecast_source: string; detected_at: string;
  traded: boolean; skip_reason: string | null;
}

export interface Position {
  market_id: string; direction: string; entry_price: number; size_usdc: number;
  current_price: number | null; unrealized_pnl: number; city: string;
  event_id: string; entry_time: string; peak_pnl_pct: number;
}

export interface Trade {
  id: number; market_id: string | null; question: string | null;
  token_id: string | null; size_usdc: number | null; fill_price: number | null;
  status: string; pnl_usdc: number | null; settlement_result: string | null;
  placed_at: string | null; exit_reason: string | null;
}

export interface WeatherStation {
  station_id: string; city_name: string; country_code: string;
  last_temp_c: number | null; last_report_at: string | null;
  is_stale: boolean; reliability_score: number | null;
}

export interface CalibrationBin {
  bin_lower: number; bin_upper: number; predicted_mean: number;
  observed_rate: number; count: number;
}

export interface ConfigItem { key: string; value: string; updated_at: string | null; }
export interface CityMapping { id: number | null; city_pattern: string; station_id: string; priority: number; }
export interface SystemEvent {
  id: number; event_type: string; severity: string;
  message: string | null; details: Record<string, unknown> | null; created_at: string;
}

export interface JobStatus {
  name: string;
  interval_seconds: number;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_duration_ms: number | null;
  last_error: string | null;
  last_error_at: string | null;
  successes: number;
  failures: number;
  healthy: boolean;
}

export interface KillSwitchState { paused: boolean; available: boolean; }

// API functions
export const api = {
  overview: () => fetchJson<Overview>('/overview'),
  opportunities: (traded?: boolean) => fetchJson<Opportunity[]>(`/opportunities${traded !== undefined ? `?traded=${traded}` : ''}`),
  positions: () => fetchJson<Position[]>('/positions'),
  history: (limit = 100) => fetchJson<Trade[]>(`/history?limit=${limit}`),
  weather: () => fetchJson<WeatherStation[]>('/weather'),
  calibration: () => fetchJson<CalibrationBin[]>('/calibration'),
  config: () => fetchJson<ConfigItem[]>('/config'),
  updateConfig: (key: string, value: string) => putJson('/config', { key, value }),
  cities: () => fetchJson<CityMapping[]>('/cities'),
  updateCity: (mapping: CityMapping) => putJson('/cities', mapping),
  events: (severity?: string) => fetchJson<SystemEvent[]>(`/events${severity ? `?severity=${severity}` : ''}`),
  jobs: () => fetchJson<JobStatus[]>('/jobs'),
  killSwitch: () => fetchJson<KillSwitchState>('/kill_switch'),
  setKillSwitch: (paused: boolean) => postJson<KillSwitchState>('/kill_switch', { paused }),

  validateKey: async (key: string): Promise<boolean> => {
    const res = await fetch(`${BASE}/overview`, { headers: { 'X-API-Key': key } });
    return res.status !== 401;
  },
};
