"""OpenRouter client, sync jobs, scheduling, alerting and read API. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_openrouter.py").read(), {"__name__": "t"})'

OpenRouter is never contacted: the HTTP layer is replaced, and the key is a recognisable dummy so
the test can prove it never appears in any error message or log. Everything is rolled back.
"""
import datetime as dt
from datetime import datetime, timedelta, timezone

import frappe
import requests

from alfaedge_pulse.alerts import dispatch as dispatch_mod
from alfaedge_pulse.openrouter_client import base as client_mod
from alfaedge_pulse.openrouter_client.base import OpenRouterAPIError, OpenRouterClient
from alfaedge_pulse.openrouter_monitor import api
from alfaedge_pulse.tasks import openrouter_sync as sync

results = []
KEY = "sk-or-mgmt-DUMMY-DO-NOT-LEAK-12345"


def check(name, ok, detail=""):
	results.append((name, bool(ok), detail))


class Resp:
	def __init__(self, status=200, payload=None, text_ok=True):
		self.status_code, self._payload, self._ok = status, payload, text_ok

	def json(self):
		if not self._ok:
			raise ValueError("not json")
		return self._payload


def client_with(responses, calls=None):
	"""A client whose HTTP layer answers from ``responses`` (a list; an exception is raised)."""
	c = OpenRouterClient("https://openrouter.ai/api/v1/", KEY)
	queue = list(responses)

	def fake_get(url, params=None, timeout=None):
		if calls is not None:
			calls.append((url, params, timeout))
		nxt = queue.pop(0) if len(queue) > 1 else queue[0]
		if isinstance(nxt, Exception):
			raise nxt
		return nxt

	c.session.get = fake_get
	return c


def message_of(fn):
	try:
		fn()
	except OpenRouterAPIError as exc:
		return exc
	return None


orig = {
	"get_cached_doc": frappe.get_cached_doc, "commit": frappe.db.commit, "utcnow": sync.utcnow, "log_error": frappe.log_error,
	"dispatch_alert": sync.dispatch_alert, "dispatch_recovery": sync.dispatch_recovery, "has_open": sync.has_open_alert, "resolve": sync.resolve_alert,
	"enqueue_a": sync.enqueue_activity_sync, "enqueue_b": sync.enqueue_balance_check, "has_key": sync._has_key,
}
logged, sleeps = [], []
frappe.db.commit = lambda: None
real_rollback = frappe.db.rollback
frappe.db.rollback = lambda *a, **k: None  # the error paths roll back; keep the test rows
frappe.log_error = lambda *a, **k: logged.append((a, k))

