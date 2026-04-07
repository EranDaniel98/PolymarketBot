import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Layout from './components/Layout';
import Overview from './pages/Overview';
import Opportunities from './pages/Opportunities';
import Positions from './pages/Positions';
import History from './pages/History';
import Weather from './pages/Weather';
import Calibration from './pages/Calibration';
import Config from './pages/Config';
import Cities from './pages/Cities';
import Logs from './pages/Logs';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5000 } },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Overview />} />
            <Route path="/opportunities" element={<Opportunities />} />
            <Route path="/positions" element={<Positions />} />
            <Route path="/history" element={<History />} />
            <Route path="/weather" element={<Weather />} />
            <Route path="/calibration" element={<Calibration />} />
            <Route path="/config" element={<Config />} />
            <Route path="/cities" element={<Cities />} />
            <Route path="/logs" element={<Logs />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
