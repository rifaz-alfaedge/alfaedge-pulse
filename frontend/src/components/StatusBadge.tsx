import { Badge } from '@rtcamp/frappe-ui-react'

const THEME_BY_STATUS: Record<string, 'green' | 'gray' | 'orange' | 'red'> = {
  running: 'green',
  Online: 'green',
  stopped: 'gray',
  Offline: 'gray',
  paused: 'orange',
  Warning: 'orange',
  Critical: 'red',
  unknown: 'gray',
  Success: 'green',
  Failed: 'red',
  Running: 'orange',
  Up: 'green',
  Down: 'red',
  Pending: 'orange',
  Maintenance: 'gray',
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge label={status} theme={THEME_BY_STATUS[status] ?? 'gray'} size="md" variant="subtle" />
}
