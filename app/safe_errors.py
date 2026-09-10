"""Shared helper for turning an exception into safe-to-log/store text.

A third-party library's exception message (OpenAI, boto3, psycopg) is not
something this code controls the content of, and has been found to be able
to echo back request/response content in some cases (see the OpenAI
refusal-message fix in openai_scorer.py) - so no exception message or args
are ever logged or stored anywhere in this project, only its type name plus
a frame trail (file/function/line, never the message). See DECISIONS.md, and
tests/test_no_sensitive_data_in_logs.py for the test that verifies this
holds even with synthetic sensitive data forced through this exact path -
including through the global JSON log formatter, which used to bypass this
rule via Python's own formatException().
"""

import traceback


def safe_stack_trace(tb) -> str:
    """Stack frames only - file/line/function - built from a raw traceback
    object, never an exception's own str()/args."""
    return "".join(traceback.format_tb(tb))
