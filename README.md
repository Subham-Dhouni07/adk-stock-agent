# ADK Stock Agent

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Subham-Dhouni07/adk-stock-agent/blob/main/LICENSE) [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

## Description

ADK Stock Agent is a Python project for stock selection and analysis using Indian NSE data. The repository contains modular agents for IPO research, stock evaluation, and stock picking workflows.

## Features

- IPO analysis and screening
- Stock evaluation and scoring
- Automated stock picking workflow
- Modular project structure for easy updates
- Test suite for validation

## Project Structure

- `ipo_agent/` — IPO analysis logic
- `stock_agent/` — core stock evaluation components
- `stock_picker_agent/` — stock picker pipeline and assets
- `tests/` — test cases
- `requirements.txt` — dependencies

## Installation

```bash
git clone https://github.com/Subham-Dhouni07/adk-stock-agent.git
cd adk-stock-agent
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

Run the main agent modules directly:

```bash
python stock_picker_agent/agent.py
python ipo_agent/agent.py
python stock_agent/agent.py
```

## Running Tests

```bash
python -m pytest tests
```

## Contributing

Contributions are welcome. Fork the repo, create a branch, add your changes, and open a pull request.

## License

MIT License. See `LICENSE` for details.
