from html.parser import HTMLParser
from html import unescape
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import logging
import time


def _import_selenium():
    try:
        from selenium import webdriver
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.chrome.options import Options
        return webdriver, TimeoutException, By, WebDriverWait, EC, Options
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "Selenium is required for this function but is not installed. "
            "Install it with `pip install selenium`."
        ) from e

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _click_gmp_tab(driver, wait, By, EC) -> bool:
    """Click the GMP tab on the IPO Watch page if present."""
    try:
        gmp_tab_xpath = (
            "//a[contains(@class, 'ipo-tab') and contains(translate(normalize-space(text()), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'gmp')]"
            " | //a[contains(@href, '-ipo-gmp-grey-market-premium-')]"
        )
        gmp_tab = wait.until(EC.element_to_be_clickable((By.XPATH, gmp_tab_xpath)))
        gmp_tab.click()
        logger.info("Clicked GMP tab")
        time.sleep(2)
        return True
    except Exception as e:
        logger.warning(f"GMP tab was not clickable or not found: {e}")
        return False


class _HTMLTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self._table_depth = 0
        self._current_table = None
        self._current_row = None
        self._current_cell = None
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self._table_depth += 1
            if self._table_depth == 1:
                self._current_table = []
        elif tag == 'tr' and self._table_depth == 1:
            self._current_row = []
        elif tag in ('td', 'th') and self._current_row is not None and self._table_depth == 1:
            self._in_cell = True
            self._current_cell = ''

    def handle_data(self, data):
        if self._in_cell and self._current_cell is not None:
            self._current_cell += data

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self._in_cell:
            cell_value = unescape(self._current_cell).strip()
            self._current_row.append(cell_value)
            self._in_cell = False
            self._current_cell = None
        elif tag == 'tr' and self._current_row is not None and self._table_depth == 1:
            self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == 'table' and self._table_depth == 1:
            self.tables.append(self._current_table)
            self._current_table = None
            self._table_depth = 0
        elif tag == 'table':
            self._table_depth = max(0, self._table_depth - 1)


def _fetch_html(url: str) -> str:
    request = Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    })
    with urlopen(request, timeout=20) as response:
        return response.read().decode('utf-8', errors='replace')


def _parse_html_tables(html_text: str) -> list[tuple[list[str], list[list[str]]]]:
    parser = _HTMLTableParser()
    parser.feed(html_text)
    return parser.tables


def _parse_html_table(table, By) -> dict:
    """Parse a Selenium table element into headers and rows."""
    headers = []
    rows_data = []

    # Prefer thead headers if present
    thead = table.find_elements(By.TAG_NAME, "thead")
    if thead:
        header_rows = thead[0].find_elements(By.TAG_NAME, "tr")
        if header_rows:
            header_cells = header_rows[-1].find_elements(By.XPATH, "./th|./td")
            headers = [cell.text.strip() or f"Column_{idx+1}" for idx, cell in enumerate(header_cells)]

    # If no thead, derive headers from first row
    if not headers:
        all_rows = table.find_elements(By.TAG_NAME, "tr")
        if all_rows:
            first_row_cells = all_rows[0].find_elements(By.XPATH, "./th|./td")
            if first_row_cells:
                headers = [cell.text.strip() or f"Column_{idx+1}" for idx, cell in enumerate(first_row_cells)]
                all_rows = all_rows[1:]
            else:
                all_rows = all_rows
        else:
            all_rows = []
    else:
        # Use tbody rows if headers came from thead
        tbody = table.find_elements(By.TAG_NAME, "tbody")
        all_rows = tbody[0].find_elements(By.TAG_NAME, "tr") if tbody else table.find_elements(By.TAG_NAME, "tr")

    if not headers:
        headers = [f"Column_{idx+1}" for idx in range(1, 10)]

    for row in all_rows:
        cells = row.find_elements(By.XPATH, "./td|./th")
        if not cells:
            continue
        row_data = {}
        for idx, cell in enumerate(cells):
            column_name = headers[idx] if idx < len(headers) else f"Column_{idx+1}"
            row_data[column_name] = cell.text.strip()
        rows_data.append(row_data)

    return {
        'columns': headers,
        'rows': rows_data,
        'total_rows': len(rows_data)
    }


