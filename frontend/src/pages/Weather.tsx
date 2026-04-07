import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

export default function Weather() {
  const { data = [], isLoading } = useQuery({ queryKey: ['weather'], queryFn: api.weather, refetchInterval: 30000 });

  return (
    <div>
      <h2 className="text-xl font-bold text-white mb-4">Weather Monitor</h2>
      {isLoading ? <p className="text-slate-400">Loading...</p> : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {data.map(s => (
            <div key={s.station_id} className={`bg-slate-800 rounded-lg p-4 border ${s.is_stale ? 'border-red-500/50' : 'border-slate-700'}`}>
              <div className="flex items-center justify-between mb-2">
                <span className="font-mono text-sm font-bold text-white">{s.station_id}</span>
                {s.is_stale && <span className="px-2 py-0.5 bg-red-500/20 text-red-400 rounded text-xs font-bold">STALE</span>}
              </div>
              <p className="text-sm text-slate-300">{s.city_name} ({s.country_code})</p>
              {s.last_report_at && (
                <p className="text-xs text-slate-500 mt-1">Last: {new Date(s.last_report_at).toLocaleString()}</p>
              )}
              {s.reliability_score != null && (
                <div className="mt-2">
                  <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>Reliability</span><span>{(s.reliability_score * 100).toFixed(0)}%</span>
                  </div>
                  <div className="w-full bg-slate-700 rounded-full h-1.5">
                    <div className="bg-green-500 h-1.5 rounded-full" style={{ width: `${s.reliability_score * 100}%` }} />
                  </div>
                </div>
              )}
            </div>
          ))}
          {data.length === 0 && <p className="text-slate-500 col-span-full text-center py-8">No stations configured</p>}
        </div>
      )}
    </div>
  );
}
