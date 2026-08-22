"""Persistence.

Direct SQL through a thin repository layer, no ORM. The schema *is* the spec
here — two of its constraints are product rules that application code must not be
able to bypass — and an ORM hides exactly the thing worth reading.
"""
