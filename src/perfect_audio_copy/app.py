"""Application entry point: Qt setup, the dark theme, and the QML main window."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from perfect_audio_copy import APP_ID, APP_NAME, __version__

QML_DIR = Path(__file__).resolve().parent / "ui" / "qml"
ICON_PATH = Path(__file__).resolve().parent / "ui" / "icons" / f"{APP_ID}.svg"
#: The app draws every control from its own design tokens (ui/qml/Pac), on top of
#: Qt Quick Controls' fully customisable Basic style.
STYLE = "Basic"


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="perfect-audio-copy", description=APP_NAME)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="load the UI, verify it started, then exit (used by tests/CI)",
    )
    parser.add_argument(
        "--demo", action="store_true", help="run against a simulated drive (no CD, offline)"
    )
    parser.add_argument(
        "--install-desktop",
        action="store_true",
        help="add the app to the application menu for this user (for uv/pipx installs)",
    )
    parser.add_argument(
        "--uninstall-desktop", action="store_true", help="remove what --install-desktop added"
    )
    parser.add_argument("--demo-rip", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--demo-speed", type=float, default=12.0, help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", metavar="PNG", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot-after", type=int, default=1500, help=argparse.SUPPRESS)
    parser.add_argument("--size", default="", help=argparse.SUPPRESS)  # WxH
    parser.add_argument("--open-dialog", default="", help=argparse.SUPPRESS)
    # Unknown args (e.g. Qt's -platform) are passed through to Qt.
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv: Sequence[str] | None = None) -> int:  # noqa: PLR0915
    argv = list(sys.argv if argv is None else argv)
    args = _parse_args(argv[1:])
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.install_desktop or args.uninstall_desktop:
        from perfect_audio_copy import desktop_install

        if args.install_desktop:
            for path in desktop_install.install():
                print(f"installed {path}")
        else:
            for path in desktop_install.uninstall():
                print(f"removed {path}")
        return 0

    # Qt is imported lazily so `--version`/`--help` work without a display.
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from perfect_audio_copy.ui.backend.controller import Backend
    from perfect_audio_copy.ui.backend.images import SectorMapProvider
    from perfect_audio_copy.ui.backend.theme import IconProvider, apply_theme
    from perfect_audio_copy.ui.backend.udev import DiscWatcher
    from perfect_audio_copy.ui.backend.wizard import DriveWizard

    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationVersion(__version__)
    QApplication.setOrganizationName("PerfectAudioCopy")
    QApplication.setDesktopFileName(APP_ID)
    QQuickStyle.setStyle(STYLE)

    app = QApplication(argv)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    mono = apply_theme(app)

    if args.demo:
        from perfect_audio_copy.ui.backend.demo import demo_backend_kwargs

        backend = Backend(**demo_backend_kwargs(speed=args.demo_speed))
    else:
        backend = Backend()
    wizard = DriveWizard(backend)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    icons = IconProvider()
    maps = SectorMapProvider(backend.sector_maps)
    engine.addImageProvider("icon", icons)
    engine.addImageProvider("cover", backend.cover_provider)
    engine.addImageProvider("sectormap", maps)
    ctx = engine.rootContext()
    ctx.setContextProperty("pacMonoFamily", mono)
    ctx.setContextProperty("backend", backend)
    ctx.setContextProperty("wizard", wizard)
    engine.setInitialProperties({"appVersion": __version__})
    engine.warnings.connect(
        lambda warnings: [print(w.toString(), file=sys.stderr) for w in warnings]
    )
    engine.load(QML_DIR / "Main.qml")
    if not engine.rootObjects():
        print("error: failed to load the QML user interface", file=sys.stderr)
        return 1
    window = engine.rootObjects()[0]
    if args.size:
        w, _, h = args.size.partition("x")
        window.setProperty("width", int(w))
        window.setProperty("height", int(h))

    watcher = DiscWatcher(lambda action, dev: backend.post(lambda: backend.udevEvent(action, dev)))
    if not (args.smoke_test or args.demo or args.screenshot):
        watcher.start()

    if args.smoke_test:
        QTimer.singleShot(0, app.quit)
        code = int(app.exec())
        print(f"smoke-test ok: style={QQuickStyle.name()}")
        return code

    if args.demo_rip:

        def start_when_ready() -> None:
            if backend.property("state") == "ready" and backend.property("coverUrl"):
                backend.startRip("")
            else:
                QTimer.singleShot(100, start_when_ready)

        QTimer.singleShot(300, start_when_ready)

    if args.open_dialog:
        from PySide6.QtCore import Q_ARG, QMetaObject

        name = args.open_dialog
        QTimer.singleShot(
            1200,
            lambda: QMetaObject.invokeMethod(window, "openDialog", Q_ARG("QVariant", name)),
        )

    if args.screenshot:
        out = Path(args.screenshot)

        def shoot() -> None:
            window.grabWindow().save(str(out))
            app.quit()

        QTimer.singleShot(args.screenshot_after, shoot)

    code = int(app.exec())
    watcher.stop()
    del icons, maps, engine
    return code


if __name__ == "__main__":
    raise SystemExit(main())
