import { useState } from 'react'
import { useFrappePostCall } from 'frappe-react-sdk'
import { useWeeklyAiReports } from '../lib/hooks'
import type { ProxmoxServerRole, WeeklyAIReport, WeeklyReportType } from '../lib/types'
import { timeAgo } from '../lib/format'
import { getErrorMessage } from '../lib/errors'
import { StatusBadge } from './StatusBadge'

const REPORT_TYPE_TABS: WeeklyReportType[] = ['Proxmox Fleet', 'Host Health']

/** Very small Markdown-lite renderer for the LLM's report text — this app
 * has no Markdown library dependency anywhere (checked package.json), and
 * the report only ever uses a handful of constructs (##/### headers, `-`
 * bullets, **bold**, plain paragraphs), so a tiny hand-rolled line-based
 * renderer is enough rather than pulling in a whole Markdown stack for
 * four heading levels and bold text. Escapes HTML first so the LLM's own
 * output (which we don't otherwise sanitize) can never inject markup. */
function renderReportMarkdown(text: string) {
  const escapeHtml = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const bold = (s: string) => escapeHtml(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')

  const lines = text.split('\n')
  const blocks: { type: 'h2' | 'h3' | 'li' | 'p' | 'blank'; content: string }[] = []
  for (const line of lines) {
    if (/^##\s+/.test(line)) blocks.push({ type: 'h2', content: line.replace(/^##\s+/, '') })
    else if (/^#\s+/.test(line)) blocks.push({ type: 'h2', content: line.replace(/^#\s+/, '') })
    else if (/^###\s+/.test(line)) blocks.push({ type: 'h3', content: line.replace(/^###\s+/, '') })
    else if (/^[-*]\s+/.test(line)) blocks.push({ type: 'li', content: line.replace(/^[-*]\s+/, '') })
    else if (line.trim() === '') blocks.push({ type: 'blank', content: '' })
    else blocks.push({ type: 'p', content: line })
  }

  return (
    <div className="space-y-1 text-sm leading-relaxed text-ink-primary">
      {blocks.map((b, i) => {
        if (b.type === 'blank') return null
        if (b.type === 'h2')
          return (
            <h4 key={i} className="mt-4 mb-1.5 text-xs font-semibold uppercase tracking-widest text-ink-muted first:mt-0">
              {b.content}
            </h4>
          )
        if (b.type === 'h3')
          return (
            <h5 key={i} className="mt-3 mb-1 font-semibold text-ink-primary">
              {b.content}
            </h5>
          )
        if (b.type === 'li')
          return (
            <div key={i} className="flex gap-2 pl-1">
              <span className="text-ink-muted">-</span>
              <span dangerouslySetInnerHTML={{ __html: bold(b.content) }} />
            </div>
          )
        return <p key={i} dangerouslySetInnerHTML={{ __html: bold(b.content) }} />
      })}
    </div>
  )
}

function ReportRow({ report }: { report: WeeklyAIReport }) {
  const [expanded, setExpanded] = useState(false)
  const [reportText, setReportText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { call: getReportText, loading } = useFrappePostCall<{ message: string }>('alfaedge_pulse.ai_insights.api.get_report_text')

  const toggle = async () => {
    if (expanded) {
      setExpanded(false)
      return
    }
    setExpanded(true)
    if (reportText != null || report.status !== 'Success') return
    setError(null)
    try {
      const response = await getReportText({ name: report.name })
      setReportText(response.message)
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  const download = async () => {
    try {
      const response = await getReportText({ name: report.name })
      const blob = new Blob([response.message], { type: 'text/plain' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${report.report_type.replace(/\s+/g, '-').toLowerCase()}-${report.name}.txt`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  return (
    <div className="rounded-xl border border-gridline">
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center justify-between gap-4 px-4 py-3 text-left hover:bg-ink-primary/5"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-sm text-ink-muted">
            <span>{timeAgo(report.generated_at)}</span>
            {report.role && (
              <span className="rounded-full border border-border-hairline px-2 py-0.5 text-xs text-ink-secondary">
                {report.role}
              </span>
            )}
            <StatusBadge status={report.status} />
            {!!report.sent_email && <span title="Sent via email">✉️</span>}
            {!!report.sent_whatsapp && <span title="Sent via WhatsApp">💬</span>}
          </div>
          <div className="mt-0.5 truncate text-sm text-ink-primary">
            {report.status === 'Success' ? report.summary || '—' : report.error || 'Report generation failed'}
          </div>
        </div>
        <span className="shrink-0 text-xs text-ink-muted">{expanded ? 'Hide' : 'View'}</span>
      </button>
      {expanded && (
        <div className="border-t border-border-hairline px-4 py-3">
          {report.status !== 'Success' ? (
            <p className="text-sm text-status-critical">{report.error}</p>
          ) : loading && reportText == null ? (
            <p className="text-sm text-ink-muted">Loading…</p>
          ) : error ? (
            <p className="text-sm text-status-critical">{error}</p>
          ) : (
            <>
              {reportText && renderReportMarkdown(reportText)}
              <div className="mt-4 flex justify-end border-t border-border-hairline pt-3">
                <button
                  type="button"
                  onClick={download}
                  className="rounded-lg border border-border-hairline px-2.5 py-1 text-xs text-ink-secondary hover:bg-ink-primary/5"
                >
                  Download ↓
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

/** Weekly AI-generated reports — Proxmox Fleet and Host Health analyzed
 * and generated completely independently (see ai_insights/proxmox_report.py,
 * .host_health_report.py's own docstrings for why), shown here as two
 * sub-tabs over one shared list rather than two separate main tabs, since
 * they're otherwise identical in shape and this is a lower-traffic
 * ("once a week") panel that doesn't need its own top-level real estate
 * split in two. */
export function AiInsightsPanel() {
  const [reportType, setReportType] = useState<WeeklyReportType>('Proxmox Fleet')
  const [roleFilter, setRoleFilter] = useState<string>('All')
  const { data } = useWeeklyAiReports()

  const typeReports = (data ?? []).filter((r) => r.report_type === reportType)
  // Proxmox Fleet reports are generated once per Proxmox Server role
  // (Production/Development/Staging/...) rather than blended — only offer
  // roles that actually have a report yet, so this filter row doesn't show
  // options that would always be empty.
  const availableRoles = Array.from(new Set(typeReports.map((r) => r.role).filter((r): r is ProxmoxServerRole => !!r)))
  const reports =
    reportType === 'Proxmox Fleet' && roleFilter !== 'All' ? typeReports.filter((r) => r.role === roleFilter) : typeReports

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center rounded-lg border border-border-hairline p-0.5 text-xs w-fit">
          {REPORT_TYPE_TABS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => {
                setReportType(t)
                setRoleFilter('All')
              }}
              className={`rounded-md px-3 py-1.5 transition-colors ${
                reportType === t ? 'bg-accent text-white' : 'text-ink-secondary hover:bg-ink-primary/5'
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        {reportType === 'Proxmox Fleet' && availableRoles.length > 1 && (
          <div className="flex items-center rounded-lg border border-border-hairline p-0.5 text-xs w-fit">
            {['All', ...availableRoles].map((role) => (
              <button
                key={role}
                type="button"
                onClick={() => setRoleFilter(role)}
                className={`rounded-md px-3 py-1.5 transition-colors ${
                  roleFilter === role ? 'bg-accent text-white' : 'text-ink-secondary hover:bg-ink-primary/5'
                }`}
              >
                {role}
              </button>
            ))}
          </div>
        )}
      </div>

      {reports.length === 0 ? (
        <p className="py-10 text-center text-sm text-ink-muted">
          {typeReports.length === 0
            ? `No ${reportType} reports yet — the first one generates automatically next Monday, or configure and enable it in AI Insights Settings from Desk.`
            : `No ${roleFilter} reports yet.`}
        </p>
      ) : (
        <div className="space-y-2">
          {reports.map((r) => (
            <ReportRow key={r.name} report={r} />
          ))}
        </div>
      )}
    </div>
  )
}
