import { NavLink, Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { Cpu, FileText, History, LayoutDashboard, ScanLine, Table2, Upload } from 'lucide-react'
import { api } from '../lib/api'
import type { Capabilities } from '../types/api'

const NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/new', label: 'New invoice', icon: Upload, end: false },
  { to: '/runs', label: 'Run history', icon: History, end: false },
  { to: '/reference', label: 'Reference data', icon: Table2, end: false },
]

export function Layout() {
  const { data: capabilities } = useQuery({
    queryKey: ['capabilities'],
    queryFn: api.capabilities,
    staleTime: 60_000,
  })

  return (
    <div className="min-h-screen lg:flex">
      <a href="#main" className="skip-link">
        Skip to main content
      </a>

      <header className="flex items-center gap-3 border-b border-line bg-surface px-4 py-3 lg:hidden">
        <Brand />
      </header>

      <nav
        aria-label="Primary"
        className="border-b border-line bg-surface px-2 py-2 lg:w-[228px] lg:shrink-0 lg:border-r lg:border-b-0 lg:px-3 lg:py-4"
      >
        <div className="mb-4 hidden px-2 lg:block">
          <Brand />
        </div>
        <ul className="flex gap-1 overflow-x-auto lg:flex-col lg:overflow-visible">
          {NAV.map((item) => (
            <li key={item.to} className="shrink-0">
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  clsx(
                    'flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap',
                    isActive
                      ? 'bg-brand-soft text-brand'
                      : 'text-ink hover:bg-canvas hover:text-brand',
                  )
                }
              >
                <item.icon aria-hidden="true" className="size-4" />
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
        {capabilities && <CapabilityPanel capabilities={capabilities} />}
      </nav>

      <div className="min-w-0 flex-1">
        <main id="main" className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

function Brand() {
  return (
    <div className="flex items-center gap-2">
      <span className="grid size-8 place-items-center rounded-md bg-brand text-white">
        <FileText aria-hidden="true" className="size-4" />
      </span>
      <span>
        <span className="block text-sm font-semibold text-ink">ClearLedger</span>
        <span className="block text-[11px] text-muted">Invoice decisions with evidence</span>
      </span>
    </div>
  )
}

function CapabilityPanel({ capabilities }: { capabilities: Capabilities }) {
  return (
    <aside className="mt-5 hidden rounded-md border border-line bg-canvas/70 p-3 lg:block">
      <p className="mb-2 text-[11px] font-semibold tracking-wide text-muted uppercase">
        This environment
      </p>
      <dl className="space-y-2 text-[11px] text-muted">
        <div className="flex items-start gap-1.5">
          <Cpu aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
          <div>
            <dt className="font-medium text-ink">
              {capabilities.extraction.configured_provider_label}
            </dt>
            <dd>
              {capabilities.extraction.allow_rules_fallback
                ? 'Fallback to the deterministic parser is enabled.'
                : 'No silent provider fallback.'}
            </dd>
          </div>
        </div>
        <div className="flex items-start gap-1.5">
          <ScanLine aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
          <div>
            <dt className="font-medium text-ink">
              OCR {capabilities.ocr.available ? 'available' : 'unavailable'}
            </dt>
            <dd>{capabilities.ocr.available ? capabilities.ocr.engine : capabilities.ocr.detail}</dd>
          </div>
        </div>
      </dl>
      <p className="mt-3 rounded border border-review/25 bg-review-soft px-2 py-1.5 text-[11px] text-review">
        Synthetic demo data. Approving reserves a commitment against a purchase order; it never
        pays anyone.
      </p>
      <p className="mt-2 text-[10px] text-muted">
        Policy {capabilities.app_version} · {capabilities.environment}
      </p>
    </aside>
  )
}
