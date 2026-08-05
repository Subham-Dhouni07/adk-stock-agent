from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from dotenv import load_dotenv  
import requests
import os
import re
import logging
from difflib import SequenceMatcher

# custom functions
from ipo_agent.gmp_lambda import get_gmp_data_from_ipo_watch_tool
# from gmp_lambda import get_gmp_data_from_ipo_watch_tool

load_dotenv(override=True)


def normalize_text(text: str) -> str:
    text = str(text or '').strip().lower()
    text = text.replace('-', ' ')
    text = re.sub(r"[\W_]+", ' ', text)
    text = re.sub(r"\b(?:ipo|limited|ltd|private|company)\b", '', text)
    return re.sub(r"\s+", ' ', text).strip()

def get_item_name(item: dict) -> str:
    for key in ('name', 'company_name', 'ipo_name', 'issue_name', 'company', 'listing_name', 'issueName'):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def get_item_symbol(item: dict) -> str:
    for key in ('symbol', 'ticker', 'issue_symbol', 'isin'):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''

def similarity_score(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def get_ipo_matches(ipo_items: list[dict], query_norm: str, query_first_token: str) -> list[dict]:
    matches: list[dict] = []

    for item in ipo_items:
        if not isinstance(item, dict):
            logging.debug("Skipping non-dict IPO item: %r", item)
            continue
        item_id = str(item.get('id', '')).strip()
        item_name = get_item_name(item)
        item_symbol = get_item_symbol(item)

        logging.debug("Evaluating IPO item: id=%s name=%s symbol=%s", item_id, item_name, item_symbol)
        if not item_id and not item_name and not item_symbol:
            logging.debug("Skipping item because id, name, and symbol are all empty")
            continue

        item_id_norm = normalize_text(item_id)
        item_name_norm = normalize_text(item_name)
        item_symbol_norm = normalize_text(item_symbol)

        item_slugs = generate_ipo_watch_slugs(item_name, item_id)
        logging.debug("Generated IPO Watch slug candidates: %s", item_slugs)

        score = 0.0
        if query_norm == item_id_norm or query_norm == item_symbol_norm:
            score = 1.0
        elif query_norm == item_name_norm:
            score = 0.95
        elif is_token_subset(query_norm, item_name_norm):
            score = 0.9
        elif query_norm in item_name_norm or item_name_norm in query_norm:
            score = 0.8
        elif query_norm in item_id_norm or item_id_norm in query_norm:
            score = 0.85
        elif is_token_subset(query_norm, item_id_norm):
            score = 0.92
        elif any(query_norm == normalize_text(slug) for slug in item_slugs):
            score = 0.92
        elif is_token_subset(query_norm, item_name_norm) or is_token_subset(query_norm, item_id_norm):
            score = 0.9
        else:
            query_tokens = set(query_norm.split())
            name_tokens = set(item_name_norm.split())
            id_tokens = set(item_id_norm.split())
            overlap = len(query_tokens.intersection(name_tokens | id_tokens))
            if overlap >= 2:
                score = 0.75
            else:
                score = max(
                    similarity_score(query_norm, item_name_norm),
                    similarity_score(query_norm, item_symbol_norm),
                    similarity_score(query_norm, item_id_norm),
                    max((similarity_score(query_norm, normalize_text(slug)) for slug in item_slugs), default=0.0),
                )

        logging.debug(
            "Normalized item values: item_id_norm=%s item_name_norm=%s item_symbol_norm=%s score=%s",
            item_id_norm,
            item_name_norm,
            item_symbol_norm,
            round(score, 3),
        )

        if score >= 0.55:
            matches.append({
                'id': item_id,
                'name': item_name,
                'symbol': item_symbol,
                'score': round(score, 3),
                'ipo_watch_slugs': item_slugs,
            })
            logging.info(
                "Candidate match: id=%s name=%s symbol=%s score=%s slugs=%s",
                item_id,
                item_name,
                item_symbol,
                round(score, 3),
                item_slugs,
            )
        else:
            logging.debug(
                "Rejected candidate: id=%s name=%s symbol=%s score=%s slugs=%s",
                item_id,
                item_name,
                item_symbol,
                round(score, 3),
                item_slugs,
            )

    if not matches and query_first_token:
        logging.info(f"No matches found; trying first-token fallback: {query_first_token}")
        for item in ipo_items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get('id', '')).strip()
            item_name = get_item_name(item)
            item_symbol = get_item_symbol(item)
            item_name_norm = normalize_text(item_name)
            item_symbol_norm = normalize_text(item_symbol)
            item_id_norm = normalize_text(item_id)
            logging.info(f"Item name is '{item_name}', symbol is '{item_symbol}', id is '{item_id}'")
            if (
                query_first_token in item_name_norm.split()
                or query_first_token in item_symbol_norm.split()
                or query_first_token in item_id_norm.split()
            ):
                matches.append({
                    'id': item_id,
                    'name': item_name,
                    'symbol': item_symbol,
                    'score': 0.5,
                    'fallback': 'first_token'
                })
                logging.info("First-token fallback match: id=%s name=%s symbol=%s", item_id, item_name, item_symbol)

    return matches


