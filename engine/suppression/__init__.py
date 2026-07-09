"""The cross-channel STOP/suppression ledger (CustomerAcq-t90.3).

ONE durable source of opt-out truth for every channel. Opt-outs arrive via
channels the SMS provider never sees (email unsubscribes, web forms, verbal
requests at the front desk — FCC any-reasonable-means), so provider-side
opt-out handling alone is insufficient: everything that selects or sends to a
recipient reads THIS ledger. Relation to :mod:`outreach.suppression`: that
module is the in-memory email gate; :func:`outreach.suppression.SuppressionGate.from_ledger`
loads it from this ledger so the email path consumes the same truth.
"""
