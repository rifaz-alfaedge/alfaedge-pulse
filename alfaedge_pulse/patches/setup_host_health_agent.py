# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Historic patch (v3.0.0): created the Host Health agent user and role and
seeded the default expected services. The agent is gone as of v6.0.0 (see
``remove_push_agent``), so only the idempotent service seeding is kept, which
keeps this safe to run on a fresh site."""

from alfaedge_pulse.install import _seed_default_expected_services


def execute():
	_seed_default_expected_services()
