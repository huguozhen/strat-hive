"""Small, provider-independent market data values."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class Price:
    symbol: str
    price: str
    kind: str
    provider: str
    as_of: str | None
    session_date: str
    retrieved_at: str
    currency: str = "CNY"
    adjustment: str = "none"

    def __post_init__(self):
        try:
            value = Decimal(self.price)
            valid = value.is_finite() and value > 0
        except (InvalidOperation, TypeError):
            valid = False
        if not valid or self.kind not in {"last", "close"} or self.adjustment != "none" or self.currency != "CNY":
            raise ValueError("invalid market price")
        datetime.fromisoformat(self.session_date)
        fetched = datetime.fromisoformat(self.retrieved_at)
        if fetched.tzinfo is None:
            raise ValueError("market timestamps require timezone")
        if self.as_of:
            at = datetime.fromisoformat(self.as_of)
            if at.tzinfo is None or at > fetched:
                raise ValueError("invalid market timestamp")

    def to_dict(self):
        return asdict(self)


def utcnow():
    return datetime.now(timezone.utc)
