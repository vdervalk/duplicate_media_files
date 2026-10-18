"""Voorbeeldpaneel: miniatuur plus gegevens van het geselecteerde bestand."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .workers import ThumbnailService

PREVIEW_SIZE = 320


class PreviewPanel(QWidget):
    """Toont een vergroot voorbeeld van het aangeklikte bestand.

    Het paneel vraagt zelf om miniaturen en onthoudt welke bestanden geen
    voorbeeld opleveren, zodat er niet telkens opnieuw geprobeerd wordt.
    """

    open_requested = Signal(str)
    reveal_requested = Signal(str)

    def __init__(self, thumbnails: ThumbnailService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumbnails = thumbnails
        self._thumbnails.ready.connect(self._on_thumbnail_ready)
        self._pixmaps: dict[str, QPixmap] = {}
        self._no_preview: set[str] = set()
        self._path: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 0, 0)

        title = QLabel("Voorbeeld")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        self.image_label = QLabel("Selecteer een bestand")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(PREVIEW_SIZE)
        self.image_label.setFrameShape(QFrame.Shape.StyledPanel)
        self.image_label.setStyleSheet("color: gray;")
        layout.addWidget(self.image_label)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.info_label)

        buttons = QHBoxLayout()
        open_button = QPushButton("Bestand openen")
        open_button.clicked.connect(lambda: self._emit(self.open_requested))
        reveal_button = QPushButton("Map openen")
        reveal_button.clicked.connect(lambda: self._emit(self.reveal_requested))
        buttons.addWidget(open_button)
        buttons.addWidget(reveal_button)
        layout.addLayout(buttons)

        layout.addStretch(1)

    # -- publieke API --------------------------------------------------------

    @property
    def path(self) -> str | None:
        return self._path

    def show_file(self, path: str | None, info_html: str = "") -> None:
        """Toon dit bestand, of leeg het paneel met ``None``."""
        self._path = path
        self.info_label.setText(info_html if path else "")
        if path is None:
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText("Selecteer een bestand")
            return

        pixmap = self._pixmaps.get(os.path.normcase(path))
        if pixmap is not None and not pixmap.isNull():
            self._show_pixmap(pixmap)
        elif os.path.normcase(path) in self._no_preview:
            self._show_unavailable()
        else:
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText("Voorbeeld laden...")
            self._thumbnails.request(path, PREVIEW_SIZE)

    def clear_cache(self) -> None:
        self._pixmaps.clear()
        self._no_preview.clear()

    # -- intern --------------------------------------------------------------

    def _emit(self, signal: Signal) -> None:
        if self._path:
            signal.emit(self._path)

    def _on_thumbnail_ready(self, path: str, size: int, data: object) -> None:
        if size != PREVIEW_SIZE:
            return
        key = os.path.normcase(path)
        pixmap = QPixmap()
        if isinstance(data, (bytes, bytearray)) and data:
            pixmap.loadFromData(bytes(data), "PNG")
        if pixmap.isNull():
            self._no_preview.add(key)
            if self._path and os.path.normcase(self._path) == key:
                self._show_unavailable()
            return
        self._pixmaps[key] = pixmap
        if self._path and os.path.normcase(self._path) == key:
            self._show_pixmap(pixmap)

    def _show_pixmap(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(
            max(64, self.image_label.width() - 8),
            PREVIEW_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setText("")
        self.image_label.setPixmap(scaled)

    def _show_unavailable(self) -> None:
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText("Geen voorbeeld beschikbaar")
