"""Tabblad met onscherpe foto's."""

from __future__ import annotations

import math
import os
from bisect import bisect_left
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
    """Sorteert kolommen op hun onderliggende getal in plaats van op tekst.

    De sorteerkolom staat op de klasse: bij tienduizenden rijen wordt ``__lt__``
    honderdduizenden keren aangeroepen, en dan telt elke Qt-aanroep mee.
    """

    sort_column = 1

    def __lt__(self, other: QTreeWidgetItem) -> bool:  # noqa: D105 - Qt-naamgeving
        column = _SortableItem.sort_column
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
        self._entries_by_path: dict[str, SharpnessEntry] = {}
        #: Gesorteerde scores van foto's met genoeg detail; telt met bisect in
        #: plaats van telkens de hele lijst door te lopen.
        self._scores_with_detail: list[float] = []
        self._low_detail_total = 0
        #: Aangevinkte foto's, bijgehouden terwijl je klikt (normcase -> echt pad).
        self._checked: dict[str, str] = {}
        self._pixmaps: dict[str, QPixmap] = {}
        self._no_thumbnail: set[str] = set()
        self._updating = False

        self._build_ui()

        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(120)
        self._thumb_timer.timeout.connect(self._request_visible_thumbnails)

        # Tijdens het slepen van de schuifregelaar wordt de lijst pas bijgewerkt
        # als je even stilhoudt; anders kost elke tussenstap een rondgang.
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(120)
        self._filter_timer.timeout.connect(self._apply_visibility)

        # Hetzelfde voor het voorbeeld: snel door de lijst pijlen zet anders een
        # rij zware decodeeropdrachten in de wachtrij.
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(160)
        self._preview_timer.timeout.connect(self._load_preview)

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
        # Alle rijen even hoog: anders meet Qt bij elke wijziging de rijhoogte
        # opnieuw, en dat is bij duizenden foto's goed merkbaar.
        self.tree.setUniformRowHeights(True)
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
        self.tree.header().sortIndicatorChanged.connect(self._on_sort_changed)
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
        self._entries_by_path.clear()
        self._scores_with_detail.clear()
        self._low_detail_total = 0
        self._checked.clear()
        self._pixmaps.clear()
        self._no_thumbnail.clear()
        self.tree.clear()
        self.preview.show_file(None)
        self._update_counts()

    def _populate(self) -> None:
        self._updating = True
        self.tree.setSortingEnabled(False)
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        self._items_by_path.clear()
        self._checked.clear()
        self._index_entries()

        items: list[QTreeWidgetItem] = []
        threshold = self.threshold
        for entry in (self.result.sorted_entries() if self.result else []):
            item = _SortableItem()
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setText(0, entry.name)
            item.setToolTip(0, entry.path)
            item.setText(1, f"{entry.score:.0f}")
            item.setText(2, entry.label(threshold))
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
            items.append(item)
            self._items_by_path[os.path.normcase(entry.path)] = item

        # In een keer toevoegen is bij duizenden rijen veel sneller dan per stuk.
        self.tree.addTopLevelItems(items)
        _SortableItem.sort_column = 1
        self.tree.header().setSortIndicator(1, Qt.SortOrder.AscendingOrder)
        self.tree.setSortingEnabled(True)
        self.tree.setUpdatesEnabled(True)
        self._updating = False
        self._apply_visibility()
        self._thumb_timer.start()

    def _index_entries(self) -> None:
        """Bouw de opzoektabellen waarmee filteren en tellen snel blijven."""
        entries = self.result.entries if self.result else []
        self._entries_by_path = {os.path.normcase(e.path): e for e in entries}
        self._scores_with_detail = sorted(e.score for e in entries if not e.low_detail)
        self._low_detail_total = len(entries) - len(self._scores_with_detail)

    @staticmethod
    def _timestamp(mtime_ns: int) -> str:
        if not mtime_ns:
            return ""
        return datetime.fromtimestamp(mtime_ns / 1_000_000_000).strftime("%d-%m-%Y %H:%M")

    # ------------------------------------------------------------------ filters

    def _on_sort_changed(self, column: int, _order: Qt.SortOrder) -> None:
        _SortableItem.sort_column = column

    def _on_threshold_changed(self, _value: int) -> None:
        # Het getal volgt de regelaar meteen; de lijst volgt zodra je stilhoudt.
        self.threshold_label.setText(f"{self.threshold:.0f}")
        self._filter_timer.start()

    def _entry_for(self, item: QTreeWidgetItem) -> SharpnessEntry | None:
        key = os.path.normcase(str(item.data(0, PATH_ROLE) or ""))
        return self._entries_by_path.get(key)

    def _apply_visibility(self) -> None:
        """Werk oordeelteksten bij en verberg wat buiten de selectie valt."""
        self._filter_timer.stop()
        threshold = self.threshold
        self.threshold_label.setText(f"{threshold:.0f}")
        only_blurry = self.only_blurry_check.isChecked()
        show_low_detail = self.low_detail_check.isChecked()

        self._updating = True
        self.tree.setUpdatesEnabled(False)
        try:
            visible = 0
            for key, item in self._items_by_path.items():
                entry = self._entries_by_path.get(key)
                if entry is None:
                    item.setHidden(True)
                    continue
                label = entry.label(threshold)
                if item.text(2) != label:
                    item.setText(2, label)
                if entry.low_detail:
                    show = show_low_detail
                elif only_blurry:
                    show = entry.score < threshold
                else:
                    show = True
                hidden = not show
                if item.isHidden() != hidden:
                    item.setHidden(hidden)
                visible += int(show)
        finally:
            self.tree.setUpdatesEnabled(True)
            self._updating = False

        self._visible_count = visible
        self._update_counts()

    def _blurry_count(self, threshold: float) -> int:
        """Aantal onscherpe foto's, opgezocht in de gesorteerde scores."""
        return bisect_left(self._scores_with_detail, threshold)

    def _update_counts(self) -> None:
        total = len(self._entries_by_path)
        if not total:
            self.count_label.setText("")
            self._update_summary()
            return
        blurry = self._blurry_count(self.threshold)
        self.count_label.setText(
            f"{blurry} onscherp van {total} foto's"
            + (f" | {self._low_detail_total} met weinig detail" if self._low_detail_total else "")
        )
        self._update_summary()

    def _update_summary(self) -> None:
        """Alleen het aangevinkt-deel bijwerken; dit loopt bij elke klik."""
        count = len(self._checked)
        self.summary_label.setText(f"{count} aangevinkt" if count else "")
        self.delete_button.setEnabled(bool(count))

    # ------------------------------------------------------------------ vinkjes

    def checked_paths(self) -> list[str]:
        return list(self._checked.values())

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        path = str(item.data(0, PATH_ROLE) or "")
        if not path:
            return
        key = os.path.normcase(path)
        if item.checkState(0) == Qt.CheckState.Checked:
            self._checked[key] = path
        else:
            self._checked.pop(key, None)
        self._update_summary()

    def _check_visible(self) -> None:
        # Bij duizenden rijen is niet het vinkje duur maar het signaal per rij;
        # we houden de administratie zelf bij en zetten de signalen uit.
        self._updating = True
        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
        try:
            for index in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(index)
                if item.isHidden():
                    continue
                item.setCheckState(0, Qt.CheckState.Checked)
                path = str(item.data(0, PATH_ROLE) or "")
                if path:
                    self._checked[os.path.normcase(path)] = path
        finally:
            self.tree.blockSignals(False)
            self.tree.setUpdatesEnabled(True)
            self._updating = False
        self._update_summary()

    def _clear_checks(self) -> None:
        if not self._checked:
            return
        self._updating = True
        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
        try:
            for key in list(self._checked):
                item = self._items_by_path.get(key)
                if item is not None:
                    item.setCheckState(0, Qt.CheckState.Unchecked)
        finally:
            self.tree.blockSignals(False)
            self.tree.setUpdatesEnabled(True)
            self._updating = False
        self._checked.clear()
        self._update_summary()

    # ------------------------------------------------------------------ acties

    def remove_paths(self, paths: list[str]) -> None:
        """Haal verwijderde foto's uit de lijst."""
        removed = {os.path.normcase(p) for p in paths}
        if not removed or self.result is None:
            return
        self.result.entries = [
            e for e in self.result.entries if os.path.normcase(e.path) not in removed
        ]
        self.tree.setUpdatesEnabled(False)
        self._updating = True
        try:
            for index in reversed(range(self.tree.topLevelItemCount())):
                item = self.tree.topLevelItem(index)
                key = os.path.normcase(str(item.data(0, PATH_ROLE) or ""))
                if key in removed:
                    self.tree.takeTopLevelItem(index)
                    self._items_by_path.pop(key, None)
                    self._checked.pop(key, None)
        finally:
            self._updating = False
            self.tree.setUpdatesEnabled(True)
        self._index_entries()
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
        # Pas laden als de selectie even stilstaat: snel door de lijst pijlen
        # zet anders tientallen foto's in de decodeerwachtrij.
        self._preview_timer.start()

    def _load_preview(self) -> None:
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
        # Wat nog in de wachtrij staat gaat over rijen die inmiddels uit beeld zijn.
        self.thumbnails.drop_queued()
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
