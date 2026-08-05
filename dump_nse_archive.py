import argparse
import csv
import gzip
import io
import json
from pathlib import Path


def _resolve_input_path(input_arg: Path | None) -> Path:
    if input_arg is not None:
        return input_arg

    candidates = [
        Path("stock_picker_agent/NSE.json.gz"),
        Path("stock_picker_agent/NSE.csv.gz"),
        Path("NSE.json.gz"),
        Path("NSE.csv.gz"),
        Path("nse_archive.json"),
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return Path("stock_picker_agent/NSE.json.gz")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract a compressed NSE instrument archive to a readable JSON file."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to the source .gz archive file or an existing JSON file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("nse_archive.json"),
        help="Path to the destination JSON file.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Write pretty-formatted JSON output.",
    )
    args = parser.parse_args()
    input_path = _resolve_input_path(args.input)

    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    if input_path.name.lower().endswith(".gz"):
        with gzip.open(input_path, "rt", encoding="utf-8") as handle:
            raw_text = handle.read()
    else:
        with input_path.open("r", encoding="utf-8") as handle:
            raw_text = handle.read()

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        reader = csv.DictReader(io.StringIO(raw_text))
        payload = list(reader)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if args.pretty else None)
        handle.write("\n")

    print(f"Extracted {input_path} to {args.output}")


if __name__ == "__main__":
    main()
