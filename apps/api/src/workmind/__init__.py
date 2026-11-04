"""WorkMind AI backend."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("workmind")
except PackageNotFoundError:  # running from a source tree without an installed package
    __version__ = "0.0.0+unknown"