def get_ipo_watch_data_tool(ipo_id: str) -> dict:
    """
    Fetch IPO details and table data from IPO Watch website using Selenium.
    
    Args:
        ipo_id (str): The IPO ID to fetch data for (e.g., 'fusion-klassroom').
    
    Returns:
        dict: A dictionary containing the status of the request, the data fetched, and a message
    """
    
    driver = None
    try:
        webdriver, TimeoutException, By, WebDriverWait, EC, Options = _import_selenium()
        logger.info(f"Fetching IPO data from IPO Watch for IPO ID: {ipo_id}")
        
        # Set up Chrome options for headless mode (optional, remove for visible browser)
        chrome_options = Options()
        # chrome_options.add_argument("--headless")  # Uncomment to run in headless mode
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        
        # Create driver instance
        driver = webdriver.Chrome(options=chrome_options)
        
        # Navigate to the URL
        url = f"https://ipowatch.in/{ipo_id}/"
        logger.info(f"Navigating to: {url}")
        driver.get(url)
        
        # Wait for page to load
        time.sleep(3)
        
        # Wait for table to be present
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_all_elements_located((By.TAG_NAME, "table")))
        
        logger.info("Page loaded successfully. Extracting table data...")
        
        # Find all tables on the page
        tables = driver.find_elements(By.TAG_NAME, "table")
        
        if not tables:
            return {
                'status': 'error',
                'data': None,
                'message': f"No tables found on the IPO Watch page for IPO ID: {ipo_id}"
            }
        
        # Extract data from all tables
        tables_data = []
        
        for idx, table in enumerate(tables):
            try:
                parsed = _parse_html_table(table, By)
                if parsed['rows']:
                    tables_data.append({
                        'table_number': idx + 1,
                        'data': parsed['rows'],
                        'columns': parsed['columns']
                    })
                    logger.info(f"Extracted table {idx + 1} with {len(parsed['rows'])} rows")
            
            except Exception as e:
                logger.warning(f"Could not parse table {idx + 1}: {str(e)}")
                continue
        
        if not tables_data:
            return {
                'status': 'error',
                'data': None,
                'message': "Could not extract data from any tables on the page"
            }
        
        logger.info(f"Successfully extracted data from {len(tables_data)} table(s)")
        
        return {
            'status': 'success',
            'data': {
                'ipo_id': ipo_id,
                'url': url,
                'tables': tables_data,
                'total_tables': len(tables_data)
            },
            'message': f"Successfully fetched IPO data from IPO Watch. Extracted {len(tables_data)} table(s)."
        }
    
    except Exception as e:
        logger.error(f"Error fetching IPO Watch data: {str(e)}")
        return {
            'status': 'error',
            'data': None,
            'message': f"Failed to fetch IPO data from IPO Watch. Error: {str(e)}"
        }
    
    finally:
        # Close the browser
        if driver:
            try:
                driver.quit()
                logger.info("Browser closed successfully")
            except Exception as cleanup_error:
                logger.warning(f"Failed to close browser cleanly: {cleanup_error}")


def get_gmp_data_from_ipo_watch_tool(ipo_id: str) -> dict:
    """
    Fetch GMP (Gray Market Premium) history data from IPO Watch website.
    Extracts data from the 2nd table with class 'wp-block-table has-medium-font-size'
    which contains: Date, IPO GMP, GMP Trend, Gain, Last Updated
    
    Args:
        ipo_id (str): The IPO ID to fetch data for (e.g., 'fusion-klassroom').
    
    Returns:
        dict: A dictionary containing the status of the request, GMP history data, and a message
    """
    
    try:
        logger.info(f"Fetching GMP data from IPO Watch for IPO ID: {ipo_id}")
        
        url = f"https://ipowatch.in/{ipo_id}-gmp-grey-market-premium/"
        logger.info(f"Fetching GMP page via HTTP: {url}")
        html_text = _fetch_html(url)
        tables = _parse_html_tables(html_text)
        
        if not tables:
            return {
                'status': 'error',
                'data': None,
                'message': f"No tables found on GMP page for IPO ID: {ipo_id}."
            }

        def _is_gmp_table(table_rows):
            if not table_rows:
                return False
            headers = [cell.strip().lower() for cell in table_rows[0]]
            required = {"date", "ipo gmp", "gmp trend", "gain"}
            found = set(headers)
            return required.issubset(found) or any(key in ' '.join(headers) for key in ["ipo gmp", "gmp trend", "gain"])

        gmp_table = None
        for table_rows in tables:
            if _is_gmp_table(table_rows):
                gmp_table = table_rows
                break

        if gmp_table is None and len(tables) >= 2:
            logger.warning("Could not detect GMP table by headers; falling back to second table")
            gmp_table = tables[1]

        if gmp_table is None:
            return {
                'status': 'error',
                'data': None,
                'message': f"Could not locate the GMP table on the GMP page for IPO ID: {ipo_id}."
            }

        headers = [h if h else f"Column_{idx+1}" for idx, h in enumerate(gmp_table[0])]
        rows = []
        from itertools import zip_longest
        for row in gmp_table[1:]:
            row_data = {headers[idx]: (cell or "") for idx, cell in enumerate(row)}
            rows.append(row_data)

        return {
            'status': 'success',
            'data': {
                'ipo_id': ipo_id,
                'url': url,
                'table_type': 'GMP History',
                'columns': headers,
                'rows': rows,
                'total_rows': len(rows)
            },
            'message': f"Successfully fetched GMP history from IPO Watch with {len(rows)} records."
        }
    except (HTTPError, URLError) as e:
        message = str(e) or repr(e)
        logger.error(f"HTTP error fetching GMP page: {message}")
        return {
            'status': 'error',
            'data': None,
            'message': f"Failed to fetch GMP data via HTTP. Error: {message}"
        }
    except Exception as e:
        message = str(e) or repr(e)
        logger.error(f"Error fetching IPO Watch GMP data: {message}")
        return {
            'status': 'error',
            'data': None,
            'message': f"Failed to fetch GMP data from IPO Watch. Error: {message}"
        }


