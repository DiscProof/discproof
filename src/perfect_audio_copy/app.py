"""Application entry point: sets up Qt, picks the QML style and loads the main window."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from perfect_audio_copy import APP_ID, APP_NAME, __version__

QML_DIR = Path(__file__).resolve().parent / "ui" / "qml"
ICON_PATH = Path(__file__).resolve().parent / "ui" / "icons" / f"{APP_ID}.svg"


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="perfect-audio-copy", description=APP_NAME)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="load the UI, verify it started, then exit (used by tests/CI)",
    )
    # Unknown args (e.g. Qt's -platform, -style) are passed through to Qt.
    args, _unknown = parser.parse_known_args(argv)
    return args


def _qml_module_available(import_paths: Sequence[str], *parts: str) -> bool:
    return any(Path(p, *parts, "qmldir").is_file() for p in import_paths)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = _parse_args(argv[1:])

    # Qt is imported lazily so `--version`/`--help` work without a display.
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationVersion(__version__)
    QApplication.setOrganizationName("PerfectAudioCopy")
    QApplication.setDesktopFileName(APP_ID)

    # QApplication (not QGuiApplication): the org.kde.desktop style renders through QStyle.
    app = QApplication(argv)
    app.setWindowIcon(QIcon(str(ICON_PATH)))

    engine = QQmlApplicationEngine()
    import_paths = engine.importPathList()

    # Native Plasma look (Breeze via qqc2-desktop-style) when available, otherwise Fusion.
    # An explicit QT_QUICK_CONTROLS_STYLE in the environment still wins.
    # (QQuickStyle.name() can't be used to detect that: it always reports a default.)
    if not os.environ.get("QT_QUICK_CONTROLS_STYLE"):
        if _qml_module_available(import_paths, "org", "kde", "desktop"):
            QQuickStyle.setStyle("org.kde.desktop")
        else:
            QQuickStyle.setStyle("Fusion")

    has_kirigami = _qml_module_available(import_paths, "org", "kde", "kirigami")
    engine.setInitialProperties(
        {
            "appVersion": __version__,
            "kirigamiAvailable": has_kirigami,
            "styleName": QQuickStyle.name(),
        }
    )
    engine.load(QML_DIR / "Main.qml")
    if not engine.rootObjects():
        print("error: failed to load the QML user interface", file=sys.stderr)
        return 1

    if args.smoke_test:
        QTimer.singleShot(0, app.quit)
        code = int(app.exec())
        print(f"smoke-test ok: style={QQuickStyle.name()} kirigami={has_kirigami}")
        return code

    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
