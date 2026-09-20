"""Startpunt van de applicatie."""

from __future__ import annotations

import sys

from . import __app_name__


def main(argv: list[str] | None = None) -> int:
    """Start de GUI. Geeft de exitcode van de Qt-applicatie terug."""
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.stderr.write(
            "PySide6 is niet geinstalleerd.\n"
            "Installeer de afhankelijkheden met: pip install -r requirements.txt\n"
        )
        return 1

    from .gui.main_window import MainWindow
    from .resources import icon_path

    if sys.platform == "win32":
        # Zorgt dat Windows de app een eigen taakbalkpictogram geeft.
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "dupefinder.duplicatemediafinder"
            )
        except Exception:
            pass

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(__app_name__)
    app.setOrganizationName(__app_name__)
    icon_file = icon_path()
    if icon_file is not None:
        app.setWindowIcon(QIcon(str(icon_file)))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
