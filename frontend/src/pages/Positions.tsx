import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

export default function Positions() {
  const { data = [], isLoading } = useQuery({ queryKey: ['positions'], queryFn: api.positions, refetchInterval: 5000 });

  return (
    <div>
      <h2 className="text-xl font-bold text-white mb-4">Open Positions</h2>
      {isLoading ? <p className="text-slate-400">Loading...</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-slate-800 text-slate-400 uppercase text-xs">
              <tr>
                <th className="px-4 py-3">City</th>
                <th className="px-4 py-3">Direction</th>
                <th className="px-4 py-3">Size</th>
                <th className="px-4 py-3">Entry</th>
                <th className="px-4 py-3">Current</th>
                <th className="px-4 py-3">P&L</th>
                <th className="px-4 py-3">Peak</th>
              </tr>
            </thead>
            <tbody>
              {data.map((pos) => (
                <tr key={pos.market_id} className="border-b border-slate-700 hover:bg-slate-800/50">
                  <td className="px-4 py-3 text-white font-medium">{pos.city || pos.market_id.slice(0, 12)}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded text-xs font-bold ${pos.direction === 'YES' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                      {pos.direction}
                    </span>
                  </td>
                  <td className="px-4 py-3">${pos.size_usdc.toFixed(2)}</td>
                  <td className="px-4 py-3">${pos.entry_price.toFixed(4)}</td>
                  <td className="px-4 py-3">{pos.current_price != null ? `$${pos.current_price.toFixed(4)}` : '—'}</td>
                  <td className={`px-4 py-3 font-bold ${pos.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl.toFixed(2)}
                  </td>
                  <td className="px-4 py-3 text-slate-400">{(pos.peak_pnl_pct * 100).toFixed(1)}%</td>
                </tr>
              ))}
              {data.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-500">No open positions</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
