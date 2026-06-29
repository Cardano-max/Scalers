"""Review-queue actions: the store/publish path behind the operator console.

The engine's decision path writes a PENDING ``actions`` row when an action routes
to REVIEW (autonomy HOLD); the console renders these, the operator approves, and
:func:`actions.publish.approve_and_publish` sends via the real connector and flips
the row to ``sent`` / ``failed``. The jury card + confidence come from the linked
``autonomy_decisions`` / ``autonomy_jury`` rows (joined by ``decision_id``).
"""
