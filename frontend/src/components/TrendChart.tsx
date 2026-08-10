import { useEffect, useRef } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'

export interface TrendSeries {
  label: string
  data: (number | null)[]
  /** A CSS custom property name (e.g. `--color-accent`), resolved fresh on
   * every (re)build — canvas needs a concrete color, it can't read
   * `var(...)` live the way the rest of this app's SVG/CSS pieces do. */
  colorVar: string
  fill?: boolean
}

function formatBucketLabel(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000)
  const isMidnight = d.getHours() === 0 && d.getMinutes() === 0
  const date = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  if (isMidnight) return date
  return `${date}, ${d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`
}

/** Shared uPlot wrapper for the AI Usage and Uptime trend charts.
 *
 * uPlot's cursor crosshair alone doesn't show values — its only built-in
 * value readout is the legend area, which for a single-series chart would
 * mean looking away from the cursor to read it. This builds a small
 * floating tooltip instead (a standard uPlot pattern via the `setCursor`
 * hook) that follows the mouse and shows the exact value(s) under it.
 *
 * uPlot bakes colors into the canvas at draw time, so unlike this app's
 * hand-rolled SVG components (which read `var(--color-*)` directly and
 * re-theme for free), this rebuilds from scratch whenever the `.dark`
 * class on `<html>` changes — see main.tsx for where that class gets
 * toggled — and whenever the container resizes. */
export function TrendChart({
  x,
  series,
  height = 200,
  valueFormatter,
}: {
  /** X values as Unix seconds (uPlot's own time axis expects this). */
  x: number[]
  series: TrendSeries[]
  height?: number
  valueFormatter?: (v: number) => string
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<uPlot | null>(null)
  const tooltipRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    function ensureTooltip(): HTMLDivElement {
      let tooltip = tooltipRef.current
      if (!tooltip) {
        tooltip = document.createElement('div')
        Object.assign(tooltip.style, {
          position: 'absolute',
          pointerEvents: 'none',
          zIndex: '10',
          display: 'none',
          borderRadius: '10px',
          padding: '8px 11px',
          fontSize: '12px',
          lineHeight: '1.5',
          whiteSpace: 'nowrap',
          boxShadow: '0 4px 14px rgba(0,0,0,0.16)',
        } satisfies Partial<CSSStyleDeclaration>)
        container!.appendChild(tooltip)
        tooltipRef.current = tooltip
      }
      return tooltip
    }

    function build() {
      chartRef.current?.destroy()
      chartRef.current = null
      if (!container || x.length === 0) return

      const styles = getComputedStyle(document.documentElement)
      const resolve = (name: string) => styles.getPropertyValue(name).trim()
      const muted = resolve('--color-ink-muted')
      const grid = resolve('--color-gridline')
      const fontFamily = resolve('--font-sans') || 'sans-serif'
      const seriesColors = series.map((s) => resolve(s.colorVar))

      const tooltip = ensureTooltip()
      tooltip.style.background = resolve('--color-surface-card')
      tooltip.style.border = `1px solid ${resolve('--color-border-hairline')}`
      tooltip.style.color = resolve('--color-ink-primary')

      const opts: uPlot.Options = {
        width: container.clientWidth || 400,
        height,
        padding: [10, 8, 0, 0],
        cursor: { points: { size: 6 } },
        legend: { show: false },
        scales: { x: { time: true } },
        axes: [
          { stroke: muted, grid: { stroke: grid, width: 1 }, font: `11px ${fontFamily}` },
          {
            stroke: muted,
            grid: { stroke: grid, width: 1 },
            font: `11px ${fontFamily}`,
            values: (_u, vals) => vals.map((v) => (valueFormatter ? valueFormatter(v) : String(v))),
          },
        ],
        series: [
          {},
          ...series.map((s, i) => ({
            label: s.label,
            stroke: seriesColors[i],
            width: 2,
            fill: s.fill ? `${seriesColors[i]}22` : undefined,
            points: { show: false },
          })),
        ],
        hooks: {
          setCursor: [
            (u) => {
              const idx = u.cursor.idx
              if (idx == null || idx < 0) {
                tooltip.style.display = 'none'
                return
              }
              const xVal = u.data[0][idx] as number | null
              const lines = series
                .map((s, i) => {
                  const val = u.data[i + 1][idx] as number | null
                  const formatted = val == null ? '—' : valueFormatter ? valueFormatter(val) : String(val)
                  return `<div style="display:flex;align-items:center;gap:6px;margin-top:2px;"><span style="display:inline-block;width:8px;height:8px;border-radius:9999px;background:${seriesColors[i]};flex-shrink:0;"></span><span>${s.label}: <strong>${formatted}</strong></span></div>`
                })
                .join('')
              tooltip.innerHTML =
                (xVal != null ? `<div style="color:${muted}">${formatBucketLabel(xVal)}</div>` : '') + lines
              tooltip.style.display = 'block'

              const left = u.cursor.left ?? 0
              const top = u.cursor.top ?? 0
              const tooltipWidth = tooltip.offsetWidth
              const maxLeft = container.clientWidth - tooltipWidth - 8
              tooltip.style.left = `${Math.max(4, Math.min(left + 14, maxLeft))}px`
              tooltip.style.top = `${Math.max(0, top - 12)}px`
            },
          ],
        },
      }

      chartRef.current = new uPlot(opts, [x, ...series.map((s) => s.data)], container)
    }

    build()

    const resizeObserver = new ResizeObserver(() => build())
    resizeObserver.observe(container)

    const themeObserver = new MutationObserver(build)
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })

    return () => {
      resizeObserver.disconnect()
      themeObserver.disconnect()
      chartRef.current?.destroy()
      chartRef.current = null
      tooltipRef.current?.remove()
      tooltipRef.current = null
    }
    // Re-run whenever the actual data changes, not just on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(x), JSON.stringify(series), height])

  return <div ref={containerRef} className="relative [&_.uplot]:w-full" />
}
