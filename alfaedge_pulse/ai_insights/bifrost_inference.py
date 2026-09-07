# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Outbound LLM calls for the weekly AI reports — the one piece that
didn't exist anywhere in this app before (see llm_usage_monitor/, which is
a read-only accounting layer over Bifrost's Management API `/api/logs`,
never a caller). This talks to Bifrost's separate OpenAI-compatible
inference surface instead, at the same `Bifrost Settings.base_url` but
with a "virtual key" Bearer token rather than the Management API's Basic
Auth admin credentials.

Routing report-generation calls through this same Bifrost instance (rather
than calling a provider directly) means they show up in Bifrost's own
`/api/logs` and get picked up by the existing bifrost_sync job into `LLM
Usage Log` on its next cycle — cost/token tracking for these reports comes
for free from infrastructure that already exists, no new accounting code
needed here.
"""

from __future__ import annotations

import requests


class BifrostInferenceError(Exception):
	"""Raised for any failure generating a completion. Callers (the report
	orchestration functions) catch this and record it on the Weekly AI
	Report doc's `error` field rather than letting a scheduled job crash."""


def generate_completion(base_url: str, virtual_key: str, model: str, prompt: str, max_tokens: int) -> str:
	"""One-shot, non-streaming chat completion — a report is generated
	fully server-side with no user waiting on a response, so there's no
	reason to add streaming complexity here.
	"""
	url = f"{base_url.rstrip('/')}/v1/chat/completions"
	try:
		response = requests.post(
			url,
			headers={"Authorization": f"Bearer {virtual_key}"},
			json={
				"model": model,
				"messages": [{"role": "user", "content": prompt}],
				"max_tokens": max_tokens,
			},
			timeout=120,
		)
		response.raise_for_status()
		payload = response.json()
	except requests.HTTPError as e:
		# Bifrost's error responses carry a genuinely useful body (e.g.
		# "could not auto resolve a provider for the request" or "Provider
		# 'openai' is not allowed for this virtual key") — `raise_for_status`
		# alone discards it, leaving only an opaque "400 Client Error" with
		# no way to tell a misconfigured virtual key from a bad model name
		# without reproducing the call by hand. Surface it verbatim instead.
		detail = e.response.text[:500] if e.response is not None else str(e)
		raise BifrostInferenceError(f"{url} failed: {e} — {detail}") from e
	except requests.RequestException as e:
		raise BifrostInferenceError(f"{url} failed: {e}") from e
	except ValueError as e:
		raise BifrostInferenceError(f"{url} returned non-JSON response") from e

	try:
		return payload["choices"][0]["message"]["content"]
	except (KeyError, IndexError, TypeError) as e:
		raise BifrostInferenceError(f"{url} returned an unexpected response shape: {payload}") from e
