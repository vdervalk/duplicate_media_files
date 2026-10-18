"""Tabblad met onscherpe foto's."""

from __future__ import annotations

import math
import os
from datetime import datetime

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..blurscan import SharpnessResult
from ..exporter import export_sharpness_csv
from ..scanner import format_bytes
from ..sharpness import DEFAULT_BLUR_THRESHOLD, SharpnessEntry
from .preview import PreviewPanel
from .workers import ThumbnailService

PATH_ROLE = Qt.ItemDataRole.UserRole
SORT_ROLE = Qt.ItemDataRole.UserRole + 1

THUMBNAIL_SIZE = 64

#: Grenzen van de schuifregelaar; de schaal is logaritmisch omdat scherptescores
#: over meerdere ordes van grootte lopen.
MIN_THRESHOLD = 5.0
MAX_THRESHOLD = 1000.0


def slider_to_threshold(value: int) -> float:
    fraction = max(0, min(100, value)) / 100
    return MIN_THRESHOLD * (MAX_THRESHOLD / MIN_THRESHOLD) ** fraction


def threshold_to_slider(threshold: float) -> int:
    threshold = max(MIN_THRESHOLD, min(MAX_THRESHOLD, threshold))
    fraction = math.log(threshold / MIN_THRESHOLD) / math.log(MAX_THRESHOLD / MIN_THRESHOLD)
    return int(round(fraction * 100))


class _SortableItem(QTreeWidgetItem):
    """Sorteert kolommen op hun onderliggende getal in plaats van op tekst."""

    def __lt__(self, other: QTreeWidgetItem) -> bool:  # noqa: D105 - Qt-naamgeving
        column = self.treeWidget().sortColumn() if self.treeWidget() else 0
        mine = self.data(column, SORT_ROLE)
        theirs = other.data(column, SORT_ROLE)
        if mine is not None and theirs is not None:
            return float(mine) < float(theirs)
        return super().__lt__(other)


