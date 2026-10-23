"""Start the web app: python -m leadgen.web  (then open http://127.0.0.1:5000)"""
import argparse
import logging
import os
import sys
import threading
import webbrowser

from dotenv import load_dotenv

from ..config import ENV_PATH
from . import create_app


def main(argv=None):
    ap = argparse.ArgumentParser(prog="leadgen.web", description="Dental lead generator web app")
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to listen on (default: this computer only)")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = ap.parse_args(argv)

    load_dotenv(ENV_PATH)
    password = os.getenv("LEADGEN_WEB_PASSWORD", "")
    if args.host not in ("127.0.0.1", "localhost") and not password:
        sys.exit("Refusing to listen on other devices without a password. Set LEADGEN_WEB_PASSWORD in .env "
                 "(or your shell) first. It protects your API keys and stops others spending your Google credit.")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    for noisy in ("urllib3", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    app = create_app(password)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    print(f"Dental Leads is running at {url}  (press Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
