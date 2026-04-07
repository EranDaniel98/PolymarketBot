import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer, Bar, BarChart } from 'recharts';

export default function Calibration() {
  const { data = [], isLoading } = useQuery({ queryKey: ['calibration'], queryFn: api.calibration });

  const chartData = data.map(bin => ({
    predicted: bin.predicted_mean,
    observed: bin.observed_rate,
    count: bin.count,
  }));

  return (
    <div>
      <h2 className="text-xl font-bold text-white mb-4">Edge Calibration</h2>
      <p className="text-sm text-slate-400 mb-6">
        Reliability diagram: when we predict X%, does it happen X% of the time? Points on the diagonal = perfect calibration.
      </p>

      {isLoading ? <p className="text-slate-400">Loading...</p> : data.length === 0 ? (
        <div className="space-y-4">
          <div className="bg-slate-800 rounded-lg p-6 border border-slate-700 text-center">
            <p className="text-slate-300 font-bold mb-2">No calibration data yet</p>
            <p className="text-slate-500 text-sm">
              This chart populates once the bot has settled trades to compare
              against. The calibration job runs daily and recomputes over the
              last 90 days of resolved markets.
            </p>
          </div>
          <div className="bg-slate-800 rounded-lg p-4 border border-slate-700 opacity-60">
            <h3 className="text-sm font-bold text-slate-300 mb-4">Predicted vs Observed (preview)</h3>
            <ResponsiveContainer width="100%" height={300}>
              <ScatterChart margin={{ top: 10, right: 30, bottom: 10, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis type="number" dataKey="predicted" name="Predicted" domain={[0, 1]}
                  tick={{ fill: '#94a3b8', fontSize: 12 }} label={{ value: 'Predicted P', position: 'bottom', fill: '#94a3b8' }} />
                <YAxis type="number" dataKey="observed" name="Observed" domain={[0, 1]}
                  tick={{ fill: '#94a3b8', fontSize: 12 }} label={{ value: 'Observed Rate', angle: -90, position: 'left', fill: '#94a3b8' }} />
                <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#475569" strokeDasharray="5 5" />
              </ScatterChart>
            </ResponsiveContainer>
            <p className="text-xs text-slate-500 text-center mt-2">
              Dashed line = perfect calibration. Points above = under-confident; below = over-confident.
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-6">
          {/* Reliability diagram */}
          <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
            <h3 className="text-sm font-bold text-slate-300 mb-4">Predicted vs Observed Probability</h3>
            <ResponsiveContainer width="100%" height={300}>
              <ScatterChart margin={{ top: 10, right: 30, bottom: 10, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis type="number" dataKey="predicted" name="Predicted" domain={[0, 1]}
                  tick={{ fill: '#94a3b8', fontSize: 12 }} label={{ value: 'Predicted P', position: 'bottom', fill: '#94a3b8' }} />
                <YAxis type="number" dataKey="observed" name="Observed" domain={[0, 1]}
                  tick={{ fill: '#94a3b8', fontSize: 12 }} label={{ value: 'Observed Rate', angle: -90, position: 'left', fill: '#94a3b8' }} />
                <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8 }}
                  formatter={(v) => `${(Number(v) * 100).toFixed(0)}%`} />
                <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#475569" strokeDasharray="5 5" />
                <Scatter data={chartData} fill="#3b82f6" />
              </ScatterChart>
            </ResponsiveContainer>
          </div>

          {/* Sample count per bin */}
          <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
            <h3 className="text-sm font-bold text-slate-300 mb-4">Observations per Bin</h3>
            <ResponsiveContainer width="100%" height={150}>
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="predicted" tick={{ fill: '#94a3b8', fontSize: 12 }}
                  tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
                <YAxis tick={{ fill: '#94a3b8', fontSize: 12 }} />
                <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8 }} />
                <Bar dataKey="count" fill="#6366f1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}
