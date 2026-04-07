import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';

export default function Config() {
  const queryClient = useQueryClient();
  const { data = [], isLoading } = useQuery({ queryKey: ['config'], queryFn: api.config });
  const [editKey, setEditKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');

  const mutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) => api.updateConfig(key, value),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['config'] }); setEditKey(null); },
  });

  return (
    <div>
      <h2 className="text-xl font-bold text-white mb-4">Config Editor</h2>
      <p className="text-sm text-slate-400 mb-4">Risk parameters stored in DB. Changes take effect on next cycle.</p>
      {isLoading ? <p className="text-slate-400">Loading...</p> : (
        <table className="w-full text-sm text-left">
          <thead className="bg-slate-800 text-slate-400 uppercase text-xs">
            <tr>
              <th className="px-4 py-3">Key</th>
              <th className="px-4 py-3">Value</th>
              <th className="px-4 py-3">Updated</th>
              <th className="px-4 py-3 w-24">Action</th>
            </tr>
          </thead>
          <tbody>
            {data.map(item => (
              <tr key={item.key} className="border-b border-slate-700">
                <td className="px-4 py-3 font-mono text-xs text-white">{item.key}</td>
                <td className="px-4 py-3">
                  {editKey === item.key ? (
                    <input value={editValue} onChange={e => setEditValue(e.target.value)}
                      className="bg-slate-700 text-white px-2 py-1 rounded text-sm w-40 border border-slate-600"
                      autoFocus onKeyDown={e => e.key === 'Enter' && mutation.mutate({ key: item.key, value: editValue })} />
                  ) : (
                    <span className="text-slate-300">{item.value}</span>
                  )}
                </td>
                <td className="px-4 py-3 text-xs text-slate-500">
                  {item.updated_at ? new Date(item.updated_at).toLocaleString() : '—'}
                </td>
                <td className="px-4 py-3">
                  {editKey === item.key ? (
                    <div className="flex gap-1">
                      <button onClick={() => mutation.mutate({ key: item.key, value: editValue })}
                        className="px-2 py-1 bg-green-600 text-white text-xs rounded">Save</button>
                      <button onClick={() => setEditKey(null)}
                        className="px-2 py-1 bg-slate-600 text-white text-xs rounded">Cancel</button>
                    </div>
                  ) : (
                    <button onClick={() => { setEditKey(item.key); setEditValue(item.value); }}
                      className="px-2 py-1 bg-blue-600 text-white text-xs rounded hover:bg-blue-700">Edit</button>
                  )}
                </td>
              </tr>
            ))}
            {data.length === 0 && <tr><td colSpan={4} className="px-4 py-8 text-center text-slate-500">No config entries in DB</td></tr>}
          </tbody>
        </table>
      )}
    </div>
  );
}
