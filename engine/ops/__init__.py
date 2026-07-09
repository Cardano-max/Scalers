"""Ops-spine hygiene (CustomerAcq-fr1.3): review-queue archive-not-delete, a
TTL auto-archive sweep, and a dev/prod tenant write-guard. Keeps the
client-facing review queue honest before any demo without ever hard-deleting a
row (everything archives with a reason and stays queryable).
"""
