import csv
import gzip
import json
import logging
import os
import re
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import requests
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

load_dotenv(override=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def normalize_text(text: str) -> str:
    text = str(text or "").strip().lower()
    text = text.replace("-", " ")
    text = re.sub(r"[\W_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _find_exchange_file(exchange_file: str | None = None) -> Path | None:
    logger.info("Looking for exchange archive using exchange_file=%s", exchange_file)
    if exchange_file:
        candidate = Path(exchange_file).expanduser()
        if candidate.exists():
            logger.info("Using explicitly provided exchange archive: %s", candidate)
            return candidate

    workspace_root = Path(__file__).resolve().parent.parent
    candidates = [
        workspace_root / "NSE.json.gz",
        workspace_root / "NSE.csv.gz",
        workspace_root / "data" / "NSE.json.gz",
        workspace_root / "data" / "NSE.csv.gz",
        Path.cwd() / "NSE.json.gz",
        Path.cwd() / "NSE.csv.gz",
        Path.cwd() / "data" / "NSE.json.gz",
        Path.cwd() / "data" / "NSE.csv.gz",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    for match in workspace_root.rglob("NSE.json.gz"):
        return match

    for match in workspace_root.rglob("NSE.csv.gz"):
        return match

    for fallback in workspace_root.rglob("*.json.gz"):
        if fallback.name.lower().startswith("nse"):
            logger.info("Found fallback exchange archive: %s", fallback)
            return fallback

    for fallback in workspace_root.rglob("*.csv.gz"):
        if fallback.name.lower().startswith("nse"):
            logger.info("Found fallback exchange archive: %s", fallback)
            return fallback

    logger.warning("No exchange archive was found in the workspace")
    return None


def _load_exchange_instruments(exchange_file: str | None = None) -> list[dict[str, Any]]:
    archive_path = _find_exchange_file(exchange_file)
    if archive_path is None:
        logger.warning("Exchange archive lookup returned no file")
        return []

    logger.info("Loading instruments from exchange archive: %s", archive_path)

    try:
        if archive_path.name.lower().endswith(".gz"):
            with gzip.open(archive_path, "rt", encoding="utf-8", newline="") as handle:
                raw_text = handle.read()
        else:
            with archive_path.open("r", encoding="utf-8", newline="") as handle:
                raw_text = handle.read()

        if archive_path.name.lower().endswith(".csv") or archive_path.name.lower().endswith(".csv.gz"):
            reader = csv.DictReader(raw_text.splitlines())
            payload = []
            for row in reader:
                normalized_row = {key: (value.strip() if isinstance(value, str) else value) for key, value in row.items() if key is not None}
                if not normalized_row.get("segment") and normalized_row.get("exchange"):
                    exchange = str(normalized_row.get("exchange") or "").upper()
                    normalized_row["segment"] = exchange if exchange in {"NSE_EQ", "BSE_EQ"} else f"{exchange}_EQ" if exchange in {"NSE", "BSE"} else exchange
                if not normalized_row.get("instrument_type") and normalized_row.get("segment"):
                    segment = str(normalized_row.get("segment") or "").upper()
                    normalized_row["instrument_type"] = "EQ" if "EQ" in segment else ""
                if not normalized_row.get("company_name"):
                    normalized_row["company_name"] = normalized_row.get("name") or ""
                payload.append(normalized_row)
        else:
            payload = json.loads(raw_text)
    except Exception as exc:
        logger.warning("Unable to read exchange archive %s: %s", archive_path, exc)
        return []

    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            payload = payload["data"]
        elif isinstance(payload.get("instruments"), list):
            payload = payload["instruments"]
        else:
            payload = [payload]

    if isinstance(payload, list):
        instruments = [item for item in payload if isinstance(item, dict)]
        logger.info("Loaded %d instruments from %s", len(instruments), archive_path)
        return instruments

    logger.warning("Exchange archive payload was not a list: %s", type(payload).__name__)
    return []


def _is_equity_instrument(instrument: dict[str, Any]) -> bool:
    segment = str(instrument.get("segment") or "").upper()
    instrument_type = str(instrument.get("instrument_type") or "").upper()
    exchange = str(instrument.get("exchange") or "").upper()
    trading_symbol = str(instrument.get("tradingsymbol") or instrument.get("trading_symbol") or instrument.get("symbol") or "").upper()

    if segment in {"NSE_EQ", "BSE_EQ"}:
        return True

    if exchange in {"NSE", "BSE"} and instrument_type in {"EQ", ""}:
        return True

    if "EQ" in trading_symbol or "EQUITY" in trading_symbol:
        return True

    return False


def _score_match(query_norm: str, instrument: dict[str, Any]) -> float:
    logger.debug("Scoring instrument %s against query %s", instrument.get("tradingsymbol") or instrument.get("symbol"), query_norm)

    # Prefer exact matches on trading symbol or company name first.
    tradingsymbol = normalize_text(instrument.get("tradingsymbol") or instrument.get("trading_symbol") or instrument.get("symbol") or "")
    company_name = normalize_text(instrument.get("company_name") or instrument.get("name") or "")
    name_field = normalize_text(instrument.get("name") or "")

    query_norm_no_space = query_norm.replace(" ", "")
    if tradingsymbol:
        if query_norm == tradingsymbol or query_norm_no_space == tradingsymbol.replace(" ", ""):
            return 1.0
        if query_norm in tradingsymbol or tradingsymbol in query_norm:
            return 0.98

    if company_name:
        if query_norm == company_name or query_norm_no_space == company_name.replace(" ", ""):
            return 1.0
        if query_norm in company_name or company_name in query_norm:
            return 0.95

    # Fall back to other fields (instrument_key, symbol, name) with lower weight.
    fields = [instrument.get("instrument_key"), instrument.get("symbol"), instrument.get("name")]
    normalized_fields = [normalize_text(value) for value in fields if value]
    if not normalized_fields:
        return 0.0

    for field in normalized_fields:
        field_no_space = field.replace(" ", "")
        if query_norm == field or query_norm_no_space == field_no_space:
            return 0.96
        if query_norm in field or field in query_norm or query_norm_no_space in field_no_space or field_no_space in query_norm_no_space:
            return 0.8

    query_tokens = set(query_norm.split())
    for field in normalized_fields:
        field_tokens = set(field.split())
        overlap = len(query_tokens.intersection(field_tokens))
        if overlap:
            score = overlap / max(len(query_tokens), len(field_tokens))
            if score >= 0.5:
                return 0.6 + score * 0.15

    best_score = 0.0
    for field in normalized_fields:
        similarity = SequenceMatcher(None, query_norm, field).ratio()
        best_score = max(best_score, similarity)

    return best_score


def infer_smartlist_params(query: str) -> dict[str, str]:
    """Infer Upstox smartlist asset type and category from a natural language query."""
    text = (query or "").lower()

    if any(keyword in text for keyword in ["option", "options", "call", "put", "strike", "expiry", "premium"]):
        asset_type = "STOCK"
        if any(keyword in text for keyword in ["most active", "active", "high volume", "volume"]):
            category = "MOST_ACTIVE"
        elif any(keyword in text for keyword in ["oi gain", "oi gainer", "oi losers", "open interest"]):
            category = "OI_GAINERS"
        elif any(keyword in text for keyword in ["iv", "implied volatility"]):
            category = "IV_GAINERS"
        else:
            category = "TOP_TRADED"
        return {"asset_type": asset_type, "category": category}

    if any(keyword in text for keyword in ["index", "nifty", "bank nifty", "sensex"]):
        return {"asset_type": "INDEX", "category": "TOP_TRADED"}

    if any(keyword in text for keyword in ["momentum", "bullish", "breakout", "uptrend", "gainer", "gain", "strong"]):
        return {"asset_type": "STOCK", "category": "PRICE_GAINERS"}

    price_cap_match = re.search(r"\b(?:under|below|less than)\s*₹?\s*([0-9]+(?:\.[0-9]+)?)\b", text)
    if price_cap_match:
        try:
            price_val = float(price_cap_match.group(1))
            if price_val <= 5000:
                return {"asset_type": "STOCK", "category": "UNDER_5000"}
            if price_val <= 10000:
                return {"asset_type": "STOCK", "category": "UNDER_10000"}
        except ValueError:
            pass

    if any(keyword in text for keyword in ["loser", "weak", "downtrend", "falling", "decrease", "decreased", "decline", "declined", "drop", "fell", "slump", "lower"]):
        return {"asset_type": "STOCK", "category": "PRICE_LOSERS"}

    if any(keyword in text for keyword in ["under 5000", "below 5000", "cheap", "low price"]):
        return {"asset_type": "STOCK", "category": "UNDER_5000"}

    if any(keyword in text for keyword in ["under 10000", "below 10000", "very cheap"]):
        return {"asset_type": "STOCK", "category": "UNDER_10000"}

    if any(keyword in text for keyword in ["most active", "active", "high volume", "volume"]):
        return {"asset_type": "STOCK", "category": "MOST_ACTIVE"}

    if any(keyword in text for keyword in ["oi", "open interest"]):
        return {"asset_type": "STOCK", "category": "OI_GAINERS"}

    return {"asset_type": "STOCK", "category": "TOP_TRADED"}


def _choose_smartlist_endpoint(query: str, asset_type: str) -> str:
    if asset_type.upper() == "INDEX":
        return "indices"
    text = (query or "").lower()
    if any(keyword in text for keyword in ["option", "options", "call", "put", "strike", "expiry", "premium"]):
        return "options"
    return "stocks"


def get_stock_suggestions(
    query: str,
    asset_type: str | None = None,
    category: str | None = None,
    page_number: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    """Fetch smartlist suggestions from Upstox based on a user query."""
    logger.info("Stock suggestions requested for query=%s", query)
    logger.info("Parameters: asset_type=%s, category=%s, page_number=%d, page_size=%d", asset_type, category, page_number, page_size)
    if not query or not str(query).strip():
        return {"status": "error", "data": None, "message": "Please provide a suggestion query."}

    token = os.getenv("UPSTOCK_TOKEN") or os.getenv("UPSTOCK_API_TOKEN")
    if not token:
        return {"status": "error", "data": None, "message": "UPSTOCK_TOKEN is not set."}

    resolved_params = infer_smartlist_params(query)
    # Always use equities for suggestions unless explicitly changed later.
    asset_type = "STOCK"
    category = (category or resolved_params["category"]).upper()
    endpoint = _choose_smartlist_endpoint(query, asset_type)

    # Detect a max price filter in the user's query like 'under 100' or 'below 100'.
    max_price: float | None = None
    m = re.search(r"(?:under|below)\s*₹?\s*(\d+(?:\.\d+)?)", query.lower())
    if m:
        try:
            max_price = float(m.group(1))
            logger.info("Detected max_price filter from query: %s", max_price)
        except Exception:
            max_price = None

    params = {
        "asset_type": asset_type,
        "category": category,
        "page_number": page_number,
        "page_size": page_size,
    }

    url = f"https://api.upstox.com/v2/market/smartlist/{endpoint}?{urlencode(params)}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    def _format_smartlist_items(smartlist: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Enrich Upstox smartlist items with local instrument metadata when available.
        filtered: list[dict[str, Any]] = []
        instrument_map: dict[str, dict[str, Any]] = {}

        def _enrich_item(item: dict[str, Any]) -> None:
            if "tradingsymbol" not in item and "symbol" in item:
                item["tradingsymbol"] = item["symbol"]
            if "symbol" not in item and "tradingsymbol" in item:
                item["symbol"] = item["tradingsymbol"]

            if ("company_name" not in item or not item.get("company_name")) and item.get("instrument_key"):
                instrument_key = item["instrument_key"]
                if instrument_key not in instrument_map:
                    for local_inst in _load_exchange_instruments():
                        key = local_inst.get("instrument_key")
                        if key:
                            instrument_map[key] = local_inst
                local = instrument_map.get(instrument_key)
                if local:
                    item.setdefault("tradingsymbol", local.get("tradingsymbol") or local.get("symbol"))
                    item.setdefault("symbol", local.get("symbol") or item.get("tradingsymbol"))
                    item.setdefault("company_name", local.get("company_name") or local.get("name") or item.get("tradingsymbol"))

            if "company_name" not in item:
                item["company_name"] = item.get("tradingsymbol")
            if "symbol" not in item and "tradingsymbol" in item:
                item["symbol"] = item["tradingsymbol"]
            if "tradingsymbol" not in item and "symbol" in item:
                item["tradingsymbol"] = item["symbol"]

        def _is_equity_item(item: dict[str, Any]) -> bool:
            instrument_type = (item.get("instrument_type") or "").upper()
            trading_symbol = (item.get("tradingsymbol") or item.get("symbol") or "").upper()
            instrument_key = (item.get("instrument_key") or "").upper()
            instrument_segment = instrument_key.split("|", 1)[0] if "|" in instrument_key else ""

            if instrument_type and instrument_type != "EQ":
                return False
            if instrument_segment.endswith("_FO") or instrument_segment.endswith("_FUT") or instrument_segment.endswith("_OPT"):
                return False
            if re.search(r"(?<![A-Z])(?:CE|PE|FUT)$", trading_symbol):
                return False
            return True

        for item in smartlist:
            _enrich_item(item)
            price_data = item.get("price") or {}
            metric_data = item.get("metric") or {}
            item.setdefault("current_price", price_data.get("current") or metric_data.get("current"))
            item.setdefault("previous_price", price_data.get("close_price") or metric_data.get("previous"))
            item.setdefault("change_abs", price_data.get("change_abs") or metric_data.get("change_abs"))
            item.setdefault("change_pct", price_data.get("change_pct") or metric_data.get("change_pct"))

            if asset_type.upper() == "STOCK" and not _is_equity_item(item):
                continue

            filtered.append(item)

        return filtered

    def _parse_response(response):
        payload = response.json()
        data = payload.get("data") or {}
        smartlist = data.get("smartlist", []) if isinstance(data, dict) else []
        if not isinstance(smartlist, list):
            smartlist = []
        return payload, _format_smartlist_items(smartlist)

    def _build_url(category_value: str) -> str:
        params["category"] = category_value
        return f"https://api.upstox.com/v2/market/smartlist/{endpoint}?{urlencode(params)}"

    try:
        logger.info("Calling Upstox smartlist endpoint: %s", url)
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            logger.info("Response of successful smartlist call: %s", response.text)
            payload, smartlist = _parse_response(response)
            # If user asked for a price cap, filter the smartlist by current_price.
            if max_price is not None and isinstance(smartlist, list):
                filtered_by_price: list[dict[str, Any]] = []
                for item in smartlist:
                    cp = item.get("current_price")
                    try:
                        price_val = float(cp) if cp is not None else None
                    except Exception:
                        price_val = None
                    # Only include if we can parse price and it's <= max_price.
                    if price_val is not None and price_val <= max_price:
                        filtered_by_price.append(item)
                smartlist = filtered_by_price
            if not smartlist and category != "TOP_TRADED":
                if endpoint == "options":
                    return {
                        "status": "success",
                        "data": {
                            "asset_type": asset_type,
                            "category": category,
                            "endpoint": endpoint,
                            "smartlist": [],
                            "raw": payload,
                        },
                        "message": (
                            f"No equity suggestions were found for category {category}."
                        ),
                    }
                fallback_url = _build_url("TOP_TRADED")
                logger.warning("Smartlist returned empty for %s; retrying with TOP_TRADED", category)
                fallback_response = requests.get(fallback_url, headers=headers, timeout=30)
                if fallback_response.status_code == 200:
                    payload, smartlist = _parse_response(fallback_response)
                    if max_price is not None and isinstance(smartlist, list):
                        filtered_by_price = []
                        for item in smartlist:
                            cp = item.get("current_price")
                            try:
                                price_val = float(cp) if cp is not None else None
                            except Exception:
                                price_val = None
                            if price_val is not None and price_val <= max_price:
                                filtered_by_price.append(item)
                        smartlist = filtered_by_price
                    return {
                        "status": "success",
                        "data": {
                            "asset_type": asset_type,
                            "category": "TOP_TRADED",
                            "endpoint": endpoint,
                            "smartlist": smartlist,
                            "raw": payload,
                        },
                        "message": (
                            f"Requested category {category} returned no results, so returned TOP_TRADED suggestions instead."
                        ),
                    }
            return {
                "status": "success",
                "data": {
                    "asset_type": asset_type,
                    "category": category,
                    "endpoint": endpoint,
                    "smartlist": smartlist,
                    "raw": payload,
                },
                "message": f"Fetched {asset_type.lower()} smartlist suggestions for category {category} using {endpoint}.",
            }

        if response.status_code >= 400 and category != "TOP_TRADED" and endpoint == "stocks":
            logger.info(f"Response is : {response.text}")
            fallback_url = _build_url("TOP_TRADED")
            logger.warning("Smartlist category %s not supported on stocks endpoint; retrying with TOP_TRADED", category)
            fallback_response = requests.get(fallback_url, headers=headers, timeout=30)
            if fallback_response.status_code == 200:
                payload, smartlist = _parse_response(fallback_response)
                if max_price is not None and isinstance(smartlist, list):
                    filtered_by_price = []
                    for item in smartlist:
                        cp = item.get("current_price")
                        try:
                            price_val = float(cp) if cp is not None else None
                        except Exception:
                            price_val = None
                        if price_val is not None and price_val <= max_price:
                            filtered_by_price.append(item)
                    smartlist = filtered_by_price
                return {
                    "status": "success",
                    "data": {
                        "asset_type": asset_type,
                        "category": "TOP_TRADED",
                        "endpoint": endpoint,
                        "smartlist": smartlist,
                        "raw": payload,
                    },
                    "message": (
                        f"Requested category {category} is unavailable for stocks. Returned TOP_TRADED suggestions instead."
                    ),
                }

        return {
            "status": "error",
            "data": None,
            "message": f"Unable to fetch stock suggestions. Status code {response.status_code}: {response.text}",
        }
    except requests.RequestException as exc:
        return {"status": "error", "data": None, "message": f"Unable to fetch stock suggestions: {exc}"}


def resolve_stock_instrument(query: str, exchange_file: str | None = None) -> dict[str, Any]:
    """Resolve a user query to an instrument from the exchange archive."""
    logger.info("Resolving stock query: %s", query)
    if not query or not str(query).strip():
        logger.warning("Stock query was empty")
        return {
            "status": "error",
            "data": None,
            "message": "Please provide a stock name or symbol to look up.",
        }

    instruments = _load_exchange_instruments(exchange_file)
    if not instruments:
        logger.warning("No instruments available for resolution")
        return {
            "status": "error",
            "data": None,
            "message": "Unable to load NSE instruments from NSE.json.gz. Ensure the archive exists in the workspace or pass exchange_file explicitly.",
        }

    query_norm = normalize_text(query)
    query_norm_no_space = query_norm.replace(" ", "")
    query_tokens = set(query_norm.split())
    logger.info("Normalized query: %s", query_norm)
    matches: list[dict[str, Any]] = []
    for instrument in instruments:
        if not _is_equity_instrument(instrument):
            logger.debug("Skipping non-equity instrument: %s", instrument.get("instrument_key"))
            continue

        score = _score_match(query_norm, instrument)
        if score >= 0.55:
            tradingsymbol = instrument.get("tradingsymbol") or instrument.get("trading_symbol") or instrument.get("symbol") or ""
            company_name = instrument.get("company_name") or instrument.get("name") or ""
            name = instrument.get("name") or ""

            normalized_symbol = normalize_text(tradingsymbol)
            normalized_company = normalize_text(company_name)
            normalized_name = normalize_text(name)

            symbol_tokens = set(normalized_symbol.split())
            company_tokens = set(normalized_company.split())
            name_tokens = set(normalized_name.split())

            priority = 0
            if query_norm == normalized_symbol or query_norm_no_space == normalized_symbol.replace(" ", ""):
                priority += 10
            if query_norm == normalized_company or query_norm_no_space == normalized_company.replace(" ", ""):
                priority += 8
            if query_tokens and query_tokens.issubset(symbol_tokens):
                priority += 5
            if query_tokens and query_tokens.issubset(company_tokens):
                priority += 4
            if query_tokens and query_tokens.issubset(name_tokens):
                priority += 3

            matches.append(
                {
                    "instrument_key": instrument.get("instrument_key") or "",
                    "tradingsymbol": tradingsymbol,
                    "symbol": instrument.get("symbol") or tradingsymbol,
                    "company_name": company_name,
                    "score": round(score, 3),
                    "priority": priority,
                }
            )

    if not matches:
        logger.warning("No matching instrument found for query: %s", query)
        return {
            "status": "error",
            "data": None,
            "message": f"No matching stock found for '{query}'.",
        }

    # Sort primarily by priority (exact symbol/company matches), then by score.
    matches.sort(key=lambda item: (item["priority"], item["score"]), reverse=True)
    best_match = matches[0]
    logger.info("Resolved best match: %s (score=%s priority=%s)", best_match.get("tradingsymbol"), best_match.get("score"), best_match.get("priority"))
    return {
        "status": "success",
        "data": best_match,
        "message": f"Resolved '{query}' to {best_match['tradingsymbol']}.",
    }


def get_historical_candle_data(
    instrument_key: str,
    unit: str = "day",
    interval: str = "1day",
    to_date: str | None = None,
    from_date: str | None = None,
) -> dict[str, Any]:
    """Fetch historical candle data from the Upstox API for a resolved instrument key."""
    logger.info("Fetching historical candle data for instrument_key=%s unit=%s interval=%s", instrument_key, unit, interval)
    if not instrument_key:
        logger.warning("Historical candle request received no instrument key")
        return {
            "status": "error",
            "data": None,
            "message": "Instrument key is required to fetch historical candles.",
        }

    token = os.getenv("UPSTOCK_TOKEN") or os.getenv("UPSTOCK_API_TOKEN")
    if not token:
        logger.warning("Upstox token is missing")
        return {
            "status": "error",
            "data": None,
            "message": "UPSTOCK_TOKEN is not set. Configure the environment variable before calling the historical candle API.",
        }

    if not to_date:
        to_date = date.today().strftime("%Y-%m-%d")
    if not from_date:
        from_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")

    if interval in {"1day", "1D", "day"}:
        interval = "day"
        unit = "day"
    elif interval in {"1minute", "1m", "minute"}:
        interval = "1minute"
        unit = "minute"
    else:
        interval = "day"
        unit = "day"

    encoded_key = instrument_key
    if "%" not in instrument_key:
        encoded_key = quote(instrument_key, safe="")

    logger.info("Historical candle request period: %s -> %s", from_date, to_date)
    urls = [
        f"https://api.upstox.com/v2/historical-candle/{encoded_key}/{interval}/{to_date}/{from_date}",
        f"https://api.upstox.com/v3/historical-candle/{encoded_key}/{interval}/{to_date}/{from_date}",
    ]

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    last_error: str | None = None
    for url in urls:
        try:
            logger.info("Calling Upstox endpoint: %s", url)
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                payload = response.json()
                logger.info("Historical candle call succeeded for %s", instrument_key)
                return {
                    "status": "success",
                    "data": payload,
                    "message": f"Fetched historical candle data for {instrument_key}.",
                }

            last_error = f"Status code {response.status_code}: {response.text}"
            logger.warning("Historical candle call failed for %s: %s", url, last_error)
        except requests.RequestException as exc:
            last_error = str(exc)
            logger.warning("Historical candle request failed for %s: %s", url, exc)

    logger.error("Historical candle request failed for %s: %s", instrument_key, last_error)
    return {
        "status": "error",
        "data": None,
        "message": f"Unable to fetch historical candle data. Last error: {last_error}",
    }


def get_market_quote(instrument_key: str) -> dict[str, Any]:
    """Fetch a live market quote from the Upstox API for a resolved instrument key."""
    logger.info("Fetching live market quote for instrument_key=%s", instrument_key)
    if not instrument_key:
        logger.warning("Market quote request received no instrument key")
        return {
            "status": "error",
            "data": None,
            "message": "Instrument key is required to fetch live market quotes.",
        }

    token = os.getenv("UPSTOCK_TOKEN") or os.getenv("UPSTOCK_API_TOKEN")
    if not token:
        logger.warning("Upstox token is missing")
        return {
            "status": "error",
            "data": None,
            "message": "UPSTOCK_TOKEN is not set. Configure the environment variable before calling the market quote API.",
        }

    encoded_key = instrument_key
    if "%" not in instrument_key:
        encoded_key = quote(instrument_key, safe="")

    url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={encoded_key}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    try:
        logger.info("Calling Upstox market quote endpoint: %s", url)
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            payload = response.json()
            data = payload.get("data") or {}
            if not data:
                logger.warning("Market quote response contained no data for %s", instrument_key)
                return {
                    "status": "error",
                    "data": None,
                    "message": "Market quote response did not contain any instrument data.",
                }

            quoted_values = next(iter(data.values())) if len(data) == 1 else data
            logger.info("Market quote call succeeded for %s", instrument_key)
            return {
                "status": "success",
                "data": quoted_values,
                "message": f"Fetched live market quote for {instrument_key}.",
            }

        logger.warning("Market quote call failed for %s: %s", url, response.text)
        return {
            "status": "error",
            "data": None,
            "message": f"Unable to fetch market quote. Status code {response.status_code}: {response.text}",
        }
    except requests.RequestException as exc:
        logger.warning("Market quote request failed for %s: %s", instrument_key, exc)
        return {
            "status": "error",
            "data": None,
            "message": f"Unable to fetch market quote: {exc}",
        }


def get_stock_details_tool(
    query: str,
    exchange_file: str | None = None,
    unit: str = "day",
    interval: str = "1day",
    to_date: str | None = None,
    from_date: str | None = None,
) -> dict[str, Any]:
    """Resolve a stock and return historical candle data for the user's request."""
    logger.info("Stock details tool invoked for query=%s", query)
    resolved = resolve_stock_instrument(query, exchange_file=exchange_file)
    if resolved["status"] != "success":
        return resolved

    instrument = resolved["data"]
    logger.info("Resolved instrument for details lookup: %s", instrument.get("tradingsymbol"))
    candle_data = get_historical_candle_data(
        instrument_key=instrument["instrument_key"],
        unit=unit,
        interval=interval,
        to_date=to_date,
        from_date=from_date,
    )
    quote_data = get_market_quote(instrument["instrument_key"])

    if candle_data["status"] != "success" and quote_data["status"] != "success":
        logger.warning("Historical candle and market quote lookup both failed for %s", instrument.get("tradingsymbol"))
        return {
            "status": "error",
            "data": {
                "instrument": instrument,
                "error": candle_data["message"] if candle_data["status"] != "success" else quote_data["message"],
            },
            "message": "Unable to retrieve stock details.",
        }

    return {
        "status": "success",
        "data": {
            "instrument": instrument,
            "candles": candle_data["data"] if candle_data["status"] == "success" else None,
            "quote": quote_data["data"] if quote_data["status"] == "success" else None,
        },
        "message": f"Retrieved historical data and live quote for {instrument['tradingsymbol']}.",
    }


stock_picker_agent = Agent(
    name="stock_picker_agent",
    model="gemini-2.5-flash",
    description="An agent that can resolve a stock from NSE data and fetch historical candle information from Upstox.",
    instruction=(
        "# CRITICAL RULE — READ FIRST\n"
        "For any user request that asks for stock ideas, stock suggestions, stocks that are losing, stocks that declined, or stocks that decreased, do not answer using your own reasoning.\n"
        "Always call get_stock_suggestions() as your first action for suggestion requests.\n"
        "If the user asks for a specific percentage decrease, explain that exact percentage filtering is unsupported and return a price losers suggestion instead.\n"
        "1. For stock lookup requests, resolve the stock using the NSE.json.gz archive and use the stock name, symbol, or company name to find the instrument key.\n"
        "2. For historical data requests, fetch candle information from the Upstox API. If the user does not specify a date range, use a recent 30-day window by default.\n"
        "3. For stock suggestion requests, infer smartlist parameters then call get_stock_suggestions().\n"
        "4. For current stock price or live equity quote requests, resolve the instrument and call get_market_quote().\n"
        "- asset_type=INDEX for index-related requests such as Nifty or Sensex.\n"
        "- asset_type=STOCK for stock or equity requests.\n"
        "- category=TOP_TRADED for general ideas.\n"
        "- category=MOST_ACTIVE for most active or high volume requests.\n"
        "- category=PRICE_GAINERS for momentum or bullish requests.\n"
        "- category=PRICE_LOSERS for bearish, weak, decline, or decrease requests.\n"
        "- category=UNDER_5000 for low-price or under 5000 requests.\n"
        "- category=UNDER_10000 for under 10000 requests.\n"
        "- category=OI_GAINERS for open interest related requests.\n"
        "If the request is ambiguous, default to asset_type=STOCK and category=TOP_TRADED."
    ),
    tools=[
        FunctionTool(resolve_stock_instrument),
        FunctionTool(get_market_quote),
        FunctionTool(get_historical_candle_data),
        FunctionTool(get_stock_details_tool),
        FunctionTool(get_stock_suggestions),
    ],
)