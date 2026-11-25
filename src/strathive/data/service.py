"""Capability routing, bounded price cache and explicit degradation."""
from datetime import datetime, timedelta

from strathive import settings
from strathive.adapters.catalog import Catalog
from strathive.core.rules import SHANGHAI, LedgerError, require
from .cache import PriceCache
from .models import Price, utcnow
from .providers.galaxy import Galaxy
from .history.provider import History


def configuration(values):
    if "providers" in values:
        return values
    source = values.get("source")
    return {**values, "schema_version": 2,
            "providers": {source: {"connection": values.get("connection", {})}} if source else {},
            "selection": {"reference": [source] if source else [], "quote": [], "close": [], "calendar": []}}


class Data:
    def __init__(self, config=None, providers=None, cache=None, clock=utcnow, catalog=None):
        self.config = configuration(settings.settings() if config is None else config)
        self.clock = clock
        self.catalog = catalog or Catalog(settings.catalog_path())
        self.cache = cache or PriceCache(settings.market_path())
        self.providers = providers if providers is not None else {
            name: Galaxy(value.get("connection", {}))
            for name, value in self.config.get("providers", {}).items() if name == "galaxy"
        }
        if providers is None and "history" in self.config and self.config["history"].get("enabled", True):
            self.providers["cnequity"] = History(self.config["history"])

    def status(self):
        return {"selection": self.config.get("selection", {}),
                "providers": list(self.providers), "cache": str(self.cache.path), "history": History().status()}

    def instruments(self, query):
        from strathive.core.ledger import resolve
        return resolve(self.catalog.find, query)

    def _sources(self, capability):
        return [(name, self.providers[name]) for name in self.config.get("selection", {}).get(capability, [])
                if name in self.providers]

    def calendar(self, market):
        for name, provider in self._sources("calendar"):
            try:
                dates = provider.calendar(market)
                return sorted({str(value) for value in dates})
            except (LedgerError, ValueError, TypeError, AttributeError):
                continue
        raise LedgerError("source_unavailable", "交易日历不可用。")

    def boundary_date(self, effective_at):
        at = datetime.fromisoformat(effective_at).astimezone(SHANGHAI)
        dates = self.calendar("SH")
        day = at.date().isoformat()
        if day in dates:
            require(not ("09:15" <= at.strftime("%H:%M") < "15:00"),
                    "期初和持仓交接须在盘前或盘后生效。")
        before = day if at.hour >= 15 or day not in dates else (at.date() - timedelta(days=1)).isoformat()
        available = [value for value in dates if value <= before]
        require(bool(available) and day <= dates[-1], "交易日历未覆盖生效日期。", "source_unavailable")
        return available[-1]

    def _fetch(self, capability, symbols, session_date=None):
        found, errors = {}, {}
        for name, provider in self._sources(capability):
            pending = sorted(set(symbols) - found.keys())
            if not pending:
                break
            try:
                method = provider.quotes if capability == "quote" else provider.close_prices
                rows = method(pending) if capability == "quote" else method(pending, session_date)
                for row in rows:
                    try:
                        price = Price(**row)
                        if price.symbol not in pending or price.provider != name:
                            raise ValueError("unexpected source or symbol")
                        if price.kind != ("last" if capability == "quote" else "close"):
                            raise ValueError("wrong price kind")
                        if price.session_date > self.clock().astimezone(SHANGHAI).date().isoformat():
                            raise ValueError("future session")
                        if session_date and price.session_date != session_date:
                            raise ValueError("wrong session")
                        if name != "cnequity":
                            self.cache.put(price)
                        if capability == "quote" and price.as_of and (self.clock() - datetime.fromisoformat(price.as_of)).total_seconds() > 300:
                            continue
                        found[price.symbol] = price
                    except (ValueError, TypeError, KeyError):
                        continue
            except (LedgerError, ValueError, TypeError, AttributeError) as exc:
                errors[name] = exc.code if isinstance(exc, LedgerError) else "unsupported_capability" if isinstance(exc, AttributeError) else "invalid_market_data"
        return found, errors

    def close_prices(self, symbols, session_date):
        symbols = sorted(set(symbols))
        prices = {}
        for symbol in symbols:
            candidates = [p for p in self.cache.find(symbol, "close") if p.session_date == session_date]
            if candidates:
                prices[symbol] = max(candidates, key=lambda p: p.retrieved_at)
        fetched, errors = self._fetch("close", set(symbols) - prices.keys(), session_date)
        prices.update(fetched)
        return {"prices": {s: p.to_dict() for s, p in prices.items()}, "errors": errors,
                "missing": sorted(set(symbols) - prices.keys())}

    def quotes(self, symbols, refresh=False, offline=False):
        require(not (refresh and offline), "--refresh 与 --offline 不能同时使用。")
        symbols = sorted(set(symbols))
        now = self.clock()
        prices, errors = {}, {}
        for symbol in symbols:
            candidates = self.cache.find(symbol, "last")
            if candidates:
                p = max(candidates, key=lambda p: p.as_of or p.session_date)
                if not refresh and (now - datetime.fromisoformat(p.retrieved_at)).total_seconds() <= 60:
                    prices[symbol] = p
        if not offline:
            fetched, errors = self._fetch("quote", set(symbols) - prices.keys())
            prices.update(fetched)
        close_fallback = {}
        missing = {s for s in symbols if s not in prices and not self.cache.find(s, "last")}
        # Explicitly fetch the most recent completed close only if no snapshot is available.
        if missing and not offline:
            try:
                target = self.boundary_date(now.isoformat())
            except LedgerError:
                target = None
                try:
                    dates = self.calendar("SH")
                    target = max(d for d in dates if d < now.astimezone(SHANGHAI).date().isoformat())
                except (LedgerError, ValueError):
                    pass
            if target:
                close_fallback = self.close_prices(missing, target)["prices"]
        result = {}
        for symbol in symbols:
            p = prices.get(symbol)
            if p is None:
                candidates = self.cache.find(symbol, "last") or self.cache.find(symbol, "close")
                if candidates:
                    p = max(candidates, key=lambda p: p.as_of or p.session_date)
            if p is None and symbol in close_fallback:
                p = Price(**close_fallback[symbol])
            if p is None:
                continue
            age = (now - datetime.fromisoformat(p.as_of)).total_seconds() if p.as_of else None
            freshness = "unknown" if age is None else "current" if p.kind == "last" and 0 <= age <= 300 else "stale"
            result[symbol] = {**p.to_dict(), "freshness": freshness,
                              "degraded": freshness != "current", "errors": errors}
        return {"prices": result, "missing": sorted(set(symbols) - result.keys()), "errors": errors}
