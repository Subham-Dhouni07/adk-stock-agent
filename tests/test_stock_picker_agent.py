import csv
import gzip
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_picker_agent.agent import get_historical_candle_data, get_stock_suggestions, infer_smartlist_params, resolve_stock_instrument


class StockPickerAgentTests(unittest.TestCase):
    def test_infer_smartlist_params_from_query(self):
        self.assertEqual(
            infer_smartlist_params("suggest me some stocks with good momentum"),
            {"asset_type": "STOCK", "category": "PRICE_GAINERS"},
        )
        self.assertEqual(
            infer_smartlist_params("show me most active options"),
            {"asset_type": "STOCK", "category": "MOST_ACTIVE"},
        )
        self.assertEqual(
            infer_smartlist_params("give me index ideas"),
            {"asset_type": "INDEX", "category": "TOP_TRADED"},
        )

    def test_get_historical_candle_data_does_not_double_encode_pre_encoded_instrument_key(self):
        class DummyResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {"data": []}

        with patch.dict(os.environ, {"UPSTOCK_TOKEN": "test-token"}, clear=False):
            with patch("stock_picker_agent.agent.requests.get", return_value=DummyResponse()) as mock_get:
                result = get_historical_candle_data(
                    "NSE_EQ%7CINE848E01016",
                    to_date="2025-01-02",
                    from_date="2025-01-01",
                )

        self.assertEqual(result["status"], "success")
        requested_urls = [call.args[0] for call in mock_get.call_args_list]
        self.assertTrue(all("%257C" not in url for url in requested_urls))
        self.assertTrue(any("NSE_EQ%7CINE848E01016" in url for url in requested_urls))

    def test_get_stock_suggestions_uses_stocks_endpoint_for_stock_queries(self):
        class DummyResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {"data": {"smartlist": []}}

        with patch.dict(os.environ, {"UPSTOCK_TOKEN": "test-token"}, clear=False):
            with patch("stock_picker_agent.agent.requests.get", return_value=DummyResponse()) as mock_get:
                get_stock_suggestions("suggest some undervalued stocks")

        self.assertEqual(mock_get.call_count, 1)
        requested_url = mock_get.call_args.args[0]
        self.assertIn("smartlist/stocks?", requested_url)

    def test_get_stock_suggestions_uses_options_endpoint_for_option_queries(self):
        class DummyResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {"data": {"smartlist": [{"instrument_key": "BSE_EQ|INE123456789"}]}}

        with patch.dict(os.environ, {"UPSTOCK_TOKEN": "test-token"}, clear=False):
            with patch("stock_picker_agent.agent.requests.get", return_value=DummyResponse()) as mock_get:
                get_stock_suggestions("show me options with high open interest")

        self.assertEqual(mock_get.call_count, 1)
        requested_url = mock_get.call_args.args[0]
        self.assertIn("smartlist/options?", requested_url)

    def test_get_stock_suggestions_enriches_smartlist_with_company_names(self):
        class DummyResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {
                    "data": {
                        "smartlist": [
                            {"instrument_key": "BSE_EQ|INE123456789"},
                            {"instrument_key": "BSE_EQ|INE987654321", "symbol": "TCS"},
                        ]
                    }
                }

        with patch.dict(os.environ, {"UPSTOCK_TOKEN": "test-token"}, clear=False):
            with patch("stock_picker_agent.agent.requests.get", return_value=DummyResponse()):
                with patch("stock_picker_agent.agent._load_exchange_instruments", return_value=[
                    {
                        "instrument_key": "BSE_EQ|INE123456789",
                        "tradingsymbol": "RELIANCE",
                        "company_name": "Reliance Industries Limited",
                    },
                    {
                        "instrument_key": "BSE_EQ|INE987654321",
                        "tradingsymbol": "TCS",
                        "company_name": "Tata Consultancy Services",
                    },
                ]):
                    result = get_stock_suggestions("suggest some undervalued stocks")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["smartlist"][0]["tradingsymbol"], "RELIANCE")
        self.assertEqual(result["data"]["smartlist"][0]["company_name"], "Reliance Industries Limited")
        self.assertEqual(result["data"]["smartlist"][1]["tradingsymbol"], "TCS")
        self.assertEqual(result["data"]["smartlist"][1]["company_name"], "Tata Consultancy Services")

    def test_get_stock_suggestions_includes_current_price(self):
        class DummyResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {
                    "data": {
                        "smartlist": [
                            {
                                "instrument_key": "BSE_EQ|INE123456789",
                                "price": {
                                    "current": 100.5,
                                    "close_price": 110.0,
                                    "change_abs": -9.5,
                                    "change_pct": -8.64,
                                },
                            }
                        ]
                    }
                }

        with patch.dict(os.environ, {"UPSTOCK_TOKEN": "test-token"}, clear=False):
            with patch("stock_picker_agent.agent.requests.get", return_value=DummyResponse()):
                with patch("stock_picker_agent.agent._load_exchange_instruments", return_value=[
                    {
                        "instrument_key": "BSE_EQ|INE123456789",
                        "tradingsymbol": "RELIANCE",
                        "company_name": "Reliance Industries Limited",
                    }
                ]):
                    result = get_stock_suggestions("suggest some price losers")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["smartlist"][0]["current_price"], 100.5)
        self.assertEqual(result["data"]["smartlist"][0]["previous_price"], 110.0)
        self.assertEqual(result["data"]["smartlist"][0]["change_abs"], -9.5)
        self.assertEqual(result["data"]["smartlist"][0]["change_pct"], -8.64)

    def test_get_stock_suggestions_filters_out_derivative_results_for_stock_queries(self):
        class DummyResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {
                    "data": {
                        "smartlist": [
                            {
                                "instrument_key": "NSE_FO|106353",
                                "tradingsymbol": "RELIANCE26AUG1400CE",
                                "instrument_type": "CE",
                                "price": {"current": 100.0},
                            }
                        ]
                    }
                }

        with patch.dict(os.environ, {"UPSTOCK_TOKEN": "test-token"}, clear=False):
            with patch("stock_picker_agent.agent.requests.get", return_value=DummyResponse()):
                result = get_stock_suggestions("suggest some stocks under 100")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["smartlist"], [])

    def test_resolve_stock_instrument_from_gz_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / "NSE.json.gz"
            payload = [
                {
                    "instrument_key": "BSE_EQ|INE123456789",
                    "tradingsymbol": "RELIANCE",
                    "company_name": "Reliance Industries Limited",
                    "exchange": "BSE",
                    "segment": "BSE_EQ",
                    "instrument_type": "EQ",
                },
                {
                    "instrument_key": "BSE_EQ|INE987654321",
                    "tradingsymbol": "TCS",
                    "company_name": "Tata Consultancy Services",
                    "exchange": "BSE",
                    "segment": "BSE_EQ",
                    "instrument_type": "EQ",
                },
                {
                    "instrument_key": "NSE_FO|106353",
                    "tradingsymbol": "RELIANCE 1190 PE 29 SEP 26",
                    "company_name": "Reliance Industries Limited",
                    "exchange": "NSE",
                    "segment": "NSE_FO",
                    "instrument_type": "PE",
                },
            ]
            with gzip.open(archive_path, "wt", encoding="utf-8") as handle:
                json.dump(payload, handle)

            result = resolve_stock_instrument(
                "reliance industries",
                exchange_file=str(archive_path),
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["data"]["instrument_key"], "BSE_EQ|INE123456789")
            self.assertEqual(result["data"]["tradingsymbol"], "RELIANCE")

    def test_resolve_stock_instrument_from_csv_gz_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / "NSE.csv.gz"
            rows = [
                {
                    "instrument_key": "BSE_EQ|INE123456789",
                    "tradingsymbol": "RELIANCE",
                    "company_name": "Reliance Industries Limited",
                    "exchange": "BSE",
                    "segment": "BSE_EQ",
                    "instrument_type": "EQ",
                },
                {
                    "instrument_key": "NSE_FO|106353",
                    "tradingsymbol": "RELIANCE 1190 PE 29 SEP 26",
                    "company_name": "Reliance Industries Limited",
                    "exchange": "NSE",
                    "segment": "NSE_FO",
                    "instrument_type": "PE",
                },
            ]
            with gzip.open(archive_path, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            result = resolve_stock_instrument(
                "reliance industries",
                exchange_file=str(archive_path),
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["data"]["instrument_key"], "BSE_EQ|INE123456789")
            self.assertEqual(result["data"]["tradingsymbol"], "RELIANCE")

    def test_resolve_stock_instrument_prefers_exact_equity_match(self):
        with patch.dict(os.environ, {"UPSTOCK_TOKEN": "test-token"}, clear=False):
            with patch("stock_picker_agent.agent._load_exchange_instruments", return_value=[
                {
                    "instrument_key": "NSE_EQ|INE296A01032",
                    "tradingsymbol": "BAJFINANCE",
                    "company_name": "BAJAJ FINANCE LIMITED",
                    "exchange": "NSE",
                    "segment": "NSE_EQ",
                    "instrument_type": "EQ",
                },
                {
                    "instrument_key": "NSE_EQ|INE0LGX01024",
                    "tradingsymbol": "INA",
                    "company_name": "INSOLATION ENERGY LIMITED",
                    "exchange": "NSE",
                    "segment": "NSE_EQ",
                    "instrument_type": "EQ",
                },
            ]):
                result = resolve_stock_instrument("bajaj finance")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["instrument_key"], "NSE_EQ|INE296A01032")
        self.assertEqual(result["data"]["tradingsymbol"], "BAJFINANCE")


if __name__ == "__main__":
    unittest.main()