def is_token_subset(query: str, candidate: str) -> bool:
    query_tokens = [token for token in query.split() if token]
    candidate_tokens = [token for token in candidate.split() if token]
    if not query_tokens or not candidate_tokens:
        return False
    return set(query_tokens).issubset(set(candidate_tokens))


def generate_ipo_watch_slugs(item_name: str, item_id: str | None = None) -> list[str]:
    normalized_name = normalize_text(item_name)
    tokens = normalized_name.split()
    slugs = []
    for length in range(2, min(len(tokens), 5) + 1):
        slug = '-'.join(tokens[:length])
        if not slug.endswith('ipo'):
            slug += '-ipo'
        slugs.append(slug)

    if item_id:
        # Also include a simplified slug based on item_id when it has common suffixes.
        id_slug = normalize_text(item_id).replace(' ', '-')
        id_slug = id_slug.replace('-limited', '').replace('-enterprises', '').replace('-company', '').replace('-private', '')
        if id_slug and not id_slug.endswith('-ipo'):
            id_slug += '-ipo'
        slugs.append(id_slug)

    return [slug for slug in dict.fromkeys(slugs) if slug]


BASE_URL = "https://api.upstox.com/v2/ipos"
MAX_UPSTOX_RECORDS = 10
token = os.getenv("UPSTOCK_TOKEN")

headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }

def get_all_ipo_tool(status : str = 'open', issue_type : str = 'regular', count : int = 10) -> dict:

    ''' 
    Return the list of all IPOs from the Upstock API. 
    
    Args:
        status (str): The status of the IPOs to fetch. Can be 'open', 'closed', 'listed', or 'upcoming'. Default is 'open'.
        issue_type (str): The type of IPOs to fetch. Can be 'regular' or 'sme. Default is 'regular'.
        count (int): The number of IPOs to fetch. Default is 10.

    Returns:
        dict: A dictionary containing the status of the request, the data fetched, and a message
    '''

    count = max(1, min(count, MAX_UPSTOX_RECORDS))
    if count != MAX_UPSTOX_RECORDS:
        logging.debug(f"Adjusted requested count to Upstock limit: {count}")

    logging.info("Fetching IPO data from Upstock API...")

    params = {
        'status': status,
        'issue_type': issue_type,
        'page_number': 1,
        'records': count
    }

    # get all IPOs
    response = requests.get(BASE_URL, headers=headers, params=params)
    if response.status_code != 200:
        return {
            'status': 'error',
            'data': None,
            'message': f"Failed to fetch IPO data. Status code: {response.status_code}, Response: {response.text}"
        }

    logging.info("Successfully fetched IPO data from Upstock API.")
    api_response = response.json()
    ipo_data = api_response.get('data', api_response) if isinstance(api_response, dict) else api_response

    logging.info(f"Fetched IPO data: {api_response}")
    logging.info(f"Upstock returned {len(ipo_data) if isinstance(ipo_data, (list, tuple)) else 1} IPO records.")
    return {
        'status': 'success',
        'data': ipo_data,
        'message': f"Fetched {len(ipo_data) if isinstance(ipo_data, (list, tuple)) else 1} IPOs successfully."
    }

def get_ipo_id_by_name_tool(ipo_name: str, status: str = 'open', issue_type: str = 'regular', count: int = MAX_UPSTOX_RECORDS) -> dict:
    ''' 
    Find an IPO by name, symbol, or ID and return its IPO ID.
    
    Args:
        ipo_name (str): The IPO name, symbol, or ID to search for.
        status (str): The IPO status to search within. Can be 'open', 'closed', 'listed', or 'upcoming'. Default is 'open'.
        issue_type (str): The IPO issue type to search within. Can be 'regular' or 'sme'. Default is 'regular'.
        count (int): The number of IPO records to fetch from get_all_ipo_tool. Default is 10.

    Returns:
        dict: A dictionary containing the status of the request, matching IPO record(s), and a message
    '''

    logging.info(f"Searching IPOs for name/symbol/ID: {ipo_name} with a status of '{status}' and issue type '{issue_type}' (count={count})...")

    result = get_all_ipo_tool(status=status, issue_type=issue_type, count=count)
    if result['status'] != 'success':
        logging.error(f"Failed to fetch IPO list: {result.get('message')}")
        return result

    data = result.get('data', [])
    if isinstance(data, dict) and 'data' in data:
        ipo_items = data['data']
    else:
        ipo_items = data or []

    query_norm = normalize_text(ipo_name)
    query_tokens = query_norm.split()
    query_first_token = query_tokens[0] if query_tokens else ''
    logging.info(f"Normalized query: '{query_norm}'")
    logging.info(f"Query first token: '{query_first_token}'")
    logging.info(f"IPO list count: {len(ipo_items)}")
    logging.debug("IPO items returned: %s", ipo_items)

    matches = get_ipo_matches(ipo_items, query_norm, query_first_token)

    if not matches:
        logging.info("No matching IPO found in the original status set; retrying alternate statuses.")
        alternate_statuses = ['open', 'closed', 'listed', 'upcoming']
        for alt_status in alternate_statuses:
            if alt_status == status.lower():
                continue
            logging.info(f"Retrying IPO lookup with alternate status='{alt_status}'.")
            alt_result = get_all_ipo_tool(status=alt_status, issue_type=issue_type, count=count)
            if alt_result['status'] != 'success':
                logging.warning(f"Failed to fetch IPO list for status '{alt_status}': {alt_result.get('message')}")
                continue
            alt_data = alt_result.get('data', [])
            if isinstance(alt_data, dict) and 'data' in alt_data:
                alt_items = alt_data['data']
            else:
                alt_items = alt_data or []
            logging.info(f"Alternate status '{alt_status}' returned {len(alt_items)} IPO items.")
            if not alt_items:
                continue
            matches = get_ipo_matches(alt_items, query_norm, query_first_token)
            if matches:
                break

    matches.sort(key=lambda item: item['score'], reverse=True)
    logging.info(f"Total matches found: {len(matches)}")
    if matches:
        logging.info(f"Top match: {matches[0]}")

    if not matches:
        return {
            'status': 'error',
            'data': None,
            'message': f"No IPO found matching '{ipo_name}'."
        }

    if len(matches) == 1 or matches[0]['score'] > 0.9:
        best_match = matches[0]
        return {
            'status': 'success',
            'data': {
                'id': best_match['id'],
                'name': best_match['name'],
                'symbol': best_match['symbol'],
            },
            'message': f"Found IPO '{best_match['name']}' with id '{best_match['id']}'."
        }

    logging.info(f"Multiple IPOs found matching '{ipo_name}': {matches}")
    return {
        'status': 'success',
        'data': [
            {
                'id': match['id'],
                'name': match['name'],
                'symbol': match['symbol'],
                'score': match['score'],
            }
            for match in matches
        ],
        'message': f"Found {len(matches)} matching IPOs for '{ipo_name}'. Please choose the correct one."
    }

def get_specific_ipo_detail_tool(ipo_id: str) -> dict:
    ''' 
    Return the details of a specific IPO from the Upstock API. 
    
    Args:
        ipo_id (str): The ID of the IPO to fetch.
    
    Returns:
        dict: A dictionary containing the status of the request, the data fetched, and a message
    '''

    logging.info(f"Fetching details for IPO ID: {ipo_id} from Upstock API...")

    url = f"{BASE_URL}/{ipo_id}"

    # get details of specific IPO
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        return {
            'status': 'error',
            'data': None,
            'message': f"Failed to fetch IPO data. Status code: {response.status_code}, Response: {response.text}"
        }

    logging.info("Successfully fetched IPO data from Upstock API.")
    ipo_data = response.json()

    logging.info(f"Fetched IPO data: {ipo_data}")
    
    return {
        'status': 'success',
        'data': ipo_data,
        'message': f"Fetched {len(ipo_data)} IPOs successfully."
    }

def get_gmp_detail_tool(ipo_id: str) -> dict:
    ''' 
    Return the GMP details of a specific IPO from the Upstock API. 
    
    Args:
        ipo_id (str): The ID of the IPO to fetch.
    
    Returns:
        dict: A dictionary containing the status of the request, the data fetched, and a message
    '''

    logging.info(f"Calling lambda function to fetch GMP data for IPO ID: {ipo_id}...")
    slug_candidates = generate_ipo_watch_slugs(item_name=None, item_id=ipo_id)
    logging.info(f"IPO Watch slug candidates for {ipo_id}: {slug_candidates}")

    for slug in slug_candidates:
        gmp_data = get_gmp_data_from_ipo_watch_tool(slug)
        if gmp_data.get('status') == 'success':
            logging.info(f"Fetched GMP data successfully using slug: {slug}")
            return gmp_data
        logging.warning(f"Failed GMP fetch for slug {slug}: {gmp_data.get('message')}")

    logging.info(f"Trying original IPO ID as fallback for GMP fetch: {ipo_id}")
    gmp_data = get_gmp_data_from_ipo_watch_tool(ipo_id)
    logging.info(f"Fetched GMP data: {gmp_data}")
    return gmp_data


