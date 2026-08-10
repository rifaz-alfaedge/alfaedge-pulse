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

/** Shared uPlot wrapper for the AI Usage and Uptime trend charts.
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
  /** X values as Unix seconds (uPlot's own time axis expects this) — pass
   * plain sequential numbers instead if `xIsTime` is false. */
  x: number[]
  series: TrendSeries[]
  height?: number
  valueFormatter?: (v: number) => string
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<uPlot | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    function build() {
      chartRef.current?.destroy()
      chartRef.current = null
      if (!container || x.length === 0) return

      const styles = getComputedStyle(document.documentElement)
      const resolve = (name: string) => styles.getPropertyValue(name).trim()
      const muted = resolve('--color-ink-muted')
      const grid = resolve('--color-gridline')
      const fontFamily = resolve('--font-sans') || 'sans-serif'

      const opts: uPlot.Options = {
        width: container.clientWidth || 400,
        height,
        padding: [10, 8, 0, 0],
        cursor: { points: { size: 6 } },
        legend: { show: series.length > 1 },
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
          ...series.map((s) => {
            const color = resolve(s.colorVar)
            return {
              label: s.label,
              stroke: color,
              width: 2,
              fill: s.fill ? `${color}22` : undefined,
              points: { show: false },
            }
          }),
        ],
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
    }
    // Re-run whenever the actual data changes, not just on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(x), JSON.stringify(series), height])

  return <div ref={containerRef} className="[&_.uplot]:w-full" />
}
