import { useQuery } from '@tanstack/react-query';
import { api, type WeatherStation } from '../api/client';

type Freshness = 'fresh' | 'warm' | 'stale' | 'unknown';

function freshness(station: WeatherStation): Freshness {
  if (!station.last_report_at) return 'unknown';
  const ageSeconds = (Date.now() - new Date(station.last_report_at).getTime()) / 1000;
  if (ageSeconds < 3600) return 'fresh';         // < 1h
  if (ageSeconds < 10800) return 'warm';          // < 3h
  return 'stale';                                 // >= 3h
}

function timeAgo(iso: string | null): string {
  if (!iso) return 'never';
  const delta = (Date.now() - new Date(iso).getTime()) / 1000;
  if (delta < 60) return `${delta.toFixed(0)}s ago`;
  if (delta < 3600) return `${(delta / 60).toFixed(0)}m ago`;
  if (delta < 86400) return `${(delta / 3600).toFixed(1)}h ago`;
  return `${(delta / 86400).toFixed(1)}d ago`;
}

const FRESHNESS_STYLE: Record<Freshness, { border: string; dot: string; badge: string; label: string }> = {
  fresh: {
    border: 'border-green-500/50',
    dot: 'bg-green-500',
    badge: 'bg-green-500/20 text-green-400',
    label: 'FRESH',
  },
  warm: {
    border: 'border-yellow-500/50',
    dot: 'bg-yellow-500',
    badge: 'bg-yellow-500/20 text-yellow-400',
    label: 'WARM',
  },
  stale: {
    border: 'border-red-500/50',
    dot: 'bg-red-500',
    badge: 'bg-red-500/20 text-red-400',
    label: 'STALE',
  },
  unknown: {
    border: 'border-slate-600',
    dot: 'bg-slate-500',
    badge: 'bg-slate-700 text-slate-400',
    label: 'NO DATA',
  },
};

export default function Weather() {
  const { data = [], isLoading } = useQuery({
    queryKey: ['weather'],
    queryFn: api.weather,
    refetchInterval: 30000,
  });

  const counts = data.reduce(
    (acc, s) => {
      acc[freshness(s)]++;
      return acc;
    },
    { fresh: 0, warm: 0, stale: 0, unknown: 0 } as Record<Freshness, number>,
  );

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold text-white">Weather Monitor</h2>
        <div className="flex items-center gap-3 text-xs">
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-green-500" /> {counts.fresh} fresh
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-yellow-500" /> {counts.warm} warm
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-red-500" /> {counts.stale} stale
          </span>
          {counts.unknown > 0 && (
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-slate-500" /> {counts.unknown} no data
            </span>
          )}
        </div>
      </div>
      <p className="text-xs text-slate-500 mb-6">
        Fresh &lt; 1h · Warm 1–3h · Stale ≥ 3h since last METAR report
      </p>

      {isLoading ? (
        <p className="text-slate-400">Loading...</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {data.map(s => {
            const f = freshness(s);
            const style = FRESHNESS_STYLE[f];
            return (
              <div
                key={s.station_id}
                className={`bg-slate-800 rounded-lg p-4 border ${style.border}`}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${style.dot}`} />
                    <span className="font-mono text-sm font-bold text-white">
                      {s.station_id}
                    </span>
                  </div>
                  <span className={`px-2 py-0.5 rounded text-xs font-bold ${style.badge}`}>
                    {style.label}
                  </span>
                </div>
                <p className="text-sm text-slate-300">
                  {s.city_name} ({s.country_code})
                </p>
                {s.last_report_at ? (
                  <p className="text-xs text-slate-500 mt-1">Last: {timeAgo(s.last_report_at)}</p>
                ) : (
                  <p className="text-xs text-slate-500 mt-1">Last: never</p>
                )}
                {s.last_temp_c != null && (
                  <p className="text-xs text-slate-400 mt-1">
                    Temp: {s.last_temp_c.toFixed(1)}°C
                  </p>
                )}
                {s.reliability_score != null && (
                  <div className="mt-2">
                    <div className="flex justify-between text-xs text-slate-400 mb-1">
                      <span>Reliability</span>
                      <span>{(s.reliability_score * 100).toFixed(0)}%</span>
                    </div>
                    <div className="w-full bg-slate-700 rounded-full h-1.5">
                      <div
                        className="bg-green-500 h-1.5 rounded-full"
                        style={{ width: `${s.reliability_score * 100}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>
            );
          })}
          {data.length === 0 && (
            <p className="text-slate-500 col-span-full text-center py-8">No stations configured</p>
          )}
        </div>
      )}
    </div>
  );
}
