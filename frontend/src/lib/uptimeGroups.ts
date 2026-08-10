import type { UptimeMonitorType, UptimeSite } from './types'

/** One row per distinct site_name (the sync key — see uptime_monitor/api.py),
 * not per Uptime Site doc: the same site has one doc per instance, and both
 * the dashboard grid and the fleet-wide severity lists show that as one row
 * per site, not a repeat per instance. */
export interface SiteGroup {
  siteName: string
  monitorType: UptimeMonitorType
  target: string | undefined
  sites: UptimeSite[]
}

export function targetOf(site: UptimeSite): string | undefined {
  return site.monitor_type === 'HTTP(s)' ? site.url : site.monitor_type === 'TCP' ? `${site.hostname}:${site.port}` : site.hostname
}

export function groupSites(sites: UptimeSite[]): SiteGroup[] {
  const byName = new Map<string, UptimeSite[]>()
  for (const site of sites) {
    const group = byName.get(site.site_name)
    if (group) group.push(site)
    else byName.set(site.site_name, [site])
  }
  return Array.from(byName.values())
    .map((group) => ({ siteName: group[0].site_name, monitorType: group[0].monitor_type, target: targetOf(group[0]), sites: group }))
    .sort((a, b) => a.siteName.localeCompare(b.siteName))
}
