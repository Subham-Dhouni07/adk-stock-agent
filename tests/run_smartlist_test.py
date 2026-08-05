import json
import sys
from pathlib import Path

# Ensure workspace root is on sys.path so local package imports work when running the script directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_picker_agent.agent import get_stock_suggestions


if __name__ == '__main__':
    res = get_stock_suggestions("suggest me the stock under 100 which has the chance of increasing tomorrow")
    print(json.dumps(res, default=str, indent=2))
