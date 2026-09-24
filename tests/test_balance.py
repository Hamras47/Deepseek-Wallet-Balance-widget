"""Tests for balance.py — the wallet maths, with no network anywhere.

The transport is injected (`fetch(key, getter=...)`), so every failure path is
testable: a rejected key, an empty account, a rate limit, a broken reply.

    .venv\\Scripts\\python.exe -m unittest discover -s tests -t .
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import time
import unittest

import balance


def payload(total="7.48", granted="0.00", topped="7.48", currency="USD", available=True):
    return json.dumps(
        {
            "is_available": available,
            "balance_infos": [
                {
                    "currency": currency,
                    "total_balance": total,
                    "granted_balance": granted,
                    "topped_up_balance": topped,
                }
            ],
        }
    )


def transport(status=200, body=""):
    def getter(_url, _key):
        return status, body

    return getter


def reading(total, at=0.0, available=True, currency="USD"):
    return balance.Reading(
        at=at,
        currency=currency,
        total=total,
        granted=0.0,
        topped_up=total,
        is_available=available,
    )


class KeyFile(unittest.TestCase):
    def test_missing_file_is_no_key(self):
        self.assertIsNone(balance.read_key(pathlib.Path(tempfile.gettempdir()) / "definitely-absent"))

    def test_blank_file_is_no_key(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "key.txt"
            path.write_text("   \n\n", encoding="utf-8")
            self.assertIsNone(balance.read_key(path))

    def test_key_is_trimmed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "key.txt"
            path.write_text("  sk-abcdefghijklmnop\n", encoding="utf-8")
            self.assertEqual(balance.read_key(path), "sk-abcdefghijklmnop")


class Fetch(unittest.TestCase):
    def test_reads_the_wallet(self):
        got = balance.fetch("sk-x", transport(200, payload()))
        self.assertEqual(got.total, 7.48)
        self.assertEqual(got.topped_up, 7.48)
        self.assertEqual(got.currency, "USD")
        self.assertTrue(got.is_available)

    def test_amounts_with_separators_parse(self):
        got = balance.fetch("sk-x", transport(200, payload(total="1,234.56")))
        self.assertEqual(got.total, 1234.56)

    def test_granted_two_decimal_precision_is_kept(self):
        got = balance.fetch("sk-x", transport(200, payload(granted="0.01")))
        self.assertEqual(got.granted, 0.01)

    def test_rejected_key(self):
        with self.assertRaises(balance.BalanceError) as caught:
            balance.fetch("sk-x", transport(401, "Authentication Fails"))
        self.assertEqual(caught.exception.kind, "auth")

    def test_no_credit(self):
        with self.assertRaises(balance.BalanceError) as caught:
            balance.fetch("sk-x", transport(402, "Insufficient Balance"))
        self.assertEqual(caught.exception.kind, "empty")

    def test_rate_limited_counts_as_try_later(self):
        with self.assertRaises(balance.BalanceError) as caught:
            balance.fetch("sk-x", transport(429, "slow down"))
        self.assertEqual(caught.exception.kind, "offline")

    def test_server_error(self):
        with self.assertRaises(balance.BalanceError) as caught:
            balance.fetch("sk-x", transport(503, "nope"))
        self.assertEqual(caught.exception.kind, "error")

    def test_broken_json(self):
        with self.assertRaises(balance.BalanceError) as caught:
            balance.fetch("sk-x", transport(200, "<html>not json</html>"))
        self.assertEqual(caught.exception.kind, "error")

    def test_no_balance_infos(self):
        body = json.dumps({"is_available": True, "balance_infos": []})
        with self.assertRaises(balance.BalanceError) as caught:
            balance.fetch("sk-x", transport(200, body))
        self.assertEqual(caught.exception.kind, "error")

    def test_transport_failure_is_offline(self):
        def exploding(_url, _key):
            raise TimeoutError("no route to host")

        with self.assertRaises(balance.BalanceError) as caught:
            balance.fetch("sk-x", exploding)
        self.assertEqual(caught.exception.kind, "offline")

    def test_missing_key_never_calls_out(self):
        called = []

        def getter(url, key):
            called.append(key)
            return 200, payload()

        with self.assertRaises(balance.BalanceError) as caught:
            balance.fetch("", getter)
        self.assertEqual(caught.exception.kind, "no_key")
        self.assertEqual(called, [])  # nothing was sent


class Spend(unittest.TestCase):
    def test_spending_shows_as_a_decrease(self):
        got = balance.with_spend(reading(7.48), reading(7.34))
        self.assertEqual(got.spent, 0.14)
        self.assertIsNone(got.topup)

    def test_top_up_is_not_negative_spend(self):
        got = balance.with_spend(reading(2.00), reading(7.48))
        self.assertIsNone(got.spent)
        self.assertEqual(got.topup, 5.48)

    def test_no_change_reads_as_zero_not_none(self):
        got = balance.with_spend(reading(7.48), reading(7.48))
        self.assertEqual(got.spent, 0.0)

    def test_first_reading_has_no_change(self):
        got = balance.with_spend(None, reading(7.48))
        self.assertIsNone(got.spent)
        self.assertIsNone(got.topup)

    def test_a_cent_is_the_smallest_step(self):
        got = balance.with_spend(reading(7.48), reading(7.47))
        self.assertEqual(got.spent, 0.01)


class Store(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.folder.name) / "history.json"

    def tearDown(self):
        self.folder.cleanup()

    def test_a_reading_round_trips(self):
        store = balance.Store(self.path)
        store.append(reading(7.48, at=time.time()))
        again = balance.Store(self.path)
        self.assertEqual(len(again.rows), 1)
        self.assertEqual(again.last().total, 7.48)

    def test_broken_history_file_is_ignored(self):
        self.path.write_text("not json at all", encoding="utf-8")
        self.assertEqual(balance.Store(self.path).rows, [])

    def test_rows_without_the_expected_fields_are_dropped(self):
        rows = [{"at": 1, "total": 5.0}, {"nope": True}]
        self.path.write_text(json.dumps(rows), encoding="utf-8")
        store = balance.Store(self.path)
        self.assertEqual(len(store.rows), 1)
        self.assertEqual(store.last().total, 5.0)

    def test_history_is_capped(self):
        store = balance.Store(self.path)
        for index in range(balance.HISTORY_LIMIT + 25):
            store.append(reading(7.48, at=float(index)))
        self.assertEqual(len(store.rows), balance.HISTORY_LIMIT)
        self.assertEqual(balance.Store(self.path).last().total, 7.48)

    def test_window_spend_is_start_minus_now(self):
        store = balance.Store(self.path)
        now = time.time()
        store.append(reading(10.00, at=now - 3600))
        store.append(reading(9.40, at=now))
        self.assertEqual(store.spent_within(1, now=now), 0.60)

    def test_window_spend_adds_top_ups_back(self):
        store = balance.Store(self.path)
        now = time.time()
        store.append(reading(2.00, at=now - 3600))
        store.append(
            balance.with_spend(reading(2.00, at=now - 1800), reading(7.00, at=now - 1800))
        )
        store.append(reading(6.50, at=now))
        # 2.00 -> 6.50 with a 5.00 top-up in between means 0.50 was actually spent.
        self.assertEqual(store.spent_within(1, now=now), 0.50)

    def test_window_spend_needs_two_readings(self):
        store = balance.Store(self.path)
        store.append(reading(7.48, at=time.time()))
        self.assertIsNone(store.spent_within(30))

    def test_last_reading_still_carries_its_change_after_a_restart(self):
        store = balance.Store(self.path)
        store.append(reading(7.48, at=1000.0))
        store.append(reading(7.34, at=2000.0))
        reloaded = balance.Store(self.path).last()
        self.assertEqual(reloaded.spent, 0.14)

    def test_last_reading_reports_a_single_row_as_unchanged(self):
        store = balance.Store(self.path)
        store.append(reading(7.48, at=1000.0))
        self.assertIsNone(balance.Store(self.path).last().spent)

    def test_a_top_up_while_closed_is_not_negative_spend(self):
        store = balance.Store(self.path)
        store.append(reading(2.00, at=1000.0))
        store.append(reading(7.00, at=2000.0))
        reloaded = balance.Store(self.path).last()
        self.assertIsNone(reloaded.spent)
        self.assertEqual(reloaded.topup, 5.00)


class Formatting(unittest.TestCase):
    def test_usd(self):
        self.assertEqual(balance.money(7.48), "$7.48")

    def test_cny(self):
        self.assertEqual(balance.money(7.5, "CNY"), "\u00a57.50")

    def test_unknown_currency_falls_back_to_its_code(self):
        self.assertEqual(balance.money(7.5, "EUR"), "EUR 7.50")

    def test_thousands_are_grouped(self):
        self.assertEqual(balance.money(1234.5), "$1,234.50")

    def test_negative_amounts_keep_their_sign(self):
        self.assertEqual(balance.money(-0.05), "-$0.05")

    def test_ages(self):
        self.assertEqual(balance.humanise(5), "just now")
        self.assertEqual(balance.humanise(60 * 10), "10 min ago")
        self.assertEqual(balance.humanise(3600 * 2), "2h ago")
        self.assertEqual(balance.humanise(86400 * 3), "3d ago")


if __name__ == "__main__":
    unittest.main()
