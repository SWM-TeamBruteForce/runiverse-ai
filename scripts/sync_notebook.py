"""src 전처리·분석 모듈을 baseline_colab 노트북의 '%%writefile' 셀에 그대로 복사한다.

src를 고친 뒤 실행: python scripts/sync_notebook.py
일치 여부는 tests/test_notebook_sync.py가 검사한다.
"""

import json
from pathlib import Path

NOTEBOOK = Path("notebooks/baseline_colab.ipynb")
SRC = Path("src/runiverse_music")
# 노트북에서 import하는 순서 (의존하는 모듈이 뒤)
MODULES = ("mapping_rules", "music_params", "prompting", "schemas", "config", "generator", "storage", "analyzer", "pipeline")


def main() -> None:
    root = Path(__file__).parents[1]
    notebook = json.loads((root / NOTEBOOK).read_text(encoding="utf-8"))
    for cell in notebook["cells"]:
        first = "".join(cell["source"]).partition("\n")[0]
        name = Path(first.split()[-1]).stem if first.startswith("%%writefile ") else None
        if cell["cell_type"] == "code" and name in MODULES:
            body = (root / SRC / f"{name}.py").read_text(encoding="utf-8")
            cell["source"] = f"{first}\n{body}".splitlines(keepends=True)
    text = json.dumps(notebook, ensure_ascii=False, indent=1) + "\n"
    (root / NOTEBOOK).write_text(text, encoding="utf-8", newline="\n")
    print("동기화:", NOTEBOOK)


if __name__ == "__main__":
    main()
