# SPDX-License-Identifier: Apache-2.0
"""Project-owned logger shim; avoids upstream root logger/stdout/file side effects."""
import logging
wxlog = logging.getLogger("wechat-rag.accessibility")
wxlog.addHandler(logging.NullHandler())
# Compatibility with the calling code; this is the same disabled logger.
wxlog.logger = wxlog
