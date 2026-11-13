# Local modification (2026-11-13): Windows workflow trim and/or secret-free diagnostics; see NOTICE.
"""Windows-only key extraction, trimmed from the pinned upstream package."""
import platform

def extract_keys(db_dir, output_path, pid=None):
    if platform.system().lower() != "windows":
        raise RuntimeError("This bundled reader supports Windows only")
    from .scanner_windows import extract_keys as extract
    return extract(db_dir, output_path, pid=pid)
