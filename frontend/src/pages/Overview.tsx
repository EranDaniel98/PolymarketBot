import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
      <p className="text-xs text-slate-400 uppercase tracking-wider">{label}</p>
      <p className="text-2xl font-bold text-white mt-1">{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  );
}

export default function Overview() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['overview'],
    queryFn: api.overview,
    refetchInterval: 5000,
  });
  const { data: killSwitch } = useQuery({
    queryKey: ['killSwitch'],
    queryFn: api.killSwitch,
    refetchInterval: 10000,
  });

  const toggleKillSwitch = useMutation({
    mutationFn: (paused: boolean) => api.setKillSwitch(paused),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['killSwitch'] }),
  });

  if (isLoading) return <p className="text-slate-400">Loading...</p>;
  if (!data) return null;

  const paused = killSwitch?.paused ?? false;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold text-white">Dashboard Overview</h2>
        <div className="flex items-center gap-3">
          <span className={`px-3 py-1 rounded-full text-xs font-bold ${data.paper_mode ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}`}>
            {data.paper_mode ? 'PAPER' : 'LIVE'}
          </span>
          {killSwitch?.available && (
            <button
              onClick={() => {
                if (!paused) {
                  if (!confirm('Pause trading? New orders will be rejected by the risk manager.')) return;
                }
                toggleKillSwitch.mutate(!paused);
              }}
              disabled={toggleKillSwitch.isPending}
              className={`px-3 py-1 rounded-full text-xs font-bold transition-colors ${
                paused
                  ? 'bg-green-500/20 text-green-400 hover:bg-green-500/30'
                  : 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
              } disabled:opacity-50`}
            >
              {paused ? '▶ RESUME' : '⏸ PAUSE TRADING'}
            </button>
          )}
        </div>
      </div>

      {paused && (
        <div className="mb-6 p-3 bg-red-500/10 border border-red-500/30 rounded-lg">
          <p className="text-red-400 text-sm font-bold">Trading paused by operator — new orders will be rejected.</p>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatCard label="Bankroll" value={`$${data.bankroll.toFixed(2)}`} />
        <StatCard label="Daily P&L" value={`${data.daily_pnl >= 0 ? '+' : ''}$${data.daily_pnl.toFixed(2)}`} />
        <StatCard label="Total P&L" value={`${data.total_pnl >= 0 ? '+' : ''}$${data.total_pnl.toFixed(2)}`} />
        <StatCard label="Win Rate" value={`${(data.win_rate * 100).toFixed(0)}%`} />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Open Positions" value={String(data.open_positions)} sub={`$${data.total_exposure.toFixed(0)} exposure`} />
        <StatCard label="Trades Today" value={String(data.trades_today)} />
        <StatCard label="System Status" value={data.system_status} />
      </div>
    </div>
  );
}
