import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

export default function Cities() {
  const { data = [], isLoading } = useQuery({ queryKey: ['cities'], queryFn: api.cities });

  return (
    <div>
      <h2 className="text-xl font-bold text-white mb-4">City Mapping</h2>
      <p className="text-sm text-slate-400 mb-4">Manual overrides for Polymarket city name to ICAO station mapping.</p>
      {isLoading ? <p className="text-slate-400">Loading...</p> : (
        <table className="w-full text-sm text-left">
          <thead className="bg-slate-800 text-slate-400 uppercase text-xs">
            <tr>
              <th className="px-4 py-3">City Pattern</th>
              <th className="px-4 py-3">Station</th>
              <th className="px-4 py-3">Priority</th>
            </tr>
          </thead>
          <tbody>
            {data.map(m => (
              <tr key={m.id ?? m.city_pattern} className="border-b border-slate-700">
                <td className="px-4 py-3 text-white">{m.city_pattern}</td>
                <td className="px-4 py-3 font-mono">{m.station_id}</td>
                <td className="px-4 py-3">{m.priority}</td>
              </tr>
            ))}
            {data.length === 0 && <tr><td colSpan={3} className="px-4 py-8 text-center text-slate-500">No manual mappings. Auto-mapping from cities.json.</td></tr>}
          </tbody>
        </table>
      )}
    </div>
  );
}
