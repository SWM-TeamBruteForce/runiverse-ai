import json
from pathlib import Path

import pytest

from sync_notebook import MODULES, NOTEBOOK, SRC

ROOT = Path(__file__).parents[1]


def notebook_copies():
    """노트북의 '%%writefile .../{모듈}.py' 셀 → {모듈 이름: 본문}."""
    cells = json.loads((ROOT / NOTEBOOK).read_text(encoding="utf-8"))["cells"]
    copies = {}
    for cell in cells:
        first, _, body = "".join(cell["source"]).partition("\n")
        if cell["cell_type"] == "code" and first.startswith("%%writefile "):
            copies[Path(first.split()[1]).stem] = body
    return copies


@pytest.mark.parametrize("name", MODULES)
def test_notebook_copy_matches_src(name):
    # 다르면: python scripts/sync_notebook.py
    assert notebook_copies().get(name) == (ROOT / SRC / f"{name}.py").read_text(encoding="utf-8")
