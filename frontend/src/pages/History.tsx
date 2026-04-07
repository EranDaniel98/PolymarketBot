import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

export default function History() {
  const { data = [], isLoading } = useQuery({ queryKey: ['history'], queryFn: () => api.history(100) });

  const exportCsv = () => {
    if (!data.length) return;
    const headers = ['ID', 'Token', 'Size', 'Price', 'Status', 'PnL', 'Settlement', 'Placed', 'Exit Reason'];
    const rows = data.map(t => [t.id, t.token_id, t.size_usdc, t.fill_price, t.status, t.pnl_usdc, t.settlement_result, t.placed_at, t.exit_reason].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'trade_history.csv'; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold text-white">Trade History</h2>
        <button onClick={exportCsv} className="px-3 py-1.5 bg-blue-600 text-white text-xs rounded hover:bg-blue-700">Export CSV</button>
      </div>
      {isLoading ? <p className="text-slate-400">Loading...</p> : (
        <table className="w-full text-sm text-left">
          <thead className="bg-slate-800 text-slate-400 uppercase text-xs">
            <tr>
              <th className="px-4 py-3">Date</th>
              <th className="px-4 py-3">Size</th>
              <th className="px-4 py-3">Price</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">P&L</th>
              <th className="px-4 py-3">Result</th>
              <th className="px-4 py-3">Exit</th>
            </tr>
          </thead>
          <tbody>
            {data.map(t => (
              <tr key={t.id} className="border-b border-slate-700 hover:bg-slate-800/50">
                <td className="px-4 py-3 text-xs text-slate-400">{t.placed_at ? new Date(t.placed_at).toLocaleString() : '—'}</td>
                <td className="px-4 py-3">{t.size_usdc != null ? `$${t.size_usdc.toFixed(2)}` : '—'}</td>
                <td className="px-4 py-3">{t.fill_price != null ? `$${t.fill_price.toFixed(4)}` : '—'}</td>
                <td className="px-4 py-3"><span className="px-2 py-0.5 rounded text-xs bg-slate-700">{t.status}</span></td>
                <td className={`px-4 py-3 font-bold ${(t.pnl_usdc ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {t.pnl_usdc != null ? `${t.pnl_usdc >= 0 ? '+' : ''}$${t.pnl_usdc.toFixed(2)}` : '—'}
                </td>
                <td className="px-4 py-3">{t.settlement_result || '—'}</td>
                <td className="px-4 py-3 text-xs text-slate-400">{t.exit_reason || '—'}</td>
              </tr>
            ))}
            {data.length === 0 && <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-500">No trades yet</td></tr>}
          </tbody>
        </table>
      )}
    </div>
  );
}
