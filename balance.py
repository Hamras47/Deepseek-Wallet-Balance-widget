"""DeepSeek wallet: current balance, and spend derived from balance readings.

WHY THERE IS NO "USAGE" HERE
    DeepSeek documents exactly one wallet endpoint -- GET /user/balance. There is no
    usage, cost or API-call endpoint (verified against the docs and the live service,
    2026-09-24). So:
      * balance  = reported by the API, exact.
      * spend    = what the wallet had, minus what it has now. Money in, money out.
      * tokens, call counts = NOT knowable from here. A call would have to pass
        through a local proxy that reads the response's `usage` block.

    Two consequences that shape the card:
      * spend is only as fresh as the last reading (this widget reads on click).
      * the amounts have two decimals, so a single reading pair resolves to $0.01.
        Period totals are always computed from the FIRST and LAST balance plus any
        top-ups in between, never by adding up per-reading deltas -- that would
        drift, because each delta is rounded to the cent.

Pure stdlib: one urllib call, no scheduler, no dependency on the peak/off-peak engine.
"""

from __future__ import annotations

import json
import pathlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace

API_URL = "https://api.deepseek.com/user/balance"
TIMEOUT = 20
HISTORY_LIMIT = 400  # readings kept on disk (~a few months of clicking)

SYMBOLS = {"USD": "$", "CNY": "\u00a5"}

DEFAULT_KEY_FILE = pathlib.Path(r"C:\Users\hamra\Desktop\47-lab\.secrets\deepseek-api-key.txt")


class BalanceError(Exception):
    """Something the user needs to know. `kind` drives the card's state."""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


# --------------------------------------------------------------------------- key


def read_key(path: pathlib.Path | str = DEFAULT_KEY_FILE) -> str | None:
    """The API key from the vault file, or None when it is missing or blank."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    key = text.strip()
    return key or None


# ------------------------------------------------------------------------ reading


@dataclass(frozen=True)
class Reading:
    """One balance snapshot."""

    at: float  # when we read it
    currency: str
    total: float
    granted: float
    topped_up: float
    is_available: bool
    spent: float | None = None  # since the previous reading; None = none, or a top-up
    topup: float | None = None  # set when the balance went UP since last time

    def as_row(self) -> dict:
        row = {
            "at": round(self.at),
            "currency": self.currency,
            "total": self.total,
            "granted": self.granted,
            "topped_up": self.topped_up,
            "is_available": self.is_available,
        }
        if self.topup is not None:
            row["topup"] = self.topup
        return row


def _http_get(url: str, key: str) -> tuple[int, str]:
    """Default transport. Returns (status, body). Split out so tests never touch the net."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def fetch(key: str, getter=_http_get) -> Reading:
    """Read the wallet. Raises BalanceError with a message fit for the card."""
    if not key:
        raise BalanceError("No API key", kind="no_key")

    try:
        status, body = getter(API_URL, key)
    except Exception as error:  # noqa: BLE001 - any transport failure is "offline" to the user
        raise BalanceError(f"could not reach DeepSeek ({type(error).__name__})", "offline") from error

    if status == 401:
        raise BalanceError("DeepSeek rejected the key", "auth")
    if status == 402:
        raise BalanceError("No credit left on the account", "empty")
    if status == 429:
        raise BalanceError("Rate limited - try again shortly", "offline")
    if status != 200:
        raise BalanceError(f"DeepSeek answered HTTP {status}", "error")

    try:
        data = json.loads(body)
    except ValueError as error:
        raise BalanceError("Unexpected reply from DeepSeek", "error") from error

    infos = data.get("balance_infos") or []
    if not infos:
        raise BalanceError("The account reports no balance", "error")
    info = infos[0]

    def amount(field: str) -> float:
        try:
            return float(str(info.get(field, "0")).replace(",", ""))
        except ValueError:
            return 0.0

    return Reading(
        at=time.time(),
        currency=str(info.get("currency") or "USD"),
        total=amount("total_balance"),
        granted=amount("granted_balance"),
        topped_up=amount("topped_up_balance"),
        is_available=bool(data.get("is_available")),
    )


# ----------------------------------------------------------------------- history


def with_spend(previous: Reading | None, current: Reading) -> Reading:
    """Attach the change since `previous`: money spent, or a top-up."""
    if previous is None:
        return current
    if current.total < previous.total:
        return replace(current, spent=round(previous.total - current.total, 2))
    if current.total > previous.total:
        return replace(current, topup=round(current.total - previous.total, 2))
    return replace(current, spent=0.0)


class Store:
    """Readings on disk, so spend survives a restart. Plain JSON, atomically replaced."""

    def __init__(self, path: pathlib.Path | str) -> None:
        self.path = pathlib.Path(path)
        self.rows: list[dict] = self._load()

    def _load(self) -> list[dict]:
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [row for row in rows if isinstance(row, dict) and "at" in row and "total" in row]

    def append(self, reading: Reading) -> None:
        self.rows.append(reading.as_row())
        del self.rows[:-HISTORY_LIMIT]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.rows, indent=1), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            pass  # a widget that cannot write history still shows the balance

    def last(self) -> Reading | None:
        """The most recent reading, carrying the change since the one before it.

        Rows on disk only hold the balances, so the change is recomputed here: a
        restart then still shows "spent $0.02" instead of claiming it is the first
        reading when the history clearly says otherwise.
        """
        if not self.rows:
            return None
        row = self.rows[-1]
        reading = Reading(
            at=float(row.get("at", 0)),
            currency=str(row.get("currency", "USD")),
            total=float(row.get("total", 0)),
            granted=float(row.get("granted", 0)),
            topped_up=float(row.get("topped_up", 0)),
            is_available=bool(row.get("is_available", True)),
        )
        if row.get("topup"):
            return replace(reading, topup=float(row["topup"]))
        if len(self.rows) < 2:
            return reading
        before = float(self.rows[-2].get("total", 0))
        if reading.total < before:
            return replace(reading, spent=round(before - reading.total, 2))
        if reading.total == before:
            return replace(reading, spent=0.0)
        # The wallet grew with no recorded top-up, i.e. one was made while the
        # widget was closed. That is money in, never negative spend.
        return replace(reading, topup=round(reading.total - before, 2))

    def spent_within(self, days: float, now: float | None = None) -> float | None:
        """Spend over a window, from endpoint balances: start - now + top-ups between."""
        if not self.rows:
            return None
        now = time.time() if now is None else now
        cutoff = now - days * 86400
        window = [row for row in self.rows if float(row.get("at", 0)) >= cutoff]
        if len(window) < 2:
            return None
        first = float(window[0].get("total", 0))
        last = float(window[-1].get("total", 0))
        topups = sum(float(row.get("topup", 0) or 0) for row in window[1:])
        return round(max(0.0, first + topups - last), 2)


# --------------------------------------------------------------------- formatting


def money(amount: float, currency: str = "USD") -> str:
    symbol = SYMBOLS.get(currency.upper())
    sign = "-" if amount < 0 else ""
    body = f"{abs(amount):,.2f}"
    return f"{sign}{symbol}{body}" if symbol else f"{sign}{currency.upper()} {body}"


def humanise(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 45:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def clock(at: float) -> str:
    return time.strftime("%H:%M", time.localtime(at))
