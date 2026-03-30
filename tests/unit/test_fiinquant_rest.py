"""Unit tests for fiinquant_rest REST adapter."""
import pytest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import fiinquant_rest


class TestParseRestBar:
    def test_normal_bar(self):
        item = {
            "t": "2026-03-24T00:00:00",
            "o": 26000.0,
            "c": 25950.0,
            "h": 26300.0,
            "l": 25850.0,
            "v": 27812914.0,
            "val": 723988773000.0,
        }
        bar = fiinquant_rest._parse_rest_bar("HPG", item)
        assert bar is not None
        assert bar["ticker"] == "HPG"
        assert bar["date"] == date(2026, 3, 24)
        assert bar["open"] == 26000.0
        assert bar["close"] == 25950.0
        assert bar["high"] == 26300.0
        assert bar["low"] == 25850.0
        assert bar["volume"] == 27812914
        # REST doesn't provide flow fields
        assert bar["bu"] is None
        assert bar["sd"] is None
        assert bar["fb"] is None
        assert bar["fs"] is None
        assert bar["fn"] is None

    def test_missing_timestamp(self):
        bar = fiinquant_rest._parse_rest_bar("HPG", {"o": 100, "c": 100})
        assert bar is None

    def test_lowercase_ticker(self):
        item = {"t": "2026-03-24T00:00:00", "o": 100.0, "c": 100.0, "h": 100.0, "l": 100.0, "v": 1000.0}
        bar = fiinquant_rest._parse_rest_bar("hpg", item)
        assert bar["ticker"] == "HPG"


class TestParseRestIntradayBar:
    def test_normal_bar(self):
        bar = fiinquant_rest._parse_rest_intraday_bar(
            "hpg",
            {"t": "2026-03-27T09:15:00", "o": 100.0, "c": 101.0, "h": 102.0, "l": 99.0, "v": 5000.0},
            {"t": "2026-03-27T09:15:00", "b": 3000.0, "s": 2000.0},
            {"t": "2026-03-27T09:15:00", "fb": 500.0, "fs": 200.0, "fn": 300.0},
        )
        assert bar is not None
        assert bar["ticker"] == "HPG"
        assert bar["bar_time"] == datetime(2026, 3, 27, 2, 15, tzinfo=timezone.utc)
        assert bar["volume"] == 5000
        assert bar["bu"] == 3000
        assert bar["sd"] == 2000
        assert bar["fn"] == 300


class TestFetchOneTicker:
    def test_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "items": [
                {"t": "2026-03-24T00:00:00", "o": 100.0, "c": 100.0, "h": 100.0, "l": 100.0, "v": 5000.0},
                {"t": "2026-03-23T00:00:00", "o": 99.0, "c": 99.0, "h": 99.0, "l": 99.0, "v": 3000.0},
            ]
        }
        with patch("app.services.fiinquant_rest._requests.get", return_value=mock_response) as mock_get:
            status, bars = fiinquant_rest._fetch_one_ticker({}, "HPG", 25)

        assert status == "ok"
        assert len(bars) == 2
        assert bars[0]["ticker"] == "HPG"
        mock_get.assert_called_once()

    def test_failed_status(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "Failed",
            "errors": ["Sequence contains no matching element"],
        }
        with patch("app.services.fiinquant_rest._requests.get", return_value=mock_response):
            status, bars = fiinquant_rest._fetch_one_ticker({}, "BCH", 25)
        assert status == "empty"
        assert len(bars) == 0

    def test_http_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 403
        with patch("app.services.fiinquant_rest._requests.get", return_value=mock_response):
            status, bars = fiinquant_rest._fetch_one_ticker({}, "HPG", 25)
        assert status == "empty"
        assert len(bars) == 0

    def test_zero_volume_filtered(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "items": [
                {"t": "2026-03-24T00:00:00", "o": 100.0, "c": 100.0, "h": 100.0, "l": 100.0, "v": 0},
            ]
        }
        with patch("app.services.fiinquant_rest._requests.get", return_value=mock_response):
            status, bars = fiinquant_rest._fetch_one_ticker({}, "HPG", 25)
        assert status == "empty"
        assert len(bars) == 0


