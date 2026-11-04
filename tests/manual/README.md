# Manual test scripts

Plain Python scripts that run against a Frappe site with `bench execute`.
Each one rolls its database changes back, captures alert sends, makes
`frappe.db.commit` a no-op where it writes, and uses synthetic records. None
of them contacts a real host (the SSH tests talk to an in-process server on
localhost).

```bash
cd /home/ubuntu/frappe-bench
bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/<file>.py").read(), {"__name__": "t"})'
```

| File | Covers |
|---|---|
| `test_ssh_pull.py` | SSH client: connect, host-key pin and change, reused connection, wrong key, closed port; collector report through ingest |
| `test_performance.py` | Memory, swap, OOM, container limits, pressure and disk I/O rates; counter resets and gaps; diagnosis rules; API |
| `test_idle_pollers.py` | Uptime loop idles when nothing is enabled; unchanged host failures are not rewritten |
| `test_safeguards.py` | Site-config opt-in, queue fallback, Warpgate outage alert, bulk host creation, retention |

They print `PASS`/`FAIL` per check and `ALL PASS` at the end.
