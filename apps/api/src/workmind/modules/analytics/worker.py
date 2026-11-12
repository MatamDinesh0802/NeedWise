"""A query worker process (engine.QueryPool): `python -m workmind.modules.analytics.worker MB`.

It reads one JSON request per line on stdin and answers each on stdout, running the query with
engine.py's sandboxed connection. It exits when stdin closes, or when its resident memory passes
MB megabytes (exit status engine.EXIT_OUT_OF_MEMORY).
"""

import sys

from workmind.modules.analytics.engine import serve

if __name__ == "__main__":
    serve(int(sys.argv[1]))
