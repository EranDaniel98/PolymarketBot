import { useQuery } from '@tanstack/react-query';
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
  const { data, isLoading } = useQuery({ queryKey: ['overview'], queryFn: api.overview, refetchInterval: 5000 });

  if (isLoading) return <p className="text-slate-400">Loading...</p>;
  if (!data) return null;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold text-white">Dashboard Overview</h2>
        <span className={`px-3 py-1 rounded-full text-xs font-bold ${data.paper_mode ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}`}>
          {data.paper_mode ? 'PAPER' : 'LIVE'}
        </span>
      </div>

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
