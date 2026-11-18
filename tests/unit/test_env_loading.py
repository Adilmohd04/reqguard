"""reqguard.cli.main must resolve its .env path with find_dotenv(usecwd=True).

load_dotenv() has no usecwd kwarg of its own -- when dotenv_path is omitted
it calls find_dotenv() with ITS default, usecwd=False, which walks up from
the INSTALLED file's own location (e.g. inside site-packages/, or a `uv
tool` venv), never from the directory the user actually ran the command in.
For a real pip/pipx/uv-tool install -- the one the README tells every new
user to use -- that means a `.env` dropped in the target project's root
(exactly what the README says to do) is silently never read. Only an
editable dev install (`pip install -e .`) hid this, because the installed
file and the repo root were the same tree.

Gate: pytest tests/unit/test_env_loading.py -q
"""

from __future__ import annotations

import ast
from pathlib import Path

import reqguard.cli.main as cli_main


def test_cli_main_resolves_dotenv_path_with_usecwd_true():
    source = Path(cli_main.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    load_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "load_dotenv"
    ]
    assert load_calls, "no load_dotenv(...) call found in reqguard.cli.main"
    (load_call,) = load_calls

    dotenv_path_kwargs = [kw for kw in load_call.keywords if kw.arg == "dotenv_path"]
    assert dotenv_path_kwargs, "load_dotenv(...) must pass dotenv_path= explicitly"
    find_call = dotenv_path_kwargs[0].value
    assert (
        isinstance(find_call, ast.Call)
        and isinstance(find_call.func, ast.Name)
        and find_call.func.id == "find_dotenv"
    ), "dotenv_path= must come from a find_dotenv(...) call"

    usecwd_kwargs = [kw for kw in find_call.keywords if kw.arg == "usecwd"]
    assert usecwd_kwargs, "find_dotenv(...) call is missing usecwd=True"
    assert usecwd_kwargs[0].value.value is True
