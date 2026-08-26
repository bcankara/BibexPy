"""BibexPy: self-hosted bibliometric data preparation tool.

Installed via ``pip install bibexpy`` and launched with the ``bibexpy`` command.
Bundles an embedded FastAPI server and a prebuilt static UI in a single package,
requiring no Node.js or npm at runtime.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("bibexpy")
except PackageNotFoundError:  # running from source without an installed dist
    from bibexpy.cli import __version__  # cli.py holds the release literal

__all__ = ["__version__"]
