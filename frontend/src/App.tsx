import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { OverviewPage } from './features/overview/OverviewPage'
import { NewInvoicePage } from './features/upload/NewInvoicePage'
import { RunPage } from './features/run/RunPage'
import { HistoryPage } from './features/history/HistoryPage'
import { ReferencePage } from './features/reference/ReferencePage'
import { EmptyState } from './components/ui'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<OverviewPage />} />
        <Route path="new" element={<NewInvoicePage />} />
        <Route path="runs" element={<HistoryPage />} />
        <Route path="runs/:runId" element={<RunPage />} />
        <Route path="reference" element={<ReferencePage />} />
        <Route path="dashboard" element={<Navigate to="/" replace />} />
        <Route
          path="*"
          element={
            <EmptyState
              title="Page not found"
              description="That address does not match any part of ClearLedger."
            />
          }
        />
      </Route>
    </Routes>
  )
}
