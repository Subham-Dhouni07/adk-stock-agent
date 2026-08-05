"""
Test script for gmp_lambda.py functions
This script demonstrates how to use the IPO Watch scraping tools
"""

import json
from gmp_lambda import (
    get_ipo_watch_data_tool,
    get_gmp_data_from_ipo_watch_tool,
    get_specific_table_from_ipo_watch_tool
)

from agent import get_all_ipo_tool, ipo_news_tool


def print_result(title, result):
    """Pretty print the result"""
    print("\n" + "=" * 80)
    print(f"TEST: {title}")
    print("=" * 80)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("=" * 80 + "\n")


def main():
    """Main test function"""
    
    # # Example IPO ID - change this to test with different IPOs
    # ipo_id = "indo-mim-ipo"
    
    # print("\n" + "🔍" * 40)
    # print(f"Testing IPO Watch Scraper with IPO ID: {ipo_id}")
    # print("🔍" * 40 + "\n")
    
    # # Test 1: Fetch GMP data from the 2nd table
    # print("\n📊 Test 1: Fetching GMP History Data...")
    # print("-" * 80)
    # result_gmp = get_gmp_data_from_ipo_watch_tool(ipo_id)
    # print_result("GET GMP DATA FROM IPO WATCH", result_gmp)
    
    # if result_gmp['status'] == 'success':
    #     print("✅ GMP Data Retrieved Successfully!")
    #     print(f"   - Total Records: {result_gmp['data']['total_rows']}")
    #     print(f"   - Columns: {', '.join(result_gmp['data']['columns'])}")
    #     print(f"   - Sample Data: {result_gmp['data']['rows'][0] if result_gmp['data']['rows'] else 'No data'}")
    # else:
    #     print(f"❌ Error: {result_gmp['message']}")
    
    # get_all_ipo_tool()
    res = ipo_news_tool("Indo-MIM IPO")
    print("Final response from ipo_news_tool:", res)
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Error occurred: {str(e)}")
        import traceback
        traceback.print_exc()
