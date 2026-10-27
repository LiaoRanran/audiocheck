"""`python -m audiocheck.ui.editor` 启动编辑工作站。"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from .. import theme as T
from .shell import EditorWindow


def main() -> int:
    app = QApplication.instance() or QApplication([])
    T.init_fonts(app)
    w = EditorWindow(T.THEMES["dark"])
    w.resize(1280, 800)
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
