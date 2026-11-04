import { useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useHostDiagnosis } from '../lib/hooks'
import type { DiagnosisFinding, DiagnosisSample, HostDiagnosis } from '../lib/types'
import { getErrorMessage } from '../lib/errors'
import { timeAgo } from '../lib/format'
import { Tabs } from './Tabs'
import { TrendChart } from './TrendChart'
import { Card, Empty, Pill, Segmented, StatTile, type Tone } from './ui'

const RANGE_OPTIONS = [
  { label: '1h', value: 1 },
  { label: '6h', value: 6 },
  { label: '24h', value: 24 },
  { label: '7d', value: 168 },
]
const TABS = ['Summary', 'MariaDB', 'Redis', 'Memory', 'Disk I/O'] as const
type TabName = (typeof TABS)[number]

/** A stored number, or null when it is missing or not a number. */
function sampleNumber(row: DiagnosisSample | null | undefined, key: string): number | null {
  const v = row?.[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function toSeconds(datetime: string): number {
  return new Date(datetime.replace(' ', 'T')).getTime() / 1000
}

const fmtNum = (v: number | null, digits = 0) => (v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: digits }))
const fmtPct = (v: number | null, digits = 0) => (v == null ? '—' : `${v.toFixed(digits)}%`)
function fmtMb(mb: number | null): string {
  if (mb == null) return '—'
  return mb >= 10240 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb).toLocaleString()} MB`
}
function fmtBytes(bytes: number): string {
  return fmtMb(bytes / 1048576)
}

interface ChartField {
  key: string
  label: string
  colorVar: string
  fill?: boolean
}

/** One chart over a list of samples. Each chart keeps a single unit so the
 * axis means one thing; samples without computed rates are already blank
 * (null) from the API, which draws as a gap rather than a false zero. */
function SampleChart({
  title,
  rows,
  fields,
  formatter,
  note,
}: {
  title: string
  rows: DiagnosisSample[]
  fields: ChartField[]
  formatter?: (v: number) => string
  note?: string
}) {
  const x = rows.map((r) => toSeconds(r.collected_at))
  return (
    <Card title={title}>
      {rows.length < 2 ? (
        <p className="py-6 text-center text-sm text-ink-muted">Not enough samples in this range yet.</p>
      ) : (
        <TrendChart
          x={x}
          height={170}
          valueFormatter={formatter}
          series={fields.map((f) => ({
            label: f.label,
            colorVar: f.colorVar,
            fill: f.fill,
            data: rows.map((r) => sampleNumber(r, f.key)),
          }))}
        />
      )}
      {note && <p className="mt-2 text-xs text-ink-muted">{note}</p>}
    </Card>
  )
}

export function HostDiagnosisDialog({ hostName, label, onClose }: { hostName: string; label: string; onClose: () => void }) {
  const [hours, setHours] = useState(6)
  const [tab, setTab] = useState<TabName>('Summary')
  const { data, error, isLoading } = useHostDiagnosis(hostName, hours)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} options={{ title: `Diagnose · ${label}`, size: '2xl' }}>
      <div className="flex flex-col gap-4 py-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="max-w-full overflow-x-auto">
            <Tabs options={[...TABS]} value={tab} onChange={(v) => setTab(v as TabName)} />
          </div>
          <Segmented label="Range" options={RANGE_OPTIONS} value={hours} onChange={setHours} />
        </div>

        {error ? (
          <p className="rounded-lg border border-status-critical/40 bg-status-critical/10 px-3 py-2 text-sm text-status-critical">
            Could not load the diagnosis: {getErrorMessage(error)}
          </p>
        ) : isLoading || !data ? (
          <Empty text="Loading…" />
        ) : !data.has_data ? (
          <Empty text="No performance data for this host yet. It appears after SSH polling has run for a minute or two." />
        ) : tab === 'Summary' ? (
          <SummaryTab data={data} />
        ) : tab === 'MariaDB' ? (
          <MariadbTab data={data} />
        ) : tab === 'Redis' ? (
          <RedisTab data={data} />
        ) : tab === 'Memory' ? (
          <MemoryTab data={data} />
        ) : (
          <DiskTab data={data} />
        )}
      </div>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

function FindingItem({ f }: { f: DiagnosisFinding }) {
  const critical = f.severity === 'critical'
  return (
    <li className="px-5 py-4">
      <div className="flex items-start gap-3">
        <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${critical ? 'bg-status-critical' : 'bg-status-warning'}`} aria-hidden />
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink-primary">
            {f.title}
            <span className={`ml-2 text-xs font-semibold uppercase tracking-wide ${critical ? 'text-status-critical' : 'text-status-warning'}`}>{f.severity}</span>
          </p>
          <p className="mt-1 text-sm text-ink-secondary">{f.detail}</p>
          <p className="mt-2 text-sm text-ink-primary">
            <span className="font-semibold">What to do: </span>
            {f.advice}
          </p>
        </div>
      </div>
    </li>
  )
}

