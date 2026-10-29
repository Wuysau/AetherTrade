"""Shared wall/monotonic financial admission window; never execution authority."""
from datetime import datetime

from trading_system.contracts.canonical import utc_timestamp


class AdmissionWindow:
    def __init__(self, *, started_at: datetime, started_ns: int, wall_clock, monotonic_clock):
        self.started_at,self.started_ns=started_at,started_ns
        self.wall_clock,self.monotonic_clock=wall_clock,monotonic_clock
        self.last_at,self.last_ns=started_at,started_ns
        self.expires_at=None

    def poll(self, expires_at: datetime):
        # The caller may tighten expiry after risk/quote/authorization reads.
        # A later call cannot renew a previously established admission window.
        self.expires_at=expires_at if self.expires_at is None else min(self.expires_at,expires_at)
        duration=self.expires_at-self.started_at
        deadline_ns=self.started_ns+((duration.days*86400+duration.seconds)*1_000_000_000
                                     +duration.microseconds*1000)
        reading=self.wall_clock()
        current_at,current_ns=utc_timestamp(reading),self.monotonic_clock()
        if (type(self.started_ns) is not int or self.started_ns<0 or type(current_ns) is not int
                or not self.last_ns<=current_ns<deadline_ns
                or not self.last_at<=current_at<self.expires_at):
            return None
        self.last_at,self.last_ns=current_at,current_ns
        return reading,current_at,current_ns