def get_ipo_overview_tool(ipo_name: str, market_query: str | None = None, status: str = 'open', issue_type: str = 'regular', count: int = MAX_UPSTOX_RECORDS, max_results: int = 5) -> dict:
    '''
    Aggregate IPO id lookup, IPO details, GMP history, and market news for a general IPO information query.

    Args:
        ipo_name (str): The IPO name, symbol, or company name to analyze.
        market_query (str | None): Optional broader market or sentiment keywords for news.
        status (str): IPO status filter for initial id lookup. Can be 'open', 'closed', 'listed', or 'upcoming'. Default is 'open'.
        issue_type (str): IPO issue type filter. Can be 'regular' or 'sme'. Default is 'regular'.
        count (int): Number of IPO records to fetch during lookup. Default is 10.
        max_results (int): Maximum number of news articles to return. Default is 5.

    Returns:
        dict: Aggregated IPO overview data including IPO details, GMP history, news, and messages.
    '''

    logging.info(f"Starting IPO advice aggregation for '{ipo_name}' with market_query={market_query}")

    ipo_id_result = get_ipo_id_by_name_tool(ipo_name=ipo_name, status=status, issue_type=issue_type, count=count)
    if ipo_id_result.get('status') != 'success':
        return {
            'status': 'error',
            'data': None,
            'message': ipo_id_result.get('message', 'Could not resolve IPO id.')
        }

    ipo_id_data = ipo_id_result.get('data')
    if isinstance(ipo_id_data, list):
        if not ipo_id_data:
            return {
                'status': 'error',
                'data': None,
                'message': f"No IPO found matching '{ipo_name}'."
            }
        ipo_id_data = sorted(ipo_id_data, key=lambda item: item.get('score', 0), reverse=True)[0]
    ipo_id = ipo_id_data.get('id') if isinstance(ipo_id_data, dict) else None
    ipo_resolved_name = ipo_id_data.get('name') if isinstance(ipo_id_data, dict) else ipo_name
    ipo_symbol = ipo_id_data.get('symbol') if isinstance(ipo_id_data, dict) else ''

    if not ipo_id:
        return {
            'status': 'error',
            'data': None,
            'message': f"Could not resolve an IPO id for '{ipo_name}'."
        }

    detail_result = get_specific_ipo_detail_tool(ipo_id=ipo_id)
    gmp_result = get_gmp_detail_tool(ipo_id=ipo_id)

    if market_query and market_query.strip():
        news_query = market_query.strip()
    else:
        company_name = ipo_resolved_name.strip()
        if company_name.lower().endswith(' ipo'):
            company_name = company_name[:-4].strip()
        news_query = (
            f"{company_name} performance profits future goals financial plan status "
            f"company update"
        )

    news_result = ipo_news_tool(ipo_name=ipo_resolved_name, query=news_query, max_results=max_results)

    return {
        'status': 'success',
        'data': {
            'ipo_id': ipo_id,
            'ipo_name': ipo_resolved_name,
            'ipo_symbol': ipo_symbol,
            'details': detail_result.get('data'),
            'gmp': gmp_result.get('data'),
            'news': news_result.get('data'),
            'news_query': news_query,
            'components': {
                'ipo_id_lookup': ipo_id_result,
                'ipo_details': detail_result,
                'gmp_history': gmp_result,
                'news': news_result,
            }
        },
        'message': f"Aggregated IPO advice data for '{ipo_resolved_name}'."
    }


def get_ipo_investment_overview_alias(ipo_name: str, market_query: str | None = None, status: str = 'open', issue_type: str = 'regular', count: int = MAX_UPSTOX_RECORDS, max_results: int = 5) -> dict:
    '''
    Alias for get_ipo_overview_tool, intended for investment-related queries where the user asks whether to invest.
    '''
    return get_ipo_overview_tool(
        ipo_name=ipo_name,
        market_query=market_query,
        status=status,
        issue_type=issue_type,
        count=count,
        max_results=max_results,
    )


