import { useState, useEffect, type ReactNode } from 'react';
import { api, getApiKey, setApiKey } from '../api/client';

export default function ApiKeyGate({ children }: { children: ReactNode }) {
  const [hasKey, setHasKey] = useState(false);
  const [checking, setChecking] = useState(true);
  const [input, setInput] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const key = getApiKey();
    if (!key) {
      setChecking(false);
      return;
    }
    // Validate stored key against the backend
    api.validateKey(key).then(ok => {
      if (ok) setHasKey(true);
      setChecking(false);
    }).catch(() => setChecking(false));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      const ok = await api.validateKey(input);
      if (ok) {
        setApiKey(input);
        setHasKey(true);
      } else {
        setError('Invalid API key');
      }
    } catch (err) {
      setError('Connection error');
    } finally {
      setSubmitting(false);
    }
  };

  if (checking) {
    return (
      <div className="flex items-center justify-center h-screen bg-slate-900">
        <p className="text-slate-400">Checking credentials...</p>
      </div>
    );
  }

  if (hasKey) {
    return <>{children}</>;
  }

  return (
    <div className="flex items-center justify-center h-screen bg-slate-900">
      <form onSubmit={handleSubmit} className="bg-slate-800 rounded-lg p-8 border border-slate-700 w-96">
        <h1 className="text-xl font-bold text-white mb-2">Weather Bot Dashboard</h1>
        <p className="text-sm text-slate-400 mb-6">Enter your API key to continue.</p>
        <input
          type="password"
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="X-API-Key value"
          autoFocus
          className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded text-white text-sm focus:outline-none focus:border-blue-500"
        />
        {error && <p className="text-red-400 text-xs mt-2">{error}</p>}
        <button
          type="submit"
          disabled={submitting || !input}
          className="w-full mt-4 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-500 text-white rounded text-sm font-bold transition-colors"
        >
          {submitting ? 'Validating...' : 'Unlock'}
        </button>
        <p className="text-xs text-slate-500 mt-4">
          The key is stored in your browser's localStorage. Use the same
          <code className="text-slate-300 px-1">DASH_PASS</code> configured on Railway.
        </p>
      </form>
    </div>
  );
}