class BlurTab(QWidget):
    """Toont gemeten foto's, van meest verdacht naar scherp."""

    delete_requested = Signal(list)
    status_message = Signal(str)

    def __init__(self, thumbnails: ThumbnailService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.thumbnails = thumbnails
        self.thumbnails.ready.connect(self._on_thumbnail_ready)
        self.result: SharpnessResult | None = None
        self._items_by_path: dict[str, QTreeWidgetItem] = {}
        self._pixmaps: dict[str, QPixmap] = {}
        self._no_thumbnail: set[str] = set()
        self._updating = False

        self._build_ui()

        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(120)
        self._thumb_timer.timeout.connect(self._request_visible_thumbnails)

    # ------------------------------------------------------------------ opbouw

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)

        layout.addLayout(self._build_controls())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_tree())
        self.preview = PreviewPanel(self.thumbnails)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 340])
        layout.addWidget(splitter, stretch=1)

        layout.addLayout(self._build_actions())

    def _build_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()

        row.addWidget(QLabel("Grens:"))
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(0, 100)
        self.threshold_slider.setValue(threshold_to_slider(DEFAULT_BLUR_THRESHOLD))
        self.threshold_slider.setMaximumWidth(260)
        self.threshold_slider.setToolTip(
            "Foto's met een score onder deze grens gelden als onscherp.\n"
            "Schuif naar rechts om strenger te zijn."
        )
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        row.addWidget(self.threshold_slider)

        self.threshold_label = QLabel("")
        self.threshold_label.setMinimumWidth(70)
        row.addWidget(self.threshold_label)

        self.only_blurry_check = QCheckBox("Alleen verdachte foto's")
        self.only_blurry_check.setChecked(True)
        self.only_blurry_check.toggled.connect(lambda _c: self._apply_visibility())
        row.addWidget(self.only_blurry_check)

        self.low_detail_check = QCheckBox("Ook 'weinig detail'")
        self.low_detail_check.setToolTip(
            "Foto's zonder contrast (lucht, muur, sneeuw) scoren laag zonder onscherp te zijn."
        )
        self.low_detail_check.toggled.connect(lambda _c: self._apply_visibility())
        row.addWidget(self.low_detail_check)

        self.refresh_button = QPushButton("Vernieuwen")
        self.refresh_button.setToolTip("Haal foto's weg die niet meer op schijf staan.")
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button)

        row.addStretch(1)
        self.count_label = QLabel("")
        row.addWidget(self.count_label)
        return row

    def _build_tree(self) -> QWidget:
        self.tree = QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(
            ["Bestand", "Score", "Oordeel", "Afmetingen", "Grootte", "Gewijzigd", "Map"]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setIconSize(QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSortingEnabled(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemDoubleClicked.connect(
            lambda item, _col: self.preview.reveal_requested.emit(str(item.data(0, PATH_ROLE)))
        )
        self.tree.verticalScrollBar().valueChanged.connect(lambda _v: self._thumb_timer.start())
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(2, 120)
        self.tree.setColumnWidth(3, 110)
        self.tree.setColumnWidth(4, 90)
        self.tree.setColumnWidth(5, 130)
        return self.tree

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        select_button = QPushButton("Zichtbare foto's aanvinken")
        select_button.clicked.connect(self._check_visible)
        clear_button = QPushButton("Vinkjes wissen")
        clear_button.clicked.connect(self._clear_checks)
        export_button = QPushButton("Exporteren naar CSV")
        export_button.clicked.connect(self._export_csv)

        self.delete_button = QPushButton("Aangevinkte foto's naar prullenbak")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(
            lambda: self.delete_requested.emit(self.checked_paths())
        )

        self.summary_label = QLabel("")

        row.addWidget(select_button)
        row.addWidget(clear_button)
        row.addWidget(export_button)
        row.addStretch(1)
        row.addWidget(self.summary_label)
        row.addWidget(self.delete_button)
        return row

    # ------------------------------------------------------------------ vullen

    @property
    def threshold(self) -> float:
        return slider_to_threshold(self.threshold_slider.value())

    def set_result(self, result: SharpnessResult) -> None:
        self.result = result
        self._populate()

    def clear(self) -> None:
        self.result = None
        self._items_by_path.clear()
        self._pixmaps.clear()
        self._no_thumbnail.clear()
        self.tree.clear()
        self.preview.show_file(None)
        self._update_counts()

    def _populate(self) -> None:
        self._updating = True
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self._items_by_path.clear()

        for entry in (self.result.sorted_entries() if self.result else []):
            item = _SortableItem(self.tree)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setText(0, entry.name)
            item.setToolTip(0, entry.path)
            item.setText(1, f"{entry.score:.0f}")
            item.setText(3, f"{entry.width} x {entry.height}" if entry.width else "")
            item.setText(4, format_bytes(entry.size))
            item.setText(5, self._timestamp(entry.mtime_ns))
            item.setText(6, entry.folder)
            item.setData(0, PATH_ROLE, entry.path)
            item.setData(0, SORT_ROLE, 0.0)
            item.setData(1, SORT_ROLE, entry.score)
            item.setData(3, SORT_ROLE, float(entry.width * entry.height))
            item.setData(4, SORT_ROLE, float(entry.size))
            item.setData(5, SORT_ROLE, float(entry.mtime_ns))
            self._items_by_path[os.path.normcase(entry.path)] = item

        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        self._updating = False
        self._apply_visibility()
        self._thumb_timer.start()

    @staticmethod
    def _timestamp(mtime_ns: int) -> str:
        if not mtime_ns:
            return ""
        return datetime.fromtimestamp(mtime_ns / 1_000_000_000).strftime("%d-%m-%Y %H:%M")

    # ------------------------------------------------------------------ filters

    def _on_threshold_changed(self, _value: int) -> None:
        self._apply_visibility()

    def _entry_for(self, item: QTreeWidgetItem) -> SharpnessEntry | None:
        if self.result is None:
            return None
        path = os.path.normcase(str(item.data(0, PATH_ROLE) or ""))
        for entry in self.result.entries:
            if os.path.normcase(entry.path) == path:
                return entry
        return None

    def _apply_visibility(self) -> None:
        """Werk oordeelteksten bij en verberg wat buiten de selectie valt."""
        threshold = self.threshold
        self.threshold_label.setText(f"{threshold:.0f}")
        entries = {os.path.normcase(e.path): e for e in (self.result.entries if self.result else [])}

        self._updating = True
        visible = 0
        for key, item in self._items_by_path.items():
            entry = entries.get(key)
            if entry is None:
                item.setHidden(True)
                continue
            item.setText(2, entry.label(threshold))
            if entry.low_detail:
                show = self.low_detail_check.isChecked()
            elif self.only_blurry_check.isChecked():
                show = entry.is_blurry(threshold)
            else:
                show = True
            item.setHidden(not show)
            visible += int(show)
        self._updating = False

        self._visible_count = visible
        self._update_counts()

    def _update_counts(self) -> None:
        total = len(self.result.entries) if self.result else 0
        if not total:
            self.count_label.setText("")
            self.summary_label.setText("")
            self.delete_button.setEnabled(False)
            return
        blurry = len(self.result.blurry(self.threshold)) if self.result else 0
        low = len(self.result.low_detail()) if self.result else 0
        self.count_label.setText(
            f"{blurry} onscherp van {total} foto's"
            + (f" | {low} met weinig detail" if low else "")
        )
        checked = self.checked_paths()
        self.summary_label.setText(f"{len(checked)} aangevinkt" if checked else "")
        self.delete_button.setEnabled(bool(checked))

    # ------------------------------------------------------------------ vinkjes

    def checked_paths(self) -> list[str]:
        paths = []
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.checkState(0) == Qt.CheckState.Checked:
                path = item.data(0, PATH_ROLE)
                if path:
                    paths.append(str(path))
        return paths

    def _on_item_changed(self, _item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        self._update_counts()

    def _check_visible(self) -> None:
        self._updating = True
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if not item.isHidden():
                item.setCheckState(0, Qt.CheckState.Checked)
        self._updating = False
        self._update_counts()

    def _clear_checks(self) -> None:
        self._updating = True
        for index in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(index).setCheckState(0, Qt.CheckState.Unchecked)
        self._updating = False
        self._update_counts()

    # ------------------------------------------------------------------ acties

    def remove_paths(self, paths: list[str]) -> None:
        """Haal verwijderde foto's uit de lijst."""
        removed = {os.path.normcase(p) for p in paths}
        if not removed or self.result is None:
            return
        self.result.entries = [
            e for e in self.result.entries if os.path.normcase(e.path) not in removed
        ]
        for index in reversed(range(self.tree.topLevelItemCount())):
            item = self.tree.topLevelItem(index)
            key = os.path.normcase(str(item.data(0, PATH_ROLE) or ""))
            if key in removed:
                self.tree.takeTopLevelItem(index)
                self._items_by_path.pop(key, None)
        if self.preview.path and os.path.normcase(self.preview.path) in removed:
            self.preview.show_file(None)
        self._apply_visibility()

    def refresh(self) -> None:
        """Haal foto's weg die niet meer op schijf staan."""
        if self.result is None or not self.result.entries:
            self.status_message.emit("Er is nog geen scherptemeting om te vernieuwen.")
            return
        gone = [e.path for e in self.result.entries if not os.path.exists(e.path)]
        if not gone:
            self.status_message.emit("Lijst vernieuwd, alle foto's staan er nog.")
            return
        self.remove_paths(gone)
        self.status_message.emit(
            f"Lijst vernieuwd: {len(gone)} foto('s) niet meer aanwezig. "
            f"Over: {len(self.result.entries)} gemeten foto's."
        )

    def _export_csv(self) -> None:
        if self.result is None or not self.result.entries:
            QMessageBox.information(self, "Niets te exporteren", "Voer eerst een meting uit.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporteren naar CSV", "onscherpe-fotos.csv", "CSV-bestand (*.csv)"
        )
        if not path:
            return
        try:
            export_sharpness_csv(self.result.sorted_entries(), path, self.threshold)
        except OSError as exc:
            QMessageBox.warning(self, "Export mislukt", str(exc))
            return
        self.status_message.emit(f"Geexporteerd: {path}")

    def _show_context_menu(self, position: QPoint) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        path = str(item.data(0, PATH_ROLE) or "")
        if not path:
            return
        menu = QMenu(self)
        menu.addAction("Map openen in Verkenner", lambda: self.preview.reveal_requested.emit(path))
        menu.addAction("Bestand openen", lambda: self.preview.open_requested.emit(path))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    # ------------------------------------------------------------------ voorbeeld

    def _on_selection_changed(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            self.preview.show_file(None)
            return
        item = items[0]
        path = str(item.data(0, PATH_ROLE) or "")
        entry = self._entry_for(item)
        if not path or entry is None:
            self.preview.show_file(None)
            return
        info = (
            f"<b>{entry.name}</b><br>{entry.folder}<br><br>"
            f"Scherpte: {entry.score:.0f} ({entry.label(self.threshold)})<br>"
            f"Contrast: {entry.detail:.0f}<br>"
            f"Afmetingen: {entry.width} x {entry.height}<br>"
            f"Grootte: {format_bytes(entry.size)}"
        )
        self.preview.show_file(path, info)

    # ------------------------------------------------------------------ miniaturen

    def _request_visible_thumbnails(self) -> None:
        viewport = self.tree.viewport()
        step = max(16, self.tree.sizeHintForRow(0) or 24)
        y = 0
        while y < viewport.height():
            item = self.tree.itemAt(QPoint(8, y))
            y += step
            if item is None:
                continue
            path = str(item.data(0, PATH_ROLE) or "")
            key = os.path.normcase(path)
            if not path or key in self._no_thumbnail:
                continue
            pixmap = self._pixmaps.get(key)
            if pixmap is not None:
                item.setIcon(0, QIcon(pixmap))
                continue
            self.thumbnails.request(path, THUMBNAIL_SIZE)

    def _on_thumbnail_ready(self, path: str, size: int, data: object) -> None:
        if size != THUMBNAIL_SIZE:
            return
        key = os.path.normcase(path)
        item = self._items_by_path.get(key)
        if item is None:
            return
        pixmap = QPixmap()
        if isinstance(data, (bytes, bytearray)) and data:
            pixmap.loadFromData(bytes(data), "PNG")
        if pixmap.isNull():
            self._no_thumbnail.add(key)
            return
        self._pixmaps[key] = pixmap
        item.setIcon(0, QIcon(pixmap))
