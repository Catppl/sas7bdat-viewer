# ruff: noqa: I001 - smoke mode must exit before importing the Qt application
import sys


if __name__ == "__main__":
    if sys.argv[1:] == ["--smoke-test"]:
        raise SystemExit(0)
    from p21_issue_explorer.app import main

    raise SystemExit(main())