function SummaryTab({ data }: { data: HostDiagnosis }) {
  const mem = data.latest
  const db = data.mariadb.latest
  const total = sampleNumber(mem, 'mem_total_mb')
  const available = sampleNumber(mem, 'mem_available_mb')
  const memUsed = total && available != null ? ((total - available) / total) * 100 : null
  const hit = sampleNumber(db, 'bp_hit_percent')
  const connected = sampleNumber(db, 'threads_connected')
  const maxConn = sampleNumber(db, 'max_connections')
  const io = sampleNumber(mem, 'psi_io_some')
  const tone = (v: number | null, warn: number, crit: number, invert = false): Tone => {
    if (v == null) return 'muted'
    if (invert) return v < crit ? 'critical' : v < warn ? 'warning' : 'ok'
    return v >= crit ? 'critical' : v >= warn ? 'warning' : 'ok'
  }
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Memory in use" value={fmtPct(memUsed)} sub={available != null ? `${fmtMb(available)} available` : undefined} tone={tone(memUsed, 90, 95)} />
        <StatTile label="Waiting on disk" value={fmtPct(io)} sub="of the last 10 s" tone={tone(io, 25, 50)} />
        <StatTile label="DB reads from memory" value={hit == null ? '—' : fmtPct(hit, 1)} sub="page cache hit ratio" tone={tone(hit, 99, 95, true)} />
        <StatTile label="DB connections" value={connected == null ? '—' : `${connected} / ${maxConn ?? '—'}`} sub={`${fmtNum(sampleNumber(db, 'threads_running'))} running`} tone={maxConn && connected != null ? tone(connected / maxConn, 0.8, 0.95) : 'muted'} />
      </div>

      <Card title={data.findings.length ? `Findings · ${data.findings.length}` : 'Findings'} noPadding>
        {data.findings.length === 0 ? (
          <p className="px-5 py-8 text-center text-sm text-ink-muted">Nothing wrong found. Memory, disk and MariaDB look healthy over the recent samples.</p>
        ) : (
          <ul className="divide-y divide-border-hairline">
            {data.findings.map((f) => (
              <FindingItem key={f.id} f={f} />
            ))}
          </ul>
        )}
      </Card>
      <p className="text-xs text-ink-muted">
        A finding appears only when the problem shows in several samples in a row, so a single spike does not raise one. Last sample {timeAgo(mem?.collected_at ?? db?.collected_at)}.
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// MariaDB
// ---------------------------------------------------------------------------

const CONFIG_ROWS: { key: string; label: string; bytes?: boolean }[] = [
  { key: 'innodb_buffer_pool_size', label: 'Buffer pool size', bytes: true },
  { key: 'innodb_io_capacity', label: 'I/O capacity' },
  { key: 'innodb_io_capacity_max', label: 'I/O capacity max' },
  { key: 'innodb_log_file_size', label: 'Redo log size', bytes: true },
  { key: 'innodb_log_buffer_size', label: 'Log buffer size', bytes: true },
  { key: 'innodb_flush_log_at_trx_commit', label: 'Flush log at commit' },
  { key: 'innodb_flush_method', label: 'Flush method' },
  { key: 'max_connections', label: 'Max connections' },
  { key: 'tmp_table_size', label: 'Temp table size', bytes: true },
  { key: 'max_heap_table_size', label: 'Max in-memory table', bytes: true },
  { key: 'table_open_cache', label: 'Table open cache' },
  { key: 'open_files_limit', label: 'Open files limit' },
  { key: 'long_query_time', label: 'Slow query threshold (s)' },
  { key: 'slow_query_log', label: 'Slow query log' },
  { key: 'version', label: 'Version' },
]

function MariadbTab({ data }: { data: HostDiagnosis }) {
  const m = data.mariadb
  if (!m.has_data) {
    return (
      <Empty text="No MariaDB data for this host. It needs a bench path set, and the site's database login on that host must be readable by the SSH user." />
    )
  }
  const l = m.latest
  const trend = m.trend
  const sizes = m.sizes
  const poolMb = sampleNumber(l, 'bp_size_mb')
  const dataMb = sizes ? sizes.data_mb + sizes.index_mb : null
  const hit = sampleNumber(l, 'bp_hit_percent')
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Buffer pool" value={fmtMb(poolMb)} sub={dataMb != null ? `for ${fmtMb(dataMb)} of data` : 'data size not read yet'} tone={poolMb != null && dataMb != null && poolMb < dataMb * 0.5 ? 'warning' : 'ok'} />
        <StatTile label="Cache hit ratio" value={hit == null ? '—' : fmtPct(hit, 1)} sub={`${fmtNum(sampleNumber(l, 'bp_disk_reads_per_s'))} disk reads/s`} tone={hit == null ? 'muted' : hit < 95 ? 'critical' : hit < 99 ? 'warning' : 'ok'} />
        <StatTile label="Queries" value={`${fmtNum(sampleNumber(l, 'queries_per_s'), 1)}/s`} sub={`${fmtNum(sampleNumber(l, 'slow_queries_per_s'), 1)} slow/s`} />
        <StatTile label="Memory held" value={fmtMb(sampleNumber(l, 'mariadbd_rss_mb'))} sub={`${fmtMb(sampleNumber(l, 'mariadbd_swap_mb'))} swapped out`} tone={(sampleNumber(l, 'mariadbd_swap_mb') ?? 0) >= 50 ? 'critical' : 'ok'} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SampleChart title="Cache hit ratio" rows={trend} fields={[{ key: 'bp_hit_percent', label: 'Hit ratio %', colorVar: '--color-accent', fill: true }]} formatter={(v) => `${Math.round(v)}%`} note="Share of page reads served from memory. Low values with many disk reads mean the pool is too small for the data." />
        <SampleChart title="Reads from disk per second" rows={trend} fields={[{ key: 'bp_disk_reads_per_s', label: 'Pages read from disk/s', colorVar: '--color-status-warning', fill: true }]} />
        <SampleChart title="Writing and flushing per second" rows={trend} fields={[
          { key: 'data_writes_per_s', label: 'Data writes/s', colorVar: '--color-accent' },
          { key: 'pages_flushed_per_s', label: 'Pages flushed/s', colorVar: '--color-status-warning' },
          { key: 'data_fsyncs_per_s', label: 'fsyncs/s', colorVar: '--color-status-good' },
        ]} note={`innodb_io_capacity is ${fmtNum(sampleNumber(l, 'io_capacity'))} (max ${fmtNum(sampleNumber(l, 'io_capacity_max'))}). Flushing pinned at that line while the disk is idle means the setting is the limit.`} />
        <SampleChart title="Redo log and changed pages" rows={trend} fields={[
          { key: 'checkpoint_age_percent', label: 'Redo log full %', colorVar: '--color-status-critical' },
          { key: 'bp_dirty_percent', label: 'Changed pages %', colorVar: '--color-accent' },
        ]} formatter={(v) => `${Math.round(v)}%`} />
        <SampleChart title="Queries per second" rows={trend} fields={[
          { key: 'queries_per_s', label: 'Queries/s', colorVar: '--color-accent', fill: true },
          { key: 'slow_queries_per_s', label: 'Slow queries/s', colorVar: '--color-status-critical' },
        ]} />
        <SampleChart title="Connections" rows={trend} fields={[
          { key: 'threads_connected', label: 'Connected', colorVar: '--color-accent' },
          { key: 'threads_running', label: 'Running', colorVar: '--color-status-warning' },
        ]} note={`Limit ${fmtNum(sampleNumber(l, 'max_connections'))}.`} />
        <SampleChart title="Row locks" rows={trend} fields={[
          { key: 'row_lock_waits_per_s', label: 'Lock waits/s', colorVar: '--color-status-warning' },
          { key: 'row_lock_avg_wait_ms', label: 'Average wait (ms)', colorVar: '--color-status-critical' },
        ]} />
        <SampleChart title="Temporary tables on disk" rows={trend} fields={[{ key: 'tmp_disk_percent', label: 'On disk %', colorVar: '--color-status-warning', fill: true }]} formatter={(v) => `${Math.round(v)}%`} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Settings that matter" noPadding>
          <dl className="divide-y divide-border-hairline text-sm">
            {CONFIG_ROWS.filter((r) => m.config[r.key] != null).map((r) => {
              const raw = m.config[r.key]
              return (
                <div key={r.key} className="flex items-center justify-between gap-3 px-5 py-2">
                  <dt className="text-ink-muted">{r.label}</dt>
                  <dd className="truncate font-medium text-ink-primary">{r.bytes && typeof raw === 'number' ? fmtBytes(raw) : String(raw)}</dd>
                </div>
              )
            })}
          </dl>
        </Card>
        <Card title="Data size">
          {sizes ? (
            <>
              <p className="text-sm text-ink-primary">
                <span className="text-xl font-semibold">{fmtMb(sizes.data_mb + sizes.index_mb)}</span>
                <span className="ml-2 text-ink-muted">
                  {fmtMb(sizes.data_mb)} data + {fmtMb(sizes.index_mb)} indexes · {fmtMb(sizes.free_mb)} free inside tables
                </span>
              </p>
              <p className="mt-1 text-xs text-ink-muted">
                {sizes.sites_counted} of {sizes.sites_total ?? sizes.sites_counted} site(s) counted · read {timeAgo(sizes.collected_at)}
              </p>
              <h4 className="mb-1 mt-4 text-xs font-semibold uppercase tracking-widest text-ink-muted">Largest tables</h4>
              <ul className="space-y-1 text-sm">
                {sizes.top_tables.slice(0, 6).map((t) => (
                  <li key={`${t.site}-${t.table}`} className="flex justify-between gap-3">
                    <span className="truncate text-ink-primary" title={`${t.site} · ${t.table}`}>
                      {t.table} <span className="text-ink-muted">· {t.site}</span>
                    </span>
                    <span className="shrink-0 font-medium">{fmtMb(t.mb)}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-sm text-ink-muted">Data size is read every 5 minutes; it will appear shortly.</p>
          )}
        </Card>
      </div>
      {m.limited.length > 0 && (
        <p className="text-xs text-ink-muted">
          Not readable with this database login: {m.limited.join(', ')}. Open transactions and other users' queries need the PROCESS privilege; grant it to see which transaction is holding back cleanup.
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Redis
// ---------------------------------------------------------------------------

function RedisTab({ data }: { data: HostDiagnosis }) {
  const labels = Object.keys(data.redis?.instances ?? {})
  if (!data.redis?.has_data || labels.length === 0) {
    return <Empty text="No Redis data for this host. It needs a bench path set, and the bench's Redis servers must be reachable from the host itself." />
  }
  return (
    <div className="flex flex-col gap-6">
      {labels.map((label) => (
        <RedisInstance key={label} label={label} latest={data.redis.instances[label]} trend={data.redis.trend?.[label] ?? []} />
      ))}
    </div>
  )
}

function RedisInstance({ label, latest, trend }: { label: string; latest: DiagnosisSample; trend: DiagnosisSample[] }) {
  const used = sampleNumber(latest, 'used_mb')
  const limit = sampleNumber(latest, 'maxmemory_mb')
  const pct = limit ? ((used ?? 0) / limit) * 100 : null
  const hit = sampleNumber(latest, 'hit_percent')
  const roles = String(latest.roles ?? '').split('+').filter(Boolean)
  const isQueue = roles.includes('queue')
  const persistenceOk = Boolean(sampleNumber(latest, 'persistence_ok'))
  // Draw the limit as a flat line only when there is one.
  const rows = trend.map((r) => ({ ...r, limit_mb: sampleNumber(r, 'maxmemory_mb') || null }))
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-ink-primary">{label}</h3>
        {roles.map((r) => (
          <Pill key={r} tone="info">
            {r}
          </Pill>
        ))}
        {!persistenceOk && <Pill tone="critical">cannot save to disk</Pill>}
        <span className="text-xs text-ink-muted">
          policy {String(latest.maxmemory_policy ?? '—')} · {fmtNum(sampleNumber(latest, 'keys'))} keys · last seen {timeAgo(latest.collected_at)}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Memory" value={fmtMb(used)} sub={limit ? `${fmtPct(pct)} of ${fmtMb(limit)} limit` : 'no memory limit'} tone={pct == null ? (isQueue ? 'ok' : 'muted') : pct >= 97 ? 'critical' : pct >= 90 ? 'warning' : 'ok'} />
        <StatTile label={isQueue ? 'Commands' : 'Cache hit ratio'} value={isQueue ? `${fmtNum(sampleNumber(latest, 'ops_per_s'), 0)}/s` : hit == null ? '—' : fmtPct(hit, 1)} sub={isQueue ? 'job queue traffic' : `${fmtNum(sampleNumber(latest, 'ops_per_s'), 0)} commands/s`} tone={!isQueue && hit != null && hit < 80 ? 'warning' : 'ok'} />
        <StatTile label="Clients" value={fmtNum(sampleNumber(latest, 'connected_clients'))} sub={`${fmtNum(sampleNumber(latest, 'blocked_clients'))} waiting for work · limit ${fmtNum(sampleNumber(latest, 'max_clients'))}`} />
        <StatTile label="Evicted" value={fmtNum(sampleNumber(latest, 'evicted'))} sub={isQueue ? 'must stay 0 for a queue' : 'keys dropped to free memory'} tone={(sampleNumber(latest, 'evicted') ?? 0) > 0 && isQueue ? 'critical' : 'ok'} />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SampleChart title="Memory in use" rows={rows} fields={[
          { key: 'used_mb', label: 'In use (MB)', colorVar: '--color-accent', fill: true },
          { key: 'limit_mb', label: 'Limit (MB)', colorVar: '--color-status-critical' },
        ]} formatter={(v) => fmtMb(v)} />
        <SampleChart title="Commands per second" rows={rows} fields={[{ key: 'ops_per_s', label: 'Commands/s', colorVar: '--color-accent', fill: true }]} />
        {!isQueue && (
          <SampleChart title="Cache hit ratio" rows={rows} fields={[{ key: 'hit_percent', label: 'Hit ratio %', colorVar: '--color-status-good', fill: true }]} formatter={(v) => `${Math.round(v)}%`} note="Share of key lookups that found their key. Low just after a restart is normal." />
        )}
        <SampleChart title="Clients" rows={rows} fields={[
          { key: 'connected_clients', label: 'Connected', colorVar: '--color-accent' },
          { key: 'blocked_clients', label: 'Waiting for work', colorVar: '--color-status-warning' },
        ]} note={isQueue ? 'Background workers wait on the queue, so a few waiting clients are normal.' : undefined} />
        <SampleChart title="Keys evicted per second" rows={rows} fields={[{ key: 'evicted_per_s', label: 'Evicted/s', colorVar: '--color-status-critical', fill: true }]} />
        <SampleChart title="Pause while saving to disk (ms)" rows={rows} fields={[{ key: 'fork_ms', label: 'Last fork (ms)', colorVar: '--color-status-warning' }]} />
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

function MemoryTab({ data }: { data: HostDiagnosis }) {
  const l = data.latest
  const trend = data.memory_trend.map((r) => {
    const total = sampleNumber(r, 'mem_total_mb')
    const avail = sampleNumber(r, 'mem_available_mb')
    return { ...r, used_mb: total != null && avail != null ? total - avail : null }
  })
  const procs = l?.top_processes ?? []
  const limit = sampleNumber(l, 'cgroup_mem_limit_mb')
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Available" value={fmtMb(sampleNumber(l, 'mem_available_mb'))} sub={`of ${fmtMb(sampleNumber(l, 'mem_total_mb'))}`} />
        <StatTile label="Swap in use" value={fmtMb(sampleNumber(l, 'swap_used_mb'))} sub={`of ${fmtMb(sampleNumber(l, 'swap_total_mb'))}`} tone={(sampleNumber(l, 'swap_used_mb') ?? 0) > 0 ? 'warning' : 'ok'} />
        <StatTile label="Container limit" value={limit ? fmtMb(limit) : 'None'} sub={limit ? `${fmtMb(sampleNumber(l, 'cgroup_mem_current_mb'))} in use` : 'no memory limit set'} />
        <StatTile label="CPU held back" value={fmtPct(sampleNumber(l, 'cpu_throttled_percent'))} sub={sampleNumber(l, 'cpu_limit_cores') ? `${sampleNumber(l, 'cpu_limit_cores')} core limit` : 'no CPU limit set'} tone={(sampleNumber(l, 'cpu_throttled_percent') ?? 0) >= 10 ? 'warning' : 'ok'} />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SampleChart title="Memory in use" rows={trend} fields={[{ key: 'used_mb', label: 'In use (MB)', colorVar: '--color-accent', fill: true }]} formatter={(v) => fmtMb(v)} note="Total minus what the system can still hand out (cache that can be freed counts as available)." />
        <SampleChart title="Swap activity" rows={trend} fields={[
          { key: 'swap_in_per_s', label: 'Swapped in (pages/s)', colorVar: '--color-status-warning' },
          { key: 'swap_out_per_s', label: 'Swapped out (pages/s)', colorVar: '--color-status-critical' },
        ]} note="Anything steady here means memory is too small: data is being pushed to disk and read back." />
        <SampleChart title="Time spent waiting (pressure)" rows={trend} fields={[
          { key: 'psi_cpu_some', label: 'Waiting for CPU %', colorVar: '--color-accent' },
          { key: 'psi_memory_some', label: 'Waiting for memory %', colorVar: '--color-status-warning' },
          { key: 'psi_io_some', label: 'Waiting for disk %', colorVar: '--color-status-critical' },
        ]} formatter={(v) => `${Math.round(v)}%`} note="Share of the last 10 seconds that some tasks were stalled on that resource." />
        <Card title="Largest processes" noPadding>
          {procs.length === 0 ? (
            <p className="px-5 py-6 text-sm text-ink-muted">Not reported yet.</p>
          ) : (
            <ul className="divide-y divide-border-hairline text-sm">
              {procs.map((p) => (
                <li key={p.pid} className="flex items-center justify-between gap-3 px-5 py-2">
                  <span className="truncate text-ink-primary">
                    {p.name} <span className="text-ink-muted">· pid {p.pid}</span>
                  </span>
                  <span className="shrink-0 font-medium">
                    {fmtMb(p.rss_mb)}
                    {p.swap_mb > 0 ? <span className="ml-2 text-status-warning">{fmtMb(p.swap_mb)} swapped</span> : null}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Disk I/O
// ---------------------------------------------------------------------------

const DEVICE_COLORS = ['--color-accent', '--color-status-warning', '--color-status-critical']

function DiskTab({ data }: { data: HostDiagnosis }) {
  const devices = Object.keys(data.io_trend)
  if (devices.length === 0) return <Empty text="No disk activity recorded in this range yet." />

  // One shared time axis across the devices, so they can be drawn together.
  const times = Array.from(new Set(devices.flatMap((d) => data.io_trend[d].map((r) => r.collected_at)))).sort()
  const rowsFor = (key: string) =>
    times.map((t) => {
      const row: DiagnosisSample = { collected_at: t }
      devices.forEach((d) => {
        const hit = data.io_trend[d].find((r) => r.collected_at === t)
        row[`${d}__${key}`] = hit ? sampleNumber(hit, key) : null
      })
      return row
    })
  const fieldsFor = (key: string): ChartField[] => devices.map((d, i) => ({ key: `${d}__${key}`, label: d, colorVar: DEVICE_COLORS[i % DEVICE_COLORS.length] }))

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {data.io_latest.slice(0, 3).map((d) => (
          <StatTile key={String(d.device)} label={String(d.device)} value={fmtPct(sampleNumber(d, 'util_percent'))} sub={`${fmtNum(sampleNumber(d, 'await_ms'), 1)} ms wait · ${fmtNum(sampleNumber(d, 'read_iops'))} r / ${fmtNum(sampleNumber(d, 'write_iops'))} w IOPS`} tone={(sampleNumber(d, 'util_percent') ?? 0) >= 90 ? 'critical' : (sampleNumber(d, 'util_percent') ?? 0) >= 60 ? 'warning' : 'ok'} />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SampleChart title="How busy the disk is" rows={rowsFor('util_percent')} fields={fieldsFor('util_percent')} formatter={(v) => `${Math.round(v)}%`} note="Inside a container these are the node's shared disks, not the container's alone." />
        <SampleChart title="Average wait per request (ms)" rows={rowsFor('await_ms')} fields={fieldsFor('await_ms')} />
        <SampleChart title="Reads per second" rows={rowsFor('read_iops')} fields={fieldsFor('read_iops')} />
        <SampleChart title="Writes per second" rows={rowsFor('write_iops')} fields={fieldsFor('write_iops')} />
      </div>
    </div>
  )
}