def ipo_news_tool(ipo_name : str, query: str | None = None, max_results: int = 10) -> dict:
    ''' 
    Search GNews for news related to a specific IPO or market keywords.
    Args:
        ipo_name (str): The IPO name, symbol, or company name to search for.
        query (str | None): Optional search keywords or phrase for broader market coverage.
        max_results (int): Maximum number of articles to return. Default is 10.
    
    Returns:
        dict: A dictionary containing the status, news articles, and a message.
    '''

    logging.info(f"Fetching news for IPO: {ipo_name} from GNews API... query={query}")

    api_key = os.getenv("GNEWS_API")
    if not api_key:
        return {
            'status': 'error',
            'data': None,
            'message': 'GNEWS_API environment variable is not set.'
        }

    search_url = "https://gnews.io/api/v4/search"
    ipo_name_clean = ipo_name.strip()
    if ipo_name_clean.lower().endswith(' ipo'):
        ipo_name_clean = ipo_name_clean[:-4].strip()
    elif ipo_name_clean.lower().endswith('ipo'):
        ipo_name_clean = ipo_name_clean[:-3].strip()

    if query and query.strip():
        query_text = query.strip()
    else:
        query_text = (
            f'"{ipo_name_clean}" performance OR profits OR "future goals" OR "financial plan" OR status OR update'
        )

    params = {
        'q': query_text,
        'apikey': api_key,
        'lang': 'en',
        'max': min(max_results, 10),
        'sortby': 'publishedAt'
    }

    def perform_search(search_query: str) -> tuple[list[dict], dict | None]:
        params['q'] = search_query
        try:
            response = requests.get(search_url, params=params, timeout=15)
            response.raise_for_status()
            return response.json().get('articles', []), None
        except requests.exceptions.RequestException as e:
            logging.error(f"GNews API request failed for query '{search_query}': {e}")
            return [], {'status': 'error', 'message': f"Failed to fetch IPO news from GNews API: {e}"}

    articles_data, error = perform_search(query_text)
    articles = []
    for article in articles_data:
        articles.append({
            'title': article.get('title'),
            'description': article.get('description'),
            'content': article.get('content'),
            'url': article.get('url'),
            'image': article.get('image'),
            'published_at': article.get('publishedAt'),
            'source': article.get('source', {}).get('name') if isinstance(article.get('source'), dict) else article.get('source')
        })

    if not articles and query_text != f'"{ipo_name_clean}"':
        logging.info(f"No articles found for the expanded query; retrying with company name only: '{ipo_name_clean}'")
        articles_data, error = perform_search(f'"{ipo_name_clean}"')
        articles = [
            {
                'title': article.get('title'),
                'description': article.get('description'),
                'content': article.get('content'),
                'url': article.get('url'),
                'image': article.get('image'),
                'published_at': article.get('publishedAt'),
                'source': article.get('source', {}).get('name') if isinstance(article.get('source'), dict) else article.get('source')
            }
            for article in articles_data
        ]
        query_text = f'"{ipo_name_clean}"'

    if error and not articles:
        return {
            'status': 'error',
            'data': None,
            'message': error['message']
        }

    if not articles:
        return {
            'status': 'error',
            'data': None,
            'message': f"No news found for IPO '{ipo_name}' via GNews."
        }

    return {
        'status': 'success',
        'data': {
            'ipo_name': ipo_name,
            'query': query_text,
            'articles': articles,
            'total_articles': len(articles)
        },
        'message': f"Fetched {len(articles)} news article(s) for IPO '{ipo_name}'."
    }


get_ipo_info_tool = FunctionTool(func = get_all_ipo_tool)
get_ipo_id_by_name_function_tool = FunctionTool(func = get_ipo_id_by_name_tool)
get_specific_ipo_tool = FunctionTool(func = get_specific_ipo_detail_tool)
get_gmp_data_tool = FunctionTool(func = get_gmp_detail_tool)
get_ipo_news_tool = FunctionTool(func = ipo_news_tool)
ipo_overview_tool = FunctionTool(func = get_ipo_overview_tool)
get_ipo_investment_overview_tool = FunctionTool(func = get_ipo_investment_overview_alias)

