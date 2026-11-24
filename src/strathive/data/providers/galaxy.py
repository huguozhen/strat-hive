"""Bounded requests into the existing isolated Galaxy SDK environment."""
from strathive.sources.sdk import sdk_python
from strathive.sources.sync import request


class Galaxy:
    name = "galaxy"

    def __init__(self, connection):
        self.connection = connection

    def fetch(self, action, symbols=(), session_date=None):
        return request("galaxy", action, self.connection, python=sdk_python(), timeout=20,
                       symbols=list(symbols), session_date=session_date)

    def quotes(self, symbols):
        return self.fetch("quotes", symbols)

    def close_prices(self, symbols, session_date):
        return self.fetch("close", symbols, session_date)

    def calendar(self, market):
        return self.fetch("calendar", [market])
