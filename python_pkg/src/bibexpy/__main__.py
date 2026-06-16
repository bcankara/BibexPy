"""Package entry point for ``python -m bibexpy``; delegates to the CLI ``main()``."""

from bibexpy.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