ipo_agent = Agent(
    name="ipo_agent_tool",
    model='gemini-2.5-flash',
    description="An agent that can provide information about IPOs.",
    instruction=(
        "# CRITICAL RULE — READ FIRST\n"
        "Whenever the user's message expresses investment intent — asking whether to invest, buy, apply, "
        "subscribe, or for a recommendation/opinion on an IPO — you MUST call get_ipo_investment_overview_tool "
        "as your first and only tool call. This overrides every other tool-selection rule in this prompt.\n\n"
        "Trigger phrases include (not exhaustive — match the INTENT, not exact wording):\n"
        "- 'should I invest in X'\n"
        "- 'should I buy X IPO'\n"
        "- 'should I apply for X'\n"
        "- 'is X a good IPO'\n"
        "- 'would you recommend X'\n"
        "- 'is it worth investing in X'\n"
        "- 'what do you think about X IPO'\n"
        "- 'is X worth subscribing to'\n\n"
        "You are NEVER allowed to call get_ipo_news_tool, get_gmp_data_tool, get_specific_ipo_tool, or any other "
        "single tool alone in response to an investment-intent question. Calling get_ipo_news_tool by itself for "
        "such a question is a failure — it does not contain GMP or IPO detail data and produces an incomplete, "
        "wrong answer. Only get_ipo_investment_overview_tool is acceptable for these questions.\n\n"
        "Do NOT ask a clarifying question before calling it, and do NOT refuse. Call "
        "get_ipo_investment_overview_tool immediately, then analyze its output and respond.\n\n"
        "Contrast — get_ipo_news_tool alone is ONLY correct when the user asks purely for news/headlines with "
        "NO investment framing, e.g. 'give me the news on X IPO', 'any recent headlines about X', "
        "'what's the media coverage on X'. If there is any doubt whether the question is about investing, "
        "treat it as investment intent and use get_ipo_investment_overview_tool.\n\n"

        "# Role\n"
        "You are an IPO agent that fetches IPO information from the Upstox API and GMP (Grey Market Premium) "
        "history from IPO Watch.\n\n"

        "# Tools & When to Use Them\n"
        "- get_ipo_investment_overview_tool: MANDATORY, single call, for any investment-intent question (see rule "
        "above). Aggregates IPO id lookup, IPO details, GMP history, and news in one call.\n"
        "- get_ipo_info_tool: Use when the user wants a list of IPOs by status or issue type (not investment "
        "advice). Returns IPO records, each with an 'id' field.\n"
        "- get_ipo_id_by_name_function_tool: Use when the user provides an IPO name/symbol and you need to "
        "resolve the IPO id for a non-investment request. Accepts optional status, issue_type, and count.\n"
        "- get_specific_ipo_tool: Use when you already have an IPO id and need full IPO details, for a "
        "non-investment request.\n"
        "- get_gmp_data_tool: Use when the user asks only for GMP history for a specific IPO (no investment "
        "framing). Requires an IPO id.\n"
        "- get_ipo_news_tool: Use ONLY when the user asks purely for recent IPO news or media coverage with no "
        "investment framing. Accepts optional query and max_results (1-10).\n"
        "- get_ipo_overview_tool: Use when the user wants a general consolidated view of an IPO (details + GMP + "
        "news) without asking whether to invest.\n\n"

        "# Resolving IPO IDs (non-investment flows only)\n"
        "get_gmp_data_tool requires an IPO id. Obtain it from get_ipo_info_tool results (each item includes 'id'), "
        "from get_ipo_id_by_name_function_tool, or directly from the user.\n"
        "If the user asks for GMP data without providing an IPO id, first call get_ipo_id_by_name_function_tool or "
        "get_ipo_info_tool to identify the IPO, then use its 'id' with get_gmp_data_tool.\n\n"

        "# Parameter Rules\n"
        "- status: one of 'upcoming', 'open', 'closed', or 'listed'.\n"
        "- issue_type: 'regular' (mainboard) or 'sme'.\n"
        "- count: integer 1-10 when the user asks for a limited number of IPOs.\n"
        "- max_results: integer 1-10 when using get_ipo_news_tool.\n\n"

        "# General (Non-Investment) Tool Sequence\n"
        "For a general 'tell me everything about this IPO' request (no investment framing):\n"
        "1. get_ipo_id_by_name_function_tool to resolve the IPO id from name or symbol\n"
        "2. get_specific_ipo_tool to fetch IPO details\n"
        "3. get_gmp_data_tool to fetch GMP history\n"
        "4. get_ipo_news_tool with relevant market or IPO keywords for news and sentiment\n\n"

        "# Response Guidelines\n"
        "- Always answer with factual IPO information, not personal investment advice.\n"
        "- For any response derived from get_ipo_investment_overview_tool, end with the exact phrase: "
        "'This is information only and not investment advice.'\n"
        "- After collecting tool outputs, analyze them together and provide a detailed, human-readable summary.\n"
        "- Prefer bullet-point lists for summaries; avoid tables unless the user explicitly requests them.\n"
        "- Always include a clear conclusion that directly answers the user's question."
    ),
    tools=[
        get_ipo_investment_overview_tool,
        get_ipo_info_tool,
        get_ipo_id_by_name_function_tool,
        get_specific_ipo_tool,
        get_gmp_data_tool,
        get_ipo_news_tool,
        get_ipo_overview_tool,
    ],
)