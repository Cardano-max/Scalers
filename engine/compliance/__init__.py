"""Deterministic, fail-closed compliance gates that run BEFORE any human
approver or connector sees a draft. Pure code — no model call, no environment
read, no clock read: every input (including "now") is passed in explicitly, so
the same inputs always produce the same typed verdict.
"""