def get_specific_table_from_ipo_watch_tool(ipo_id: str, table_number: int = 1) -> dict:
    """
    Fetch a specific table data from IPO Watch website using Selenium.
    
    Args:
        ipo_id (str): The IPO ID to fetch data for (e.g., 'fusion-klassroom').
        table_number (int): The table number to extract (1-indexed). Default is 1 (first table).
    
    Returns:
        dict: A dictionary containing the status of the request, the table data, and a message
    """
    
    driver = None
    try:
        logger.info(f"Fetching table {table_number} from IPO Watch for IPO ID: {ipo_id}")
        
        webdriver, TimeoutException, By, WebDriverWait, EC, Options = _import_selenium()
        # Set up Chrome options
        chrome_options = Options()
        # chrome_options.add_argument("--headless")  # Uncomment to run in headless mode
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        
        # Create driver instance
        driver = webdriver.Chrome(options=chrome_options)
        
        # Navigate to the URL
        url = f"https://ipowatch.in/{ipo_id}/"
        logger.info(f"Navigating to: {url}")
        driver.get(url)
        
        # Wait for page to load
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        # Wait for tables to be present
        wait.until(EC.presence_of_all_elements_located((By.TAG_NAME, "table")))
        
        # Find all tables on the page
        tables = driver.find_elements(By.TAG_NAME, "table")
        
        if not tables:
            return {
                'status': 'error',
                'data': None,
                'message': f"No tables found on the IPO Watch page for IPO ID: {ipo_id}"
            }
        
        if table_number > len(tables) or table_number < 1:
            return {
                'status': 'error',
                'data': None,
                'message': f"Table number {table_number} not found. Available tables: {len(tables)}"
            }
        
        # Get the specific table
        target_table = tables[table_number - 1]
        
        try:
            parsed = _parse_html_table(target_table, By)
            logger.info(f"Successfully extracted table {table_number} with {len(parsed['rows'])} rows")
            
            return {
                'status': 'success',
                'data': {
                    'ipo_id': ipo_id,
                    'table_number': table_number,
                    'columns': parsed['columns'],
                    'rows': parsed['rows'],
                    'total_rows': parsed['total_rows']
                },
                'message': f"Successfully fetched table {table_number} from IPO Watch with {parsed['total_rows']} rows."
            }
        except Exception as e:
            logger.error(f"Error parsing table {table_number}: {str(e)}")
            return {
                'status': 'error',
                'data': None,
                'message': f"Failed to parse table {table_number}. Error: {str(e)}"
            }
    
    except Exception as e:
        logger.error(f"Error fetching IPO Watch data: {str(e)}")
        return {
            'status': 'error',
            'data': None,
            'message': f"Failed to fetch IPO data from IPO Watch. Error: {str(e)}"
        }
    
    finally:
        # Close the browser
        if driver:
            try:
                driver.quit()
                logger.info("Browser closed successfully")
            except Exception as cleanup_error:
                logger.warning(f"Failed to close browser cleanly: {cleanup_error}")
