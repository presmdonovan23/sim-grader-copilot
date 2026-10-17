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
DEFAULT_SAMPLES = 3

# Output naming (section 12)
REPORT_PREFIX = "sgc_report_"
RUN_DIR_PREFIX = "sgc_run_"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H%M%S"
