from __future__ import annotations

import os
import sys

import uvicorn


def main() -> None:
    port_text = os.environ.get("NSE_BREAKOUT_PORT", "8000")
    try:
        port = int(port_text)
    except ValueError as error:
        raise SystemExit("NSE_BREAKOUT_PORT must be a valid port number.") from error
    if not 1024 <= port <= 65535:
        raise SystemExit("NSE_BREAKOUT_PORT must be between 1024 and 65535.")
    try:
        uvicorn.run(
            "nse_fo_breakout.web:app",
            host="127.0.0.1",
            port=port,
            reload=False,
            access_log=False,
        )
    except OSError as error:
        raise SystemExit(f"Could not start the local dashboard: {error}") from error


if __name__ == "__main__":
    main()