class TestFetchOneIntradayTicker:
    def test_success_merges_chart_and_indicators(self):
        chart = MagicMock(status_code=200)
        chart.json.return_value = {
            "items": [{"t": "2026-03-27T09:15:00", "o": 100.0, "c": 101.0, "h": 102.0, "l": 99.0, "v": 5000.0}]
        }
        busd = MagicMock(status_code=200)
        busd.json.return_value = {
            "items": [{"t": "2026-03-27T09:15:00", "b": 3000.0, "s": 2000.0}]
        }
        foreign = MagicMock(status_code=200)
        foreign.json.return_value = {
            "items": [{"t": "2026-03-27T09:15:00", "fb": 500.0, "fs": 200.0, "fn": 300.0}]
        }
        with patch("app.services.fiinquant_rest._requests.get", side_effect=[chart, busd, foreign]):
            status, bars = fiinquant_rest._fetch_one_intraday_ticker({}, "HPG", date(2026, 3, 23), date(2026, 3, 27), 2000)
        assert status == "ok"
        assert len(bars) == 1
        assert bars[0]["bu"] == 3000
        assert bars[0]["fn"] == 300


class TestFetchDailyBarsBlocking:
    def test_concurrent_fetch_all_tickers(self):
        tickers = [f"T{i:03d}" for i in range(50)]

        def fake_get(url, params=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "items": [
                    {"t": "2026-03-24T00:00:00", "o": 100.0, "c": 100.0, "h": 100.0, "l": 100.0, "v": 1000.0}
                ]
            }
            return resp

        fake_fq = SimpleNamespace(
            FiinSession=lambda username, password: SimpleNamespace(
                login=lambda: SimpleNamespace(access_token="fake-jwt")
            )
        )

        with patch.dict("sys.modules", {"FiinQuantX": fake_fq}), \
             patch("app.services.fiinquant_rest._requests.get", side_effect=fake_get):
            bars = fiinquant_rest.fetch_daily_bars_blocking(tickers, days=25)

        assert len(bars) == 50
        assert all(b["ticker"].startswith("T") for b in bars)

    def test_returns_empty_when_fiin_not_installed(self):
        with patch.dict("sys.modules", {"FiinQuantX": None}):
            bars = fiinquant_rest.fetch_daily_bars_blocking(["HPG"], days=25)

        assert bars == []

    def test_reports_partial_coverage(self):
        tickers = ["HPG", "VND", "MBS"]

        def fake_get(url, params=None, headers=None, timeout=None):
            code = params["Code"]
            resp = MagicMock()
            resp.status_code = 200
            if code == "HPG":
                resp.json.return_value = {
                    "items": [{"t": "2026-03-24T00:00:00", "o": 100.0, "c": 100.0, "h": 100.0, "l": 100.0, "v": 1000.0}]
                }
            elif code == "VND":
                resp.json.return_value = {"status": "Failed", "errors": ["no data"]}
            else:
                raise OSError("timeout")
            return resp

        fake_fq = SimpleNamespace(
            FiinSession=lambda username, password: SimpleNamespace(
                login=lambda: SimpleNamespace(access_token="fake-jwt")
            )
        )

        with patch.dict("sys.modules", {"FiinQuantX": fake_fq}), \
             patch("app.services.fiinquant_rest._requests.get", side_effect=fake_get):
            result = fiinquant_rest.fetch_daily_bars_with_status_blocking(tickers, days=25)

        assert len(result.bars) == 1
        assert result.tickers_with_rows == ["HPG"]
        assert result.empty_tickers == ["VND"]
        assert result.failed_tickers == ["MBS"]


class TestFetchIntradayBarsBlocking:
    def test_reports_partial_coverage(self):
        tickers = ["HPG", "VND", "MBS"]

        def fake_fetch(headers, ticker, from_date, to_date, page_size):
            if ticker == "HPG":
                return "ok", [{
                    "ticker": "HPG",
                    "bar_time": datetime(2026, 3, 27, 2, 15, tzinfo=timezone.utc),
                    "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
                    "volume": 5000, "bu": 3000, "sd": 2000, "fb": 0, "fs": 0, "fn": 0,
                }]
            if ticker == "VND":
                return "empty", []
            return "failed", []

        fake_fq = SimpleNamespace(
            FiinSession=lambda username, password: SimpleNamespace(
                login=lambda: SimpleNamespace(access_token="fake-jwt")
            )
        )

        with patch.dict("sys.modules", {"FiinQuantX": fake_fq}), \
             patch("app.services.fiinquant_rest._fetch_one_intraday_ticker", side_effect=fake_fetch):
            result = fiinquant_rest.fetch_intraday_bars_with_status_blocking(
                tickers, date(2026, 3, 23), date(2026, 3, 27)
            )

        assert len(result.bars) == 1
        assert result.tickers_with_rows == ["HPG"]
        assert result.empty_tickers == ["VND"]
        assert result.failed_tickers == ["MBS"]
