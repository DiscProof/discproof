"""Qt side of the app: QObjects exposed to QML, image providers and worker threads.

The core packages (drive, engine, verify, metadata, output, config) stay Qt-free;
only this package and ``app.py`` import PySide6.
"""
