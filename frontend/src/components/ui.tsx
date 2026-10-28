import type { ReactNode } from 'react'
import type { SortDirection } from '../lib/sort'

/** Small building blocks shared by the table-style tabs (Logs, Host Health,
 * Uptime): stat tiles, titled cards, sortable headers, usage bars. */

export type Tone = 'ok' | 'warning' | 'critical' | 'muted'

const TONE_TEXT: Record<Tone, string> = {
  ok: 'text-ink-primary',
  warning: 'text-status-warning',
  critical: 'text-status-critical',
  muted: 'text-ink-muted',
}

export function StatTile({
  label,
  value,
  sub,
  tone = 'ok',
  onClick,
  active,
}: {
  label: string
  value?: number | string
  sub?: ReactNode
  tone?: Tone
  /** Makes the tile a button (e.g. to filter the list below it). */
  onClick?: () => void
  /** Highlights a clickable tile whose filter is currently applied. */
  active?: boolean
}) {
  const body = (
    <>
      <div className={`text-xl font-semibold tabular-nums ${TONE_TEXT[tone]}`}>
        {value === undefined ? '—' : typeof value === 'number' ? value.toLocaleString() : value}
      </div>
      <div className="mt-1 text-xs font-medium uppercase tracking-wide text-ink-muted">{label}</div>
      {sub && <div className="mt-1 truncate text-xs text-ink-muted">{sub}</div>}
    </>
  )
  if (!onClick) return <div className="rounded-2xl border border-border-hairline bg-surface-card p-4 shadow-sm">{body}</div>
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={!!active}
      className={`rounded-2xl border bg-surface-card p-4 text-left shadow-sm transition-colors hover:border-accent ${
        active ? 'border-accent ring-1 ring-accent' : 'border-border-hairline'
      }`}
    >
      {body}
    </button>
  )
}

export function Card({
  title,
  actions,
  children,
  noPadding,
  className = '',
}: {
  title: ReactNode
  actions?: ReactNode
  children: ReactNode
  noPadding?: boolean
  className?: string
}) {
  return (
    <div className={`rounded-2xl border border-border-hairline bg-surface-card shadow-sm ${className}`}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-hairline px-5 py-3">
        <h3 className="text-sm font-semibold text-ink-primary">{title}</h3>
        {actions}
      </div>
      <div className={noPadding ? '' : 'p-5'}>{children}</div>
    </div>
  )
}

export function Empty({ text }: { text: string }) {
  return <p className="py-10 text-center text-sm text-ink-muted">{text}</p>
}

export function SortHeader<F extends string>({
  label,
  field,
  sortField,
  sortDirection,
  onSort,
  align = 'left',
  className = '',
}: {
  label: string
  field: F
  sortField: F
  sortDirection: SortDirection
  onSort: (field: F) => void
  align?: 'left' | 'right'
  className?: string
}) {
  const active = field === sortField
  return (
    <th scope="col" aria-sort={active ? (sortDirection === 'asc' ? 'ascending' : 'descending') : undefined} className={className}>
      <button
        type="button"
        onClick={() => onSort(field)}
        className={`inline-flex w-full items-center gap-1 px-3 py-3 text-xs font-medium uppercase tracking-wide hover:text-ink-primary ${
          align === 'right' ? 'justify-end' : ''
        } ${active ? 'text-ink-primary' : 'text-ink-muted'}`}
      >
        {label}
        <span aria-hidden="true" className={active ? 'opacity-100' : 'opacity-30'}>
          {active && sortDirection === 'asc' ? '▲' : '▼'}
        </span>
      </button>
    </th>
  )
}

/** Horizontal usage bar with the value beside it. ``max`` is what fills the
 * bar (100 for a percentage; more for normalised load, which can exceed it). */
export function UsageBar({ value, max = 100, tone, label }: { value?: number | null; max?: number; tone: Tone; label?: string }) {
  const fill =
    tone === 'critical' ? 'bg-status-critical' : tone === 'warning' ? 'bg-status-warning' : tone === 'muted' ? 'bg-ink-muted' : 'bg-accent'
  const width = value === null || value === undefined ? 0 : Math.max(0, Math.min(100, (value / max) * 100))
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gridline">
        <div className={`h-1.5 rounded-full ${fill}`} style={{ width: `${width}%` }} />
      </div>
      <span className="w-12 text-right text-xs tabular-nums text-ink-secondary">
        {label ?? (value === null || value === undefined ? '—' : `${Math.round(value)}%`)}
      </span>
    </div>
  )
}

export function Pill({ tone, children }: { tone: Tone | 'info'; children: ReactNode }) {
  const cls =
    tone === 'critical'
      ? 'bg-status-critical/15 text-status-critical'
      : tone === 'warning'
        ? 'bg-status-warning/15 text-status-warning'
        : tone === 'info'
          ? 'bg-accent/15 text-accent'
          : tone === 'muted'
            ? 'bg-ink-primary/5 text-ink-muted'
            : 'bg-status-good/15 text-status-good'
  return <span className={`inline-block whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold ${cls}`}>{children}</span>
}

export interface AttentionItem {
  key: string
  title: string
  text: string
  tone: 'critical' | 'warning' | 'muted'
  onClick?: () => void
}

/** The ranked "Needs attention" list both tabs show. */
export function AttentionList({ items, emptyText }: { items: AttentionItem[]; emptyText: string }) {
  if (items.length === 0) return <Empty text={emptyText} />
  return (
    <ul className="divide-y divide-border-hairline">
      {items.map((it) => {
        const dot = it.tone === 'critical' ? 'bg-status-critical' : it.tone === 'warning' ? 'bg-status-warning' : 'bg-ink-muted'
        const body = (
          <>
            <span aria-hidden="true" className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${dot}`} />
            <span className="min-w-0">
              <span className="block truncate text-sm font-semibold text-ink-primary">{it.title}</span>
              <span className="block text-xs text-ink-secondary">{it.text}</span>
            </span>
          </>
        )
        return (
          <li key={it.key}>
            {it.onClick ? (
              <button type="button" onClick={it.onClick} className="flex w-full gap-3 px-5 py-2.5 text-left hover:bg-ink-primary/5">
                {body}
              </button>
            ) : (
              <div className="flex gap-3 px-5 py-2.5">{body}</div>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/** Compact segmented toggle (e.g. 24h / 3d / 7d). */
export function Segmented<T extends string | number>({
  options,
  value,
  onChange,
  label,
}: {
  options: { label: string; value: T }[]
  value: T
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div role="group" aria-label={label} className="flex items-center rounded-xl border border-border-hairline bg-surface-card p-0.5 text-xs">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
          className={`rounded-lg px-3 py-1.5 transition-colors ${
            o.value === value ? 'bg-accent text-white' : 'text-ink-secondary hover:bg-ink-primary/5'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
