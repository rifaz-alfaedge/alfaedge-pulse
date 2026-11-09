# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Minimal HTTP client for the two OpenRouter endpoints the usage dashboard reads.

Built on plain ``requests`` like ``bifrost_client.base``. Both endpoints need an
OpenRouter **Management** API key sent as a Bearer token:

- ``GET /credits``: total credits purchased and total used (``data.total_credits``,
  ``data.total_usage``). Remaining is purchased minus used.
- ``GET /activity``: daily usage grouped by endpoint for the last 30 *completed*
  UTC days (today is never included); ``?date=YYYY-MM-DD`` narrows it to one day.

The key is only ever put in the ``Authorization`` header. It is never part of a URL,
a log line or an error message: errors carry the status code or the exception class
name and nothing from the request.
"""

from __future__ import annotations

import time

import requests


class OpenRouterAPIError(Exception):
	"""Any failure talking to OpenRouter. ``retryable`` is True for failures that
	are worth trying again shortly (network trouble, 429, 5xx); a refused key
	(401, 403) or a malformed answer is not."""

	def __init__(self, message: str, retryable: bool = False):
		super().__init__(message)
		self.retryable = retryable


class OpenRouterClient:
	default_timeout = 30
	#: Seconds to wait before the 2nd, 3rd and 4th attempt of a request.
	retry_delays = (5, 20, 60)

	def __init__(self, base_url: str, management_key: str):
		self.base_url = (base_url or "https://openrouter.ai/api/v1").rstrip("/")
		self.session = requests.Session()
		self.session.headers["Authorization"] = f"Bearer {management_key}"
		self.session.headers["Accept"] = "application/json"

	# -- one request --------------------------------------------------------

	def get(self, path: str, params: dict | None = None):
		"""GET ``path`` and return the decoded JSON body (no retry)."""
		try:
			response = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.default_timeout)
		except requests.RequestException as exc:
			raise OpenRouterAPIError(
				f"Could not reach OpenRouter ({type(exc).__name__}).", retryable=True
			) from None
		status = response.status_code
		if status == 401:
			raise OpenRouterAPIError("OpenRouter rejected the key (401). Check the Management API key.")
		if status == 403:
			raise OpenRouterAPIError(
				"OpenRouter refused this key (403). The credits and activity endpoints need a "
				"Management API key, not a normal inference key."
			)
		if status == 429:
			raise OpenRouterAPIError("OpenRouter is rate limiting requests (429).", retryable=True)
		if status >= 500:
			raise OpenRouterAPIError(f"OpenRouter had a server error ({status}).", retryable=True)
		if status >= 400:
			raise OpenRouterAPIError(f"OpenRouter answered {status} for {path}.")
		try:
			return response.json()
		except ValueError:
			raise OpenRouterAPIError(f"OpenRouter returned something that is not JSON for {path}.") from None

	def get_with_retry(self, path: str, params: dict | None = None, sleep=time.sleep):
		"""``get`` that retries retryable failures after 5, 20 and 60 seconds."""
		delays = iter(self.retry_delays)
		while True:
			try:
				return self.get(path, params)
			except OpenRouterAPIError as exc:
				delay = next(delays, None)
				if not exc.retryable or delay is None:
					raise
				sleep(delay)

	# -- the two endpoints --------------------------------------------------

	def get_credits(self, sleep=time.sleep) -> dict:
		"""``{"total_credits": float, "total_usage": float}`` (USD)."""
		payload = self.get_with_retry("/credits", sleep=sleep)
		data = payload.get("data", payload) if isinstance(payload, dict) else {}
		try:
			return {"total_credits": float(data["total_credits"]), "total_usage": float(data["total_usage"])}
		except (KeyError, TypeError, ValueError):
			raise OpenRouterAPIError("OpenRouter's credits answer did not have total_credits and total_usage.") from None

	def get_activity(self, date: str | None = None, sleep=time.sleep) -> list[dict]:
		"""The activity rows (one per day and endpoint) for the last 30 completed
		UTC days, or for one ``date``."""
		payload = self.get_with_retry("/activity", {"date": date} if date else None, sleep=sleep)
		rows = payload.get("data", payload) if isinstance(payload, dict) else payload
		if not isinstance(rows, list):
			raise OpenRouterAPIError("OpenRouter's activity answer was not a list of rows.")
		return [r for r in rows if isinstance(r, dict)]
