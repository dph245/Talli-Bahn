"""Timetable adapters returning shared, normalized models."""
from .db_ris import DBRISProvider
from .base import TransitProvider
from .demo import DemoProvider
from .gtfs import GTFSProvider
from .gtfs_static import GTFSStaticProvider

__all__ = ["TransitProvider", "DemoProvider", "GTFSProvider", "GTFSStaticProvider", "DBRISProvider"]
