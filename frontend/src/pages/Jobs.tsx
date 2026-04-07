import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

function timeAgo(iso: string | null): string {
  if (!iso) return 'never';
  const delta = (Date.now() - new Date(iso).getTime()) / 1000;
  if (delta < 60) return `${delta.toFixed(0)}s ago`;
  if (delta < 3600) return `${(delta / 60).toFixed(0)}m ago`;
  if (delta < 86400) return `${(delta / 3600).toFixed(1)}h ago`;
  return `${(delta / 86400).toFixed(1)}d ago`;
}

function formatInterval(s: number): string {
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${(s / 60).toFixed(0)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

export default function Jobs() {
  const { data = [], isLoading } = useQuery({
    queryKey: ['jobs'],
    queryFn: api.jobs,
    refetchInterval: 5000,
  });

  return (
    <div>
      <h2 className="text-xl font-bold text-white mb-2">Scheduled Jobs</h2>
      <p className="text-sm text-slate-400 mb-6">
        Background workers running inside the bot process. A job is healthy
        when its last cycle finished within 2× its configured interval and
        did not error.
      </p>

      {isLoading ? (
        <p className="text-slate-400">Loading...</p>
      ) : data.length === 0 ? (
        <p className="text-slate-500 text-center py-12">No jobs registered yet.</p>
      ) : (
        <div className="space-y-3">
          {data.map(job => (
            <div
              key={job.name}
              className={`bg-slate-800 rounded-lg p-4 border-l-4 ${
                job.healthy ? 'border-green-500 border-slate-700' : 'border-red-500 border-slate-700'
              } border-t border-r border-b`}
            >
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <span
                    className={`w-2 h-2 rounded-full ${
                      job.healthy ? 'bg-green-500 animate-pulse' : 'bg-red-500'
                    }`}
                  />
                  <h3 className="font-mono text-sm font-bold text-white">{job.name}</h3>
                  <span className="text-xs text-slate-500">every {formatInterval(job.interval_seconds)}</span>
                </div>
                <span
                  className={`px-2 py-0.5 rounded text-xs font-bold ${
                    job.healthy
                      ? 'bg-green-500/20 text-green-400'
                      : 'bg-red-500/20 text-red-400'
                  }`}
                >
                  {job.healthy ? 'HEALTHY' : 'UNHEALTHY'}
                </span>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                <div>
                  <p className="text-slate-500 uppercase tracking-wider">Last Run</p>
                  <p className="text-slate-200 font-mono">{timeAgo(job.last_finished_at)}</p>
                </div>
                <div>
                  <p className="text-slate-500 uppercase tracking-wider">Duration</p>
                  <p className="text-slate-200 font-mono">
                    {job.last_duration_ms != null ? `${job.last_duration_ms.toFixed(0)}ms` : '—'}
                  </p>
                </div>
                <div>
                  <p className="text-slate-500 uppercase tracking-wider">Successes</p>
                  <p className="text-green-400 font-mono">{job.successes}</p>
                </div>
                <div>
                  <p className="text-slate-500 uppercase tracking-wider">Failures</p>
                  <p className={`font-mono ${job.failures > 0 ? 'text-red-400' : 'text-slate-400'}`}>
                    {job.failures}
                  </p>
                </div>
              </div>

              {job.last_error && (
                <div className="mt-3 p-2 bg-red-500/10 border border-red-500/30 rounded text-xs">
                  <p className="text-red-400 font-bold mb-1">
                    Last error {timeAgo(job.last_error_at)}
                  </p>
                  <p className="text-red-300 font-mono break-all">{job.last_error}</p>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