try:
	# ---- client: errors, retries, no key anywhere ----
	for status, fragment, retryable in ((401, "rejected the key", False), (403, "Management API key", False), (429, "rate limiting", True), (503, "server error", True), (404, "answered 404", False)):
		exc = message_of(lambda: client_with([Resp(status)]).get("/credits"))
		check(f"HTTP {status}: message and retryable flag", exc and fragment in str(exc) and exc.retryable is retryable, str(exc))
		check(f"HTTP {status}: the key is not in the message", exc and KEY not in str(exc))
	exc = message_of(lambda: client_with([requests.ConnectionError(f"boom {KEY}")]).get("/credits"))
	check("a network error is retryable and says only the exception class", exc and exc.retryable and "ConnectionError" in str(exc) and KEY not in str(exc), str(exc))
	exc = message_of(lambda: client_with([Resp(200, text_ok=False)]).get("/credits"))
	check("a non-JSON answer is an error", exc and "not JSON" in str(exc) and not exc.retryable, str(exc))

	calls = []
	c = client_with([Resp(503), Resp(503), Resp(200, {"data": {"total_credits": 10, "total_usage": 4}})], calls)
	got = c.get_credits(sleep=sleeps.append)
	check("a transient failure is retried and then succeeds", got == {"total_credits": 10.0, "total_usage": 4.0} and len(calls) == 3, (got, len(calls)))
	check("the retries wait 5 then 20 seconds", sleeps == [5, 20], sleeps)
	sleeps.clear()
	c = client_with([Resp(503)])
	exc = message_of(lambda: c.get_credits(sleep=sleeps.append))
	check("it gives up after 4 attempts (5, 20, 60 s waits)", exc and sleeps == [5, 20, 60], sleeps)
	sleeps.clear()
	c = client_with([Resp(401)])
	message_of(lambda: c.get_credits(sleep=sleeps.append))
	check("a refused key is not retried", sleeps == [])
	check("credits without the data envelope still parse", client_with([Resp(200, {"total_credits": "5.5", "total_usage": "1.25"})]).get_credits() == {"total_credits": 5.5, "total_usage": 1.25})
	exc = message_of(lambda: client_with([Resp(200, {"data": {"total_credits": 1}})]).get_credits())
	check("credits with a missing field is an error", exc and "total_usage" in str(exc), str(exc))
	calls = []
	rows = client_with([Resp(200, {"data": [{"date": "2026-11-01"}, "junk", {"date": "2026-11-02"}]})], calls).get_activity("2026-11-01")
	check("activity returns the row dicts and sends the date", len(rows) == 2 and calls[0][1] == {"date": "2026-11-01"} and calls[0][0].endswith("/api/v1/activity"), (rows, calls))
	check("activity accepts a bare list and no date", len(client_with([Resp(200, [{"date": "2026-11-01"}])]).get_activity()) == 1)
	exc = message_of(lambda: client_with([Resp(200, {"data": {"oops": 1}})]).get_activity())
	check("activity that is not a list is an error", exc and "not a list" in str(exc))
	c = client_with([Resp(200, {"data": []})])
	check("the key is sent only as a Bearer header", c.session.headers["Authorization"] == f"Bearer {KEY}" and c.base_url == "https://openrouter.ai/api/v1")
	src = open(client_mod.__file__).read()
	check("the client source never formats the key into a string other than the header", src.count("management_key") == src.count("management_key: str") + src.count("f\"Bearer {management_key}\""), src.count("management_key"))

	# ---- row mapping ----
	full = {"date": "2026-11-05", "model": "anthropic/claude-3.5-sonnet", "model_permaslug": "anthropic/claude-3.5-sonnet-20241022", "endpoint_id": "ep-123",
	        "provider_name": "Anthropic", "usage": 1.234567891, "byok_usage_inference": 0.5, "requests": 12, "prompt_tokens": 1000, "completion_tokens": 400, "reasoning_tokens": 50}
	m = sync.map_row(full)
	check("a full row maps every field", m["row_key"] == "2026-11-05-ep-123" and m["usage_usd"] == 1.234568 and m["byok_usage_usd"] == 0.5 and m["requests"] == 12 and m["reasoning_tokens"] == 50 and str(m["date"]) == "2026-11-05", m)
	m = sync.map_row({"date": "2026-11-05T00:00:00Z", "model": "x/y"})
	check("missing numbers become 0 and a timestamp becomes its date", m["usage_usd"] == 0 and m["requests"] == 0 and m["prompt_tokens"] == 0 and str(m["date"]) == "2026-11-05", m)
	a, b = sync.map_row({"date": "2026-11-05", "model": "x/y", "provider_name": "P"}), sync.map_row({"date": "2026-11-05", "model": "x/y", "provider_name": "P"})
	check("a missing endpoint id gets a stable hash", a["endpoint_id"] == b["endpoint_id"] and len(a["endpoint_id"]) == 16 and a["row_key"] == b["row_key"])
	check("a different provider gets a different key", sync.map_row({"date": "2026-11-05", "model": "x/y", "provider_name": "Q"})["row_key"] != a["row_key"])
	check("an endpoint id with slashes is made safe for the row key", "/" not in sync.map_row({"date": "2026-11-05", "endpoint_id": "a/b c"})["row_key"])
	check("a row with no date is rejected", sync.map_row({"model": "x"}) is None and sync.map_row({"date": "not a date"}) is None)
	m = sync.map_row({"date": "2026-11-05", "usage": "oops", "requests": None, "prompt_tokens": "7"})
	check("garbage numbers are 0 and numeric strings work", m["usage_usd"] == 0 and m["requests"] == 0 and m["prompt_tokens"] == 7)

	# ---- upsert: idempotent ----
	frappe.db.sql("delete from `tabOpenRouter Daily Usage`")
	r1 = {"date": "2026-11-04", "model": "m/a", "endpoint_id": "e1", "provider_name": "P1", "usage": 1.0, "requests": 10, "prompt_tokens": 100, "completion_tokens": 50, "reasoning_tokens": 5}
	r2 = {"date": "2026-11-04", "model": "m/b", "endpoint_id": "e2", "provider_name": "P2", "usage": 2.0, "requests": 20, "prompt_tokens": 200, "completion_tokens": 80, "reasoning_tokens": 0, "byok_usage_inference": 0.3}
	r3 = {"date": "2026-11-05", "model": "m/a", "endpoint_id": "e1", "provider_name": "P1", "usage": 3.0, "requests": 30, "prompt_tokens": 300, "completion_tokens": 90, "reasoning_tokens": 9}
	stored, skipped, latest = sync.upsert_rows([r1, r2, r3, {"model": "no date"}])
	check("first run stores 3 rows, skips the unusable one, reports the latest day", (stored, skipped, str(latest)) == (3, 1, "2026-11-05"), (stored, skipped, latest))
	sync.upsert_rows([r1, r2, r3])
	check("running it again adds nothing (idempotent)", frappe.db.count("OpenRouter Daily Usage") == 3)
	sync.upsert_rows([dict(r1, usage=1.5, requests=11)])
	check("a changed value for the same day and endpoint updates in place", frappe.db.count("OpenRouter Daily Usage") == 3 and frappe.db.get_value("OpenRouter Daily Usage", "2026-11-04-e1", "usage_usd") == 1.5)
	check("two endpoints on one day are two rows", frappe.db.count("OpenRouter Daily Usage", {"date": "2026-11-04"}) == 2)

	# ---- scheduling (pure) ----
	U = lambda d, h=0, mi=0: datetime(2026, 11, d, h, mi, tzinfo=timezone.utc)
	check("never synced: due", sync.activity_due(None, None, U(9, 10)))
	check("never synced but tried 5 minutes ago: wait", not sync.activity_due(None, U(9, 9, 55), U(9, 10)))
	check("never synced, last tried 20 minutes ago: due", sync.activity_due(None, U(9, 9, 40), U(9, 10)))
	check("synced yesterday: not due before 00:35 UTC", not sync.activity_due(U(8, 10), None, U(9, 0, 34)))
	check("synced yesterday: due from 00:35 UTC", sync.activity_due(U(8, 10), None, U(9, 0, 35)))
	check("synced today: not due again", not sync.activity_due(U(9, 0, 40), None, U(9, 15)))
	check("missed several days: catches up at once", sync.activity_due(U(3, 10), None, U(9, 12)))
	check("a failed run is retried after 15 minutes, not before", not sync.activity_due(U(8, 10), U(9, 0, 50), U(9, 1, 0)) and sync.activity_due(U(8, 10), U(9, 0, 50), U(9, 1, 5)))
	check("balance: never checked is due", sync.balance_due(None, U(9, 10), 30))
	check("balance: 29 minutes is not due, 30 is", not sync.balance_due(U(9, 9, 31), U(9, 10), 30) and sync.balance_due(U(9, 9, 30), U(9, 10), 30))
	check("balance: a zero interval counts as one minute", sync.balance_due(U(9, 9, 58), U(9, 10), 0))

	# ---- the cron entry ----
	queued = []
	state = {"enabled": 1, "key": True, "act": None, "att": None, "bal": None, "interval": 30}

	def fake_settings(doctype, *a, **k):
		if doctype == "OpenRouter Settings":
			return frappe._dict(enabled=state["enabled"], last_activity_synced=state["act"], last_activity_attempt=state["att"], last_balance_at=state["bal"], balance_interval_minutes=state["interval"])
		return orig["get_cached_doc"](doctype, *a, **k)

	frappe.get_cached_doc = fake_settings
	sync._has_key = lambda: state["key"]
	sync.enqueue_activity_sync = lambda: queued.append("activity")
	sync.enqueue_balance_check = lambda: queued.append("balance")
	sync.utcnow = lambda: U(9, 10)
	state["enabled"] = 0
	sync.sync_openrouter()
	check("disabled: nothing is queued", queued == [])
	state["enabled"], state["key"] = 1, False
	sync.sync_openrouter()
	check("no key saved: nothing is queued", queued == [])
	state["key"] = True
	sync.sync_openrouter()
	check("enabled with a key and never synced: both jobs are queued", sorted(queued) == ["activity", "balance"], queued)
	queued.clear()
	naive = lambda u: u.astimezone(dt.timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)  # IST, like the site
	orig_tz = frappe.utils.get_system_timezone
	sync.get_system_timezone = lambda: "Asia/Kolkata"
	state.update(act=naive(U(9, 0, 40)), bal=naive(U(9, 9, 50)))
	sync.sync_openrouter()
	check("synced today and balance 20 minutes old: nothing is queued", queued == [], queued)
	state.update(bal=naive(U(9, 9, 20)))
	sync.sync_openrouter()
	check("balance 50 minutes old: only the balance check is queued", queued == ["balance"], queued)
	frappe.get_cached_doc = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("settings unreadable"))
	logged.clear()
	sync.sync_openrouter()
	check("the cron entry never raises, and logs", len(logged) == 1)
	frappe.get_cached_doc = fake_settings

	# ---- the daily usage job ----
	class FakeClient:
		def __init__(self, rows=None, credits=None, error=None):
			self.rows, self.credits_, self.error = rows, credits, error

		def get_activity(self, date=None, sleep=None):
			if self.error:
				raise self.error
			return self.rows

		def get_credits(self, sleep=None):
			if self.error:
				raise self.error
			return self.credits_

	sync.utcnow = orig["utcnow"]
	frappe.db.sql("delete from `tabOpenRouter Daily Usage`")
	n = sync.run_activity_sync(FakeClient(rows=[r1, r2, r3]))
	st = lambda f: frappe.db.get_single_value("OpenRouter Settings", f)
	check("the job stores the rows and returns the count", n == 3 and frappe.db.count("OpenRouter Daily Usage") == 3)
	check("it records the status: rows seen, latest day, no error", st("last_activity_row_count") == 3 and str(st("last_activity_through")) == "2026-11-05" and not st("last_activity_error") and st("last_activity_synced"))
	n = sync.run_activity_sync(FakeClient(rows=[r1, {"nothing": 1}]))
	check("skipped rows are reported on the settings", "1 row(s) skipped" in (st("last_activity_error") or ""), st("last_activity_error"))
	logged.clear()
	try:
		sync.run_activity_sync(FakeClient(error=OpenRouterAPIError("OpenRouter refused this key (403)")))
		check("a failed run raises", False)
	except OpenRouterAPIError:
		check("a failed run raises", True)
	check("and records the error, logs it, and sets the attempt time", "403" in (st("last_activity_error") or "") and logged and st("last_activity_attempt"), (st("last_activity_error"), len(logged)))
	check("the error and log never contain the key", KEY not in (st("last_activity_error") or "") and all(KEY not in repr(x) for x in logged))
	check("a failed run does not touch stored rows", frappe.db.count("OpenRouter Daily Usage") == 3)

	# ---- the balance job and the alert ----
	opened, recovered, resolved = [], [], []
	sync.dispatch_alert = lambda *a, **k: opened.append(a)
	sync.dispatch_recovery = lambda *a, **k: recovered.append(a)
	sync.resolve_alert = lambda *a, **k: resolved.append(a)
	alert_open = {"v": False}
	sync.has_open_alert = lambda *a, **k: alert_open["v"]
	frappe.db.sql("delete from `tabOpenRouter Balance Snapshot`")
	frappe.get_cached_doc = lambda dt, *a, **k: frappe._dict(enabled=1, low_balance_threshold=5, base_url="x", balance_interval_minutes=30) if dt == "OpenRouter Settings" else orig["get_cached_doc"](dt, *a, **k)
	rem = sync.run_balance_check(FakeClient(credits={"total_credits": 100.0, "total_usage": 97.5}))
	snap = frappe.get_all("OpenRouter Balance Snapshot", fields=["*"])[0]
	check("the balance job stores purchased, used and remaining (purchased minus used)", rem == 2.5 and (snap.total_purchased, snap.total_used, snap.remaining) == (100.0, 97.5, 2.5), rem)
	check("it records the remaining credit and clears the error", st("last_remaining") == 2.5 and not st("last_balance_error") and st("last_balance_at"))
	check("below the threshold with no open alert: one alert is raised", len(opened) == 1 and opened[0][0] == "Low Credit Balance" and "$2.50" in opened[0][3] and "$5.00" in opened[0][3], opened)
	alert_open["v"] = True
	sync.run_balance_check(FakeClient(credits={"total_credits": 100.0, "total_usage": 98.0}))
	check("still below with the alert already open: no second alert", len(opened) == 1)
	sync.run_balance_check(FakeClient(credits={"total_credits": 100.0, "total_usage": 50.0}))
	check("back above the threshold: the alert is resolved with a recovery message", len(resolved) == 1 and len(recovered) == 1 and "$50.00" in recovered[0][3], (resolved, recovered))
	alert_open["v"] = False
	check("above the threshold with nothing open: nothing happens", sync.check_low_balance(50, 5) is None and len(opened) == 1)
	check("a threshold of 0 never alerts", sync.check_low_balance(0.01, 0) is None and len(opened) == 1)
	alert_open["v"] = True
	check("a threshold of 0 closes an alert left open", sync.check_low_balance(0.01, 0) == "resolved")
	alert_open["v"] = False
	logged.clear()
	check("a failed balance check returns None, does not raise", sync.run_balance_check(FakeClient(error=OpenRouterAPIError("OpenRouter rejected the key (401)"))) is None)
	check("and records the error and logs it", "401" in (st("last_balance_error") or "") and logged)
	check("a failed check does not add a snapshot", frappe.db.count("OpenRouter Balance Snapshot") == 3)
	frappe.get_cached_doc = lambda dt, *a, **k: frappe._dict(enabled=0) if dt == "OpenRouter Settings" else orig["get_cached_doc"](dt, *a, **k)
	check("disabled: the balance job does nothing", sync.run_balance_check(FakeClient(credits={"total_credits": 1, "total_usage": 0})) is None and frappe.db.count("OpenRouter Balance Snapshot") == 3)
	try:
		sync.run_activity_sync(FakeClient(rows=[]))
		check("disabled: the usage job refuses to run", False)
	except OpenRouterAPIError:
		check("disabled: the usage job refuses to run", True)
	frappe.get_cached_doc = orig["get_cached_doc"]

	# ---- read API ----
	frappe.set_user("Administrator")
	frappe.db.sql("delete from `tabOpenRouter Daily Usage`")
	frappe.db.sql("delete from `tabOpenRouter Balance Snapshot`")
	sync.upsert_rows([r1, r2, r3, dict(r3, endpoint_id="e3", model="m/c", provider_name="P1", usage=0.5, requests=5, prompt_tokens=10, completion_tokens=5, reasoning_tokens=1)])
	s = api.get_summary("2026-11-04 00:00:00", "2026-11-05 23:59:59")
	check("summary totals the range", s["total_cost"] == 6.5 and s["total_requests"] == 65 and s["prompt_tokens"] == 610 and s["completion_tokens"] == 225 and s["reasoning_tokens"] == 15, s)
	check("tokens are prompt plus completion; reasoning is not added again", s["total_tokens"] == 835)
	check("BYOK spend is separate from total spend", s["byok_cost"] == 0.3 and s["total_cost"] == 6.5)
	check("it reports the latest day with data and no credits yet", s["latest_day"] == "2026-11-05" and s["credits"] is None)
	check("a one-day range is inclusive", api.get_summary("2026-11-04", "2026-11-04")["total_requests"] == 30)
	check("a range with no data is all zeros", api.get_summary("2026-10-01", "2026-10-02")["total_cost"] == 0)
	check("the provider filter works", api.get_summary("2026-11-04", "2026-11-05", provider='["P2"]')["total_requests"] == 20)
	check("the model filter works, given as a list too", api.get_summary("2026-11-04", "2026-11-05", model=["m/a"])["total_requests"] == 40)
	check("both filters combine", api.get_summary("2026-11-04", "2026-11-05", provider='["P1"]', model='["m/c"]')["total_requests"] == 5)
	t = api.get_trend("2026-11-04", "2026-11-05")
	check("the trend has one point per UTC day, in order", [p["bucket"] for p in t] == ["2026-11-04", "2026-11-05"] and t[0]["total_cost"] == 3.0 and t[1]["total_cost"] == 3.5, t)
	check("each trend point carries the token split", t[1]["prompt_tokens"] == 310 and t[1]["completion_tokens"] == 95 and t[1]["reasoning_tokens"] == 10 and t[1]["total_tokens"] == 405)
	bm = api.get_breakdown("model", "2026-11-04", "2026-11-05")
	check("by model: biggest spend first", [x["label"] for x in bm] == ["m/a", "m/b", "m/c"] and bm[0]["total_cost"] == 4.0 and bm[0]["total_tokens"] == 540, bm)
	bp = api.get_breakdown("provider", "2026-11-04", "2026-11-05")
	check("by provider groups correctly", {x["label"]: x["total_cost"] for x in bp} == {"P1": 4.5, "P2": 2.0}, bp)
	check("the limit applies", len(api.get_breakdown("model", "2026-11-04", "2026-11-05", limit=2)) == 2)
	try:
		api.get_breakdown("virtual_key_name")
		check("an unknown dimension is refused", False)
	except frappe.ValidationError:
		check("an unknown dimension is refused", True)
	mt = api.get_model_table("2026-11-04", "2026-11-05")
	check("the model table lists every model with its totals", len(mt) == 3 and mt[0]["model"] == "m/a" and mt[0]["total_requests"] == 40 and mt[0]["prompt_tokens"] == 400 and mt[0]["providers"] == "P1", mt[0])
	fo = api.get_filter_options()
	check("filter options list providers and models", fo == {"providers": ["P1", "P2"], "models": ["m/a", "m/b", "m/c"]}, fo)

	base = datetime(2026, 11, 5, 8, 0, 0)
	for i in range(8):
		frappe.get_doc({"doctype": "OpenRouter Balance Snapshot", "taken_at": base + timedelta(minutes=30 * i), "total_purchased": 100, "total_used": 40 + i, "remaining": 60 - i}).insert(ignore_permissions=True)
	h = api.get_balance_history("2026-11-05", "2026-11-05")
	check("a short range returns every snapshot", len(h) == 8 and h[0]["remaining"] == 60 and h[-1]["remaining"] == 53, len(h))
	h = api.get_balance_history("2026-10-01", "2026-11-05")
	check("a long range is averaged per hour", len(h) == 4 and h[0]["taken_at"] == "2026-11-05 08:00:00" and h[0]["remaining"] == 59.5, h)
	s = api.get_summary("2026-11-04", "2026-11-05")
	check("summary shows the latest credits", s["credits"]["remaining"] == 53 and s["credits"]["total_purchased"] == 100, s["credits"])

	# ---- permission and isolation ----
	frappe.set_user("Guest")
	for fn in (api.get_summary, api.get_trend, api.get_breakdown, api.get_model_table, api.get_balance_history, api.get_filter_options):
		try:
			fn()
			check(f"{fn.__name__} is closed to a user without the roles", False)
		except frappe.PermissionError:
			check(f"{fn.__name__} is closed to a user without the roles", True)
	frappe.set_user("Administrator")
	opts = frappe.get_meta("LLM Usage Log").get_field("source").options
	check("Bifrost's table has no OpenRouter source and gets no OpenRouter rows", "OpenRouter" not in opts and frappe.db.count("LLM Usage Log", {"source": "OpenRouter"}) == 0)
	check("the Bifrost summary endpoint is untouched by OpenRouter data", "total_cost" in frappe.get_attr("alfaedge_pulse.llm_usage_monitor.api.get_usage_summary")("2026-11-04", "2026-11-05"))
	check("the Low Credit Balance alert type exists", "Low Credit Balance" in frappe.get_meta("Proxmox Alert Log").get_field("alert_type").options)
finally:
	frappe.get_cached_doc, frappe.db.commit, frappe.log_error = orig["get_cached_doc"], orig["commit"], orig["log_error"]
	sync.utcnow, sync.dispatch_alert, sync.dispatch_recovery = orig["utcnow"], orig["dispatch_alert"], orig["dispatch_recovery"]
	sync.has_open_alert, sync.resolve_alert = orig["has_open"], orig["resolve"]
	sync.enqueue_activity_sync, sync.enqueue_balance_check, sync._has_key = orig["enqueue_a"], orig["enqueue_b"], orig["has_key"]
	frappe.set_user("Administrator")
	frappe.db.rollback = real_rollback
	frappe.db.rollback()
	frappe.clear_document_cache("OpenRouter Settings", "OpenRouter Settings")
	frappe.clear_cache(doctype="OpenRouter Settings")

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {str(d)[:200]}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
