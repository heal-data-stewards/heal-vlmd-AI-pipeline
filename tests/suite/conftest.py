import sys
from pathlib import Path

SUITE = Path(__file__).parent
ROOT = SUITE.parent.parent
for p in (str(ROOT), str(SUITE)):
    if p not in sys.path:
        sys.path.insert(0, p)


def pytest_addoption(parser):
    parser.addoption(
        "--run-llm",
        action="store_true",
        help="Also run suite cases that need LLM credentials (tier: llm)",
    )
    parser.addoption(
        "--run-local",
        action="store_true",
        help="Also run suite cases that need local source files (tier: local)",
    )
