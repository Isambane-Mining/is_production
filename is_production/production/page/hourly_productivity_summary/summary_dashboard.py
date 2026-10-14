"""Deprecated import/API compatibility; calculations live in the standard Page module."""
from . import hourly_productivity_summary as source
from .hourly_productivity_summary import (
    CATEGORIES, SPECIFICATION, validate_range, planned_schedule, indicators,
    _excavator_rows, build_dashboard, get_dashboard,
)
# Preserve original module attributes for callers importing the former controller.
frappe = source.frappe
getdate = source.getdate
now_datetime = source.now_datetime


def __getattr__(name):
    """Keep former imported helpers/types available without duplicating implementation."""
    return getattr(source, name)
