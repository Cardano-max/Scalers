"""Proactive engine package — the loops that act BETWEEN operator sessions.

This slice (CustomerAcq-tlv.2) contributes :mod:`proactive.followup_source` — the
inbound reply/outcome capture path that closes the persistent per-customer memory
loop (a real customer turn + a structured outcome memory per inbound signal).
The fr1.1 scheduler / detector spine is eng4's separate slice.
"""
