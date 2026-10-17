"""Defaults and thresholds, in one place.

Quality-gate thresholds are starting values to be tuned against the examples.
"""

from __future__ import annotations

# Word-count flags (section 7). Exactly the boundary values are in range.
WORD_COUNT_MIN = 500
WORD_COUNT_MAX = 1000

# Quality gate (section 6.5)
MAX_WORDS_FOR_GRADING = 15_000
EXTRACTOR_MAX_WORD_DIFF = 0.05      # relative difference in word counts
EXTRACTOR_MIN_TOKEN_OVERLAP = 0.95  # |A & B| / |A | B| on token sets
MAX_TEXT_FREE_PAGE_FRACTION = 0.5   # fraction of PDF pages with no text
GARBLE_MIN_NORMAL_CHAR_FRACTION = 0.85
GARBLE_MAX_BAD_CHAR_FRACTION = 0.005
GARBLE_MIN_FUNCTION_WORD_FRACTION = 0.15
# Garble checks are meaningless on very short text.
GARBLE_MIN_WORDS_TO_CHECK = 30

# Hidden-text heuristics (section 6.3 and 6.4)
HIDDEN_FONT_SIZE_PT = 2.0
NEAR_WHITE_THRESHOLD = 0.95

# Resource limits for untrusted files
MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024

# De-identification (section 8)
MIN_STANDALONE_NAME_TOKEN_LEN = 3
NAME_PLACEHOLDER = "[NAME]"

# Models (section 10)
DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_SAMPLES = 1
DEFAULT_REASONING_EFFORT = "medium"   # SGC_REASONING_EFFORT overrides; "off" omits the parameter

# USD per 1M tokens: (input, output). Used only for --max-spend and the cost estimate.
MODEL_PRICES_PER_MILLION = {
    "gpt-6-astra": (10.0, 50.0),
    "gpt-6.1-sol": (2.0, 10.0),
    "gpt-6-luna": (0.10, 0.50),
}

MAX_OUTPUT_TOKENS = {"screening": 8_000, "grading": 12_000, "weakest_area": 8_000}
REQUEST_TIMEOUT_SECONDS = 300.0
MAX_NETWORK_ATTEMPTS = 5            # per call; network errors, 429, 5xx
NETWORK_BACKOFF_BASE_SECONDS = 2.0
NETWORK_BACKOFF_MAX_SECONDS = 60.0
MAX_VALIDATION_ATTEMPTS = 3         # one call plus up to two retries when the output is invalid
MIN_VALID_SAMPLES = 2               # fewer valid grading samples than this means Not graded
MAX_EVIDENCE_QUOTES = 3
MAX_HIDDEN_TEXT_IN_PROMPT_CHARS = 3_000
HIDDEN_EXCERPT_CHARS = 300

# Output naming (section 12)
REPORT_PREFIX = "sgc_report_"
RUN_DIR_PREFIX = "sgc_run_"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H%M%S"
