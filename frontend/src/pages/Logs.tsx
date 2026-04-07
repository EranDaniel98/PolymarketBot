import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';

const severityColors: Record<string, string> = {
  info: 'bg-blue-500/20 text-blue-400',
  warning: 'bg-yellow-500/20 text-yellow-400',
  error: 'bg-red-500/20 text-red-400',
  critical: 'bg-red-600/30 text-red-300',
};

export default function Logs() {
  const [filter, setFilter] = useState<string | undefined>();
  const { data = [], isLoading } = useQuery({
    queryKey: ['events', filter],
    queryFn: () => api.events(filter),
    refetchInterval: 10000,
  });

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold text-white">System Logs</h2>
        <div className="flex gap-2">
          {[undefined, 'info', 'warning', 'error', 'critical'].map(sev => (
            <button key={sev ?? 'all'} onClick={() => setFilter(sev)}
              className={`px-3 py-1 text-xs rounded ${filter === sev ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
              {sev ?? 'All'}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? <p className="text-slate-400">Loading...</p> : (
        <div className="space-y-2">
          {data.map(evt => (
            <div key={evt.id} className="bg-slate-800 rounded-lg p-3 border border-slate-700">
              <div className="flex items-center gap-2 mb-1">
                <span className={`px-2 py-0.5 rounded text-xs font-bold ${severityColors[evt.severity] ?? 'bg-slate-600 text-slate-300'}`}>
                  {evt.severity.toUpperCase()}
                </span>
                <span className="text-xs text-slate-400">{evt.event_type}</span>
                <span className="text-xs text-slate-500 ml-auto">{new Date(evt.created_at).toLocaleString()}</span>
              </div>
              {evt.message && <p className="text-sm text-slate-300">{evt.message}</p>}
              {evt.details && (
                <pre className="text-xs text-slate-500 mt-1 overflow-x-auto">{JSON.stringify(evt.details, null, 2)}</pre>
              )}
            </div>
          ))}
          {data.length === 0 && <p className="text-slate-500 text-center py-8">No events logged</p>}
        </div>
      )}
    </div>
  );
}
