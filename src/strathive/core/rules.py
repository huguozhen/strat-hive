"""Core 与适配器共用的纯记账规则。"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

SHANGHAI = timezone(timedelta(hours=8))
FEE_NAMES = ("commission", "stamp_duty", "transfer_fee")
COST_BASIS = "moving_average_v1"
MAX_INTEGER = 2**63 - 1


class LedgerError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, message, code="invalid_input"):
    if not condition:
        raise LedgerError(code, message)


def decimal(value, label, *, positive=False):
    try:
        require(isinstance(value, (str, int, Decimal)) and not isinstance(value, bool), f"{label} must be a decimal string or integer")
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LedgerError("invalid_input", f"{label} must be decimal") from exc
    require(number.is_finite() and 0 <= number <= MAX_INTEGER, f"{label} is out of range")
    require(not positive or number > 0, f"{label} must be positive")
    require(number.as_tuple().exponent >= -12, f"{label} supports at most 12 decimal places")
    return number


def quantity(value, *, positive=True):
    number = decimal(value, "quantity", positive=positive)
    require(number == int(number), "quantity must be a whole number")
    return int(number)


def to_cents(number):
    with localcontext() as ctx:
        ctx.prec = 50
        cents = int((number * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    require(abs(cents) <= MAX_INTEGER, "amount is out of range")
    return cents


def money(value, label, *, positive=False):
    number = decimal(value, label, positive=positive)
    cents = to_cents(number)
    require(number * 100 == cents, f"{label} supports at most two decimal places")
    return cents


def money_text(cents):
    return format(Decimal(cents).scaleb(-2), ".2f")


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        require("T" in value or " " in value, "timestamp must include the time")
        if result.tzinfo is None:
            result = result.replace(tzinfo=SHANGHAI)
        return result.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (TypeError, ValueError, OverflowError) as exc:
        raise LedgerError("invalid_input", "expected an ISO 8601 timestamp within range") from exc


def fees_for(kind, side, effective_at, gross, rate, overrides):
    """按成交日估算费用，再应用用户提供的实际费用。"""
    trade_date = datetime.fromisoformat(effective_at).astimezone(SHANGHAI).date().isoformat()
    require(set(overrides) <= set(FEE_NAMES), "unknown fee override")
    rates = {
        "commission": max(Decimal(5), gross * rate),
        "stamp_duty": gross * Decimal("0.0005") if kind == "stock" and side == "sell" else Decimal(0),
        "transfer_fee": gross * Decimal("0.00001") if kind == "stock" else Decimal(0),
    }
    historical = kind == "stock" and (
        trade_date < "2022-04-29" or (side == "sell" and trade_date < "2023-08-28")
    )
    if historical:
        required = {"transfer_fee"} if trade_date < "2022-04-29" else set()
        if side == "sell":
            required.add("stamp_duty")
        require(required <= overrides.keys(), "enter actual fees for dates outside the built-in schedule")
    calculated = {name: to_cents(amount) for name, amount in rates.items()}
    if historical:
        for name in required:
            calculated[name] = None
    actual = dict(calculated)
    actual.update({name: money(amount, name) for name, amount in overrides.items()})
    return calculated, actual


@dataclass
class Position:
    quantity: int = 0
    basis: Decimal = Decimal(0)


@dataclass
class Balance:
    cash: int = 0
    positions: dict[str, Position] = field(default_factory=dict)

    def change_cash(self, delta):
        self.cash += delta
        require(0 <= self.cash <= MAX_INTEGER, "insufficient or out-of-range cash", "ledger_conflict")

    def buy(self, symbol, count, basis):
        position = self.positions.setdefault(symbol, Position())
        position.quantity += count
        require(position.quantity <= MAX_INTEGER, "position quantity is out of range")
        position.basis += basis

    def sell(self, symbol, count):
        position = self.positions.get(symbol)
        require(position is not None and position.quantity >= count,
                f"insufficient position for {symbol}", "ledger_conflict")
        if position.quantity == count:
            del self.positions[symbol]
            return
        position.basis *= Decimal(position.quantity - count) / position.quantity
        position.quantity -= count

    def snapshot(self):
        return {
            "cash": money_text(self.cash),
            "cash_occupied": money_text(to_cents(sum((p.basis for p in self.positions.values()), Decimal(0)))),
            "positions": [
                {"symbol": symbol, "quantity": p.quantity,
                 "unit_cost": format((p.basis / p.quantity).quantize(
                     Decimal("0.00000001"), rounding=ROUND_HALF_UP), "f")}
                for symbol, p in sorted(self.positions.items())
            ],
            "cost_basis": COST_BASIS,
        }
