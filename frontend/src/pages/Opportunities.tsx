import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

export default function Opportunities() {
  const { data = [], isLoading } = useQuery({ queryKey: ['opportunities'], queryFn: () => api.opportunities(), refetchInterval: 5000 });

  return (
    <div>
      <h2 className="text-xl font-bold text-white mb-4">Live Opportunities</h2>
      {isLoading ? <p className="text-slate-400">Loading...</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-slate-800 text-slate-400 uppercase text-xs">
              <tr>
                <th className="px-4 py-3">City</th>
                <th className="px-4 py-3">Our P</th>
                <th className="px-4 py-3">Market P</th>
                <th className="px-4 py-3">Edge</th>
                <th className="px-4 py-3">Direction</th>
                <th className="px-4 py-3">Confidence</th>
                <th className="px-4 py-3">Source</th>
                <th className="px-4 py-3">Traded</th>
              </tr>
            </thead>
            <tbody>
              {data.map((opp) => (
                <tr key={opp.id} className="border-b border-slate-700 hover:bg-slate-800/50">
                  <td className="px-4 py-3 text-white font-medium">{opp.city || '—'}</td>
                  <td className="px-4 py-3">{(opp.our_p * 100).toFixed(0)}%</td>
                  <td className="px-4 py-3">{(opp.market_p * 100).toFixed(0)}%</td>
                  <td className={`px-4 py-3 font-bold ${opp.edge > 0.1 ? 'text-green-400' : 'text-yellow-400'}`}>
                    {(opp.edge * 100).toFixed(1)}%
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded text-xs font-bold ${opp.direction === 'YES' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                      {opp.direction}
                    </span>
                  </td>
                  <td className="px-4 py-3">{(opp.confidence * 100).toFixed(0)}%</td>
                  <td className="px-4 py-3 text-xs text-slate-400">{opp.forecast_source}</td>
                  <td className="px-4 py-3">
                    {opp.traded ? <span className="text-green-400">Yes</span> : <span className="text-slate-500">{opp.skip_reason || 'No'}</span>}
                  </td>
                </tr>
              ))}
              {data.length === 0 && (
                <tr><td colSpan={8} className="px-4 py-8 text-center text-slate-500">No opportunities detected yet</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
