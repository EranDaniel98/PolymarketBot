import { NavLink, Outlet } from 'react-router-dom';
import { clearApiKey } from '../api/client';

const links = [
  { to: '/', label: 'Overview' },
  { to: '/opportunities', label: 'Opportunities' },
  { to: '/positions', label: 'Positions' },
  { to: '/history', label: 'Trade History' },
  { to: '/weather', label: 'Weather' },
  { to: '/calibration', label: 'Calibration' },
  { to: '/jobs', label: 'Jobs' },
  { to: '/config', label: 'Config' },
  { to: '/cities', label: 'City Mapping' },
  { to: '/logs', label: 'System Logs' },
];

function handleLogout() {
  clearApiKey();
  window.location.reload();
}

export default function Layout() {
  return (
    <div className="flex h-screen bg-slate-900">
      {/* Sidebar */}
      <nav className="w-56 bg-slate-800 border-r border-slate-700 flex flex-col">
        <div className="px-4 py-5 border-b border-slate-700">
          <h1 className="text-lg font-bold text-white">Weather Bot</h1>
          <p className="text-xs text-slate-400">Polymarket Arbitrage</p>
        </div>
        <div className="flex-1 py-3">
          {links.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `block px-4 py-2 text-sm ${
                  isActive
                    ? 'bg-blue-600/20 text-blue-400 border-l-2 border-blue-400'
                    : 'text-slate-300 hover:bg-slate-700/50 border-l-2 border-transparent'
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </div>
        <button
          onClick={handleLogout}
          className="px-4 py-3 text-xs text-slate-500 hover:text-slate-300 border-t border-slate-700 text-left"
        >
          Clear API key & log out
        </button>
      </nav>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
