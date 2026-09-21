"""Hoofdvenster van Duplicate Media Finder."""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __app_name__, __version__
from ..actions import ActionError, open_path, plan_deletion, reveal_in_file_manager
from ..cache import HashCache
from ..config import ScanConfig, cache_path
from ..exporter import export_csv, export_json, load_json
from ..scanner import DuplicateGroup, ScanProgress, ScanResult, format_bytes
from ..thumbnails import clear_thumbnail_cache
from .settings_dialog import SettingsDialog
from .workers import DeleteWorker, ScanWorker, ThumbnailService

PATH_ROLE = Qt.ItemDataRole.UserRole
GROUP_ROLE = Qt.ItemDataRole.UserRole + 1
SIZE_ROLE = Qt.ItemDataRole.UserRole + 2
DIGEST_ROLE = Qt.ItemDataRole.UserRole + 3

THUMBNAIL_SIZE = 64
PREVIEW_SIZE = 320


def format_timestamp(mtime_ns: int) -> str:
    if not mtime_ns:
        return ""
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000).strftime("%d-%m-%Y %H:%M")


class MainWindow(QMainWindow):
    """Scannen, bekijken en opruimen in een venster."""

    def __init__(self) -> None:
        super().__init__()
        self.config = ScanConfig.load()
        self.result: ScanResult | None = None
        self.scan_worker: ScanWorker | None = None
        self.delete_worker: DeleteWorker | None = None
        self.thumbnails = ThumbnailService(max_threads=4, parent=self)
        self.thumbnails.ready.connect(self._on_thumbnail_ready)
        self._pixmaps: dict[str, QPixmap] = {}
        self._no_preview: set[str] = set()
        self._items_by_path: dict[str, QTreeWidgetItem] = {}
        self._updating_checks = False
        self._preview_path: str | None = None

        self.setWindowTitle(f"{__app_name__} {__version__}")
        self.resize(1280, 820)
        self._build_ui()
        self._build_menu()
        self._load_roots_into_list()
        self._update_selection_summary()

        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(120)
        self._thumb_timer.timeout.connect(self._request_visible_thumbnails)

    # ------------------------------------------------------------------ opbouw

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        layout.addWidget(self._build_folders_group())
        layout.addLayout(self._build_scan_row())
        layout.addWidget(self._build_results_splitter(), stretch=1)
        layout.addLayout(self._build_action_row())

        self.setCentralWidget(central)
        self.statusBar().showMessage("Klaar. Kies een map en start de scan.")

    def _build_folders_group(self) -> QWidget:
        group = QGroupBox("1. Mappen om te scannen")
        outer = QHBoxLayout(group)

        self.roots_list = QListWidget()
        self.roots_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.roots_list.setMaximumHeight(90)
        outer.addWidget(self.roots_list, stretch=1)

        buttons = QVBoxLayout()
        add_button = QPushButton("Map toevoegen...")
        add_button.clicked.connect(self._add_root)
        remove_button = QPushButton("Verwijderen")
        remove_button.clicked.connect(self._remove_root)
        settings_button = QPushButton("Filters en instellingen...")
        settings_button.clicked.connect(self._open_settings)
        for button in (add_button, remove_button, settings_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        outer.addLayout(buttons)
        return group

    def _build_scan_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.scan_button = QPushButton("2. Scan starten")
        self.scan_button.setDefault(True)
        self.scan_button.clicked.connect(self._start_scan)
        self.cancel_button = QPushButton("Stoppen")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_scan)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("")

        self.phase_label = QLabel("")
        self.phase_label.setMinimumWidth(260)

        row.addWidget(self.scan_button)
        row.addWidget(self.cancel_button)
        row.addWidget(self.progress_bar, stretch=1)
        row.addWidget(self.phase_label)
        return row

    def _build_results_splitter(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        filter_row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter op bestandsnaam of map...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.thumbs_check = QCheckBox("Miniaturen in lijst")
        self.thumbs_check.setChecked(True)
        self.thumbs_check.toggled.connect(self._on_thumbs_toggled)
        filter_row.addWidget(self.filter_edit, stretch=1)
        filter_row.addWidget(self.thumbs_check)
        left_layout.addLayout(filter_row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Bestand", "Grootte", "Gewijzigd", "Map"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setIconSize(QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree.itemDoubleClicked.connect(lambda item, _col: self._reveal_item(item))
        self.tree.itemExpanded.connect(lambda _item: self._thumb_timer.start())
        self.tree.verticalScrollBar().valueChanged.connect(lambda _v: self._thumb_timer.start())
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(0, 420)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 130)
        left_layout.addWidget(self.tree, stretch=1)

        splitter.addWidget(left)
        splitter.addWidget(self._build_preview_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 340])
        return splitter

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)

        title = QLabel("Voorbeeld")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        self.preview_label = QLabel("Selecteer een bestand")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(PREVIEW_SIZE)
        self.preview_label.setFrameShape(QFrame.Shape.StyledPanel)
        self.preview_label.setStyleSheet("color: gray;")
        layout.addWidget(self.preview_label)

        self.preview_info = QLabel("")
        self.preview_info.setWordWrap(True)
        self.preview_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.preview_info)

        button_row = QHBoxLayout()
        open_file_button = QPushButton("Bestand openen")
        open_file_button.clicked.connect(self._open_preview_file)
        open_folder_button = QPushButton("Map openen")
        open_folder_button.clicked.connect(self._reveal_preview_file)
        button_row.addWidget(open_file_button)
        button_row.addWidget(open_folder_button)
        layout.addLayout(button_row)

        layout.addStretch(1)
        return panel

    def _build_action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self.select_duplicates_button = QPushButton("Duplicaten aanvinken (oudste blijft staan)")
        self.select_duplicates_button.clicked.connect(self._select_all_duplicates)
        self.clear_selection_button = QPushButton("Vinkjes wissen")
        self.clear_selection_button.clicked.connect(self._clear_checks)

        self.delete_button = QPushButton("Aangevinkte bestanden naar prullenbak")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._delete_checked)

        self.summary_label = QLabel("")

        row.addWidget(self.select_duplicates_button)
        row.addWidget(self.clear_selection_button)
        row.addStretch(1)
        row.addWidget(self.summary_label)
        row.addWidget(self.delete_button)
        return row

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&Bestand")
        self._add_action(file_menu, "Map toevoegen...", self._add_root, "Ctrl+O")
        file_menu.addSeparator()
        self._add_action(file_menu, "Scanresultaat opslaan (JSON)...", self._save_scan, "Ctrl+S")
        self._add_action(file_menu, "Scanresultaat laden (JSON)...", self._load_scan)
        self._add_action(file_menu, "Exporteren naar CSV...", self._export_csv)
        file_menu.addSeparator()
        self._add_action(file_menu, "Afsluiten", self.close, "Ctrl+Q")

        edit_menu = menu_bar.addMenu("Be&werken")
        self._add_action(edit_menu, "Duplicaten aanvinken", self._select_all_duplicates, "Ctrl+D")
        self._add_action(edit_menu, "Vinkjes wissen", self._clear_checks, "Ctrl+Shift+D")
        self._add_action(edit_menu, "Paden kopieren", self._copy_selected_paths, "Ctrl+C")
        edit_menu.addSeparator()
        self._add_action(
            edit_menu, "Aangevinkte naar prullenbak", self._delete_checked, "Ctrl+Del"
        )

        tools_menu = menu_bar.addMenu("&Extra")
        self._add_action(tools_menu, "Filters en instellingen...", self._open_settings)
        self._add_action(tools_menu, "Hashcache wissen", self._clear_hash_cache)
        self._add_action(tools_menu, "Miniatuurcache wissen", self._clear_thumb_cache)

        help_menu = menu_bar.addMenu("&Help")
        self._add_action(help_menu, "Over...", self._show_about)

    def _add_action(self, menu: QMenu, text: str, slot, shortcut: str | None = None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        menu.addAction(action)
        return action

    # ------------------------------------------------------------------ mappen

    def _load_roots_into_list(self) -> None:
        self.roots_list.clear()
        for root in self.config.roots:
            self.roots_list.addItem(QListWidgetItem(root))

    def _add_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Kies een map om te scannen")
        if not folder:
            return
        folder = os.path.normpath(folder)
        existing = {self.roots_list.item(i).text().lower() for i in range(self.roots_list.count())}
        if folder.lower() in existing:
            return
        self.roots_list.addItem(QListWidgetItem(folder))
        self._sync_roots_to_config()

    def _remove_root(self) -> None:
        for item in self.roots_list.selectedItems():
            self.roots_list.takeItem(self.roots_list.row(item))
        self._sync_roots_to_config()

    def _sync_roots_to_config(self) -> None:
        self.config.roots = [
            self.roots_list.item(i).text() for i in range(self.roots_list.count())
        ]
        self.config.save()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec():
            dialog.apply_to(self.config)
            self.config.save()
            self.statusBar().showMessage("Instellingen opgeslagen.", 4000)

    # ------------------------------------------------------------------ scannen

    def _start_scan(self) -> None:
        if self.scan_worker is not None:
            return
        self._sync_roots_to_config()
        problems = self.config.validate()
        if problems:
            QMessageBox.warning(self, "Scan kan niet starten", "\n".join(problems))
            return

        self._clear_results()
        self.scan_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("")
        self.phase_label.setText("Mappen doorzoeken...")

        self.scan_worker = ScanWorker(self.config, parent=self)
        self.scan_worker.progressed.connect(self._on_scan_progress)
        self.scan_worker.completed.connect(self._on_scan_completed)
        self.scan_worker.failed.connect(self._on_scan_failed)
        self.scan_worker.finished.connect(self._on_scan_thread_finished)
        self.scan_worker.start()

    def _cancel_scan(self) -> None:
        if self.scan_worker is not None:
            self.scan_worker.cancel()
            self.phase_label.setText("Bezig met stoppen...")
            self.cancel_button.setEnabled(False)

    @Slot(object)
    def _on_scan_progress(self, progress: ScanProgress) -> None:
        self.phase_label.setText(progress.message)
        if progress.total:
            self.progress_bar.setRange(0, progress.total)
            self.progress_bar.setValue(progress.current)
            self.progress_bar.setFormat(f"{progress.current} / {progress.total}")
        else:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("")

    @Slot(str)
    def _on_scan_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Scan mislukt", message)
        self.phase_label.setText("Scan mislukt")

    @Slot()
    def _on_scan_thread_finished(self) -> None:
        self.scan_worker = None
        self.scan_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("")

    @Slot(object)
    def _on_scan_completed(self, result: ScanResult) -> None:
        self.result = result
        self._populate_tree(result)
        stats = result.stats
        message = (
            f"{len(result.groups)} groepen duplicaten | "
            f"{result.duplicate_files} overbodige bestanden | "
            f"{format_bytes(result.reclaimable_bytes)} te winnen | "
            f"{stats.files_considered} bestanden bekeken in {stats.duration_seconds:.1f} s"
        )
        if result.cancelled:
            message = "Scan gestopt. " + message
        if result.errors:
            message += f" | {len(result.errors)} bestanden overgeslagen (geen toegang)"
        self.statusBar().showMessage(message)
        self.phase_label.setText("Klaar")
        if not result.groups and not result.cancelled:
            QMessageBox.information(
                self,
                "Geen duplicaten",
                "Er zijn geen byte-identieke mediabestanden gevonden in de opgegeven mappen.",
            )

    # ------------------------------------------------------------------ resultaten

    def _clear_results(self) -> None:
        self.thumbnails.clear_pending()
        self.tree.clear()
        self._items_by_path.clear()
        self._pixmaps.clear()
        self._no_preview.clear()
        self.result = None
        self._set_preview(None)
        self._update_selection_summary()

    def _populate_tree(self, result: ScanResult) -> None:
        self._updating_checks = True
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        self._items_by_path.clear()

        for index, group in enumerate(result.sorted_groups(), start=1):
            group_item = QTreeWidgetItem(self.tree)
            group_item.setText(
                0,
                f"Groep {index}  -  {group.count} identieke bestanden  "
                f"({format_bytes(group.wasted_bytes)} te winnen)",
            )
            group_item.setText(1, format_bytes(group.size))
            group_item.setText(3, f"SHA-256: {group.digest[:16]}...")
            group_item.setToolTip(3, f"SHA-256: {group.digest}")
            group_item.setData(0, GROUP_ROLE, index - 1)
            group_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
            group_item.setCheckState(0, Qt.CheckState.Unchecked)

            for position, entry in enumerate(group.files):
                child = QTreeWidgetItem(group_item)
                child.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                child.setCheckState(0, Qt.CheckState.Unchecked)
                label = entry.name
                if position == 0:
                    label += "   [oudste]"
                child.setText(0, label)
                child.setText(1, format_bytes(entry.size))
                child.setText(2, format_timestamp(entry.mtime_ns))
                child.setText(3, entry.folder)
                child.setToolTip(0, entry.path)
                child.setData(0, PATH_ROLE, entry.path)
                child.setData(0, GROUP_ROLE, index - 1)
                child.setData(0, SIZE_ROLE, entry.size)
                child.setData(0, DIGEST_ROLE, group.digest)
                if not os.path.exists(entry.path):
                    child.setText(0, label + "  (niet gevonden)")
                    child.setDisabled(True)
                self._items_by_path[os.path.normcase(entry.path)] = child

            group_item.setExpanded(len(result.groups) <= 50)

        self.tree.setUpdatesEnabled(True)
        self._updating_checks = False
        self._update_selection_summary()
        self._thumb_timer.start()

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            visible_children = 0
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                path = str(child.data(0, PATH_ROLE) or "")
                match = not needle or needle in path.lower()
                child.setHidden(not match)
                visible_children += int(match)
            group_item.setHidden(visible_children == 0)
        self._thumb_timer.start()

    # ------------------------------------------------------------------ vinkjes

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating_checks or column != 0:
            return
        if item.data(0, PATH_ROLE) is None:  # groepsregel
            self._updating_checks = True
            state = item.checkState(0)
            if state != Qt.CheckState.PartiallyChecked:
                for i in range(item.childCount()):
                    child = item.child(i)
                    if not child.isDisabled():
                        child.setCheckState(0, state)
            self._updating_checks = False
        self._update_selection_summary()

    def _checked_paths(self) -> list[str]:
        paths: list[str] = []
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    path = child.data(0, PATH_ROLE)
                    if path:
                        paths.append(str(path))
        return paths

    def _select_all_duplicates(self) -> None:
        """Vink per groep alles aan behalve het oudste bestand."""
        if self.tree.topLevelItemCount() == 0:
            return
        self._updating_checks = True
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                if child.isDisabled():
                    continue
                child.setCheckState(
                    0, Qt.CheckState.Unchecked if j == 0 else Qt.CheckState.Checked
                )
            group_item.setCheckState(
                0,
                Qt.CheckState.PartiallyChecked
                if group_item.childCount() > 1
                else Qt.CheckState.Unchecked,
            )
        self._updating_checks = False
        self._update_selection_summary()

    def _clear_checks(self) -> None:
        self._updating_checks = True
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            group_item.setCheckState(0, Qt.CheckState.Unchecked)
            for j in range(group_item.childCount()):
                group_item.child(j).setCheckState(0, Qt.CheckState.Unchecked)
        self._updating_checks = False
        self._update_selection_summary()

    def _keep_only(self, item: QTreeWidgetItem) -> None:
        """Vink in deze groep alles aan behalve het gekozen bestand."""
        parent = item.parent()
        if parent is None:
            return
        self._updating_checks = True
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.isDisabled():
                continue
            child.setCheckState(
                0, Qt.CheckState.Unchecked if child is item else Qt.CheckState.Checked
            )
        self._updating_checks = False
        self._update_selection_summary()

    def _update_selection_summary(self) -> None:
        paths = self._checked_paths()
        total = 0
        for path in paths:
            item = self._items_by_path.get(os.path.normcase(path))
            if item is not None:
                total += int(item.data(0, SIZE_ROLE) or 0)
        self.summary_label.setText(
            f"{len(paths)} aangevinkt  ({format_bytes(total)})" if paths else ""
        )
        self.delete_button.setEnabled(bool(paths))

    # ------------------------------------------------------------------ miniaturen

    def _on_thumbs_toggled(self, enabled: bool) -> None:
        if enabled:
            self._thumb_timer.start()
        else:
            for item in self._items_by_path.values():
                item.setIcon(0, QIcon())

    def _request_visible_thumbnails(self) -> None:
        if not self.thumbs_check.isChecked():
            return
        viewport = self.tree.viewport()
        height = viewport.height()
        step = max(16, self.tree.sizeHintForRow(0) or 24)
        seen: set[str] = set()
        y = 0
        while y < height:
            item = self.tree.itemAt(QPoint(8, y))
            y += step
            if item is None:
                continue
            path = item.data(0, PATH_ROLE)
            if not path:
                continue
            path = str(path)
            if path in seen:
                continue
            seen.add(path)
            if path in self._pixmaps:
                item.setIcon(0, QIcon(self._pixmaps[path]))
                continue
            if os.path.normcase(path) in self._no_preview:
                continue
            self.thumbnails.request(path, THUMBNAIL_SIZE)

    @Slot(str, object)
    def _on_thumbnail_ready(self, path: str, data: object) -> None:
        pixmap = QPixmap()
        if isinstance(data, (bytes, bytearray)) and data:
            pixmap.loadFromData(bytes(data), "PNG")
        if pixmap.isNull():
            # Geen voorbeeld mogelijk (RAW zonder decoder, video zonder ffmpeg, ...).
            self._no_preview.add(os.path.normcase(path))
            if self._preview_path and os.path.normcase(self._preview_path) == os.path.normcase(
                path
            ):
                self.preview_label.setPixmap(QPixmap())
                self.preview_label.setText("Geen voorbeeld beschikbaar")
            return
        self._pixmaps[path] = pixmap
        item = self._items_by_path.get(os.path.normcase(path))
        if item is not None and self.thumbs_check.isChecked():
            item.setIcon(0, QIcon(pixmap))
        if self._preview_path and os.path.normcase(self._preview_path) == os.path.normcase(path):
            self._show_preview_pixmap(pixmap)

    # ------------------------------------------------------------------ voorbeeld

    def _on_tree_selection_changed(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            self._set_preview(None)
            return
        path = items[0].data(0, PATH_ROLE)
        self._set_preview(str(path) if path else None)

    def _set_preview(self, path: str | None) -> None:
        self._preview_path = path
        if path is None:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Selecteer een bestand")
            self.preview_info.setText("")
            return

        item = self._items_by_path.get(os.path.normcase(path))
        size = int(item.data(0, SIZE_ROLE) or 0) if item else 0
        modified = item.text(2) if item else ""
        digest = str(item.data(0, DIGEST_ROLE) or "") if item else ""

        self.preview_info.setText(
            f"<b>{os.path.basename(path)}</b><br>"
            f"{os.path.dirname(path)}<br><br>"
            f"Grootte: {format_bytes(size)}<br>"
            f"Gewijzigd: {modified}<br>"
            f"SHA-256: <span style='font-family:monospace'>{digest[:32]}...</span>"
        )

        pixmap = self._pixmaps.get(path)
        if pixmap is not None and not pixmap.isNull():
            self._show_preview_pixmap(pixmap)
        elif os.path.normcase(path) in self._no_preview:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Geen voorbeeld beschikbaar")
        else:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Voorbeeld laden...")
            self.thumbnails.request(path, PREVIEW_SIZE)

    def _show_preview_pixmap(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(
            self.preview_label.width() - 8,
            PREVIEW_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setText("")
        self.preview_label.setPixmap(scaled)

    # ------------------------------------------------------------------ acties

    def _show_context_menu(self, position: QPoint) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        menu = QMenu(self)
        if item.data(0, PATH_ROLE):
            menu.addAction("Map openen in Verkenner", lambda: self._reveal_item(item))
            menu.addAction("Bestand openen", lambda: self._open_item(item))
            menu.addSeparator()
            menu.addAction("Alleen dit bestand behouden", lambda: self._keep_only(item))
            menu.addAction("Pad kopieren", self._copy_selected_paths)
        else:
            menu.addAction("Groep uitklappen", lambda: item.setExpanded(True))
            menu.addAction("Groep inklappen", lambda: item.setExpanded(False))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _reveal_item(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, PATH_ROLE)
        if not path:
            return
        try:
            reveal_in_file_manager(str(path))
        except ActionError as exc:
            QMessageBox.warning(self, "Openen mislukt", str(exc))

    def _open_item(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, PATH_ROLE)
        if not path:
            return
        try:
            open_path(str(path))
        except ActionError as exc:
            QMessageBox.warning(self, "Openen mislukt", str(exc))

    def _open_preview_file(self) -> None:
        if not self._preview_path:
            return
        try:
            open_path(self._preview_path)
        except ActionError as exc:
            QMessageBox.warning(self, "Openen mislukt", str(exc))

    def _reveal_preview_file(self) -> None:
        if not self._preview_path:
            return
        try:
            reveal_in_file_manager(self._preview_path)
        except ActionError as exc:
            QMessageBox.warning(self, "Openen mislukt", str(exc))

    def _copy_selected_paths(self) -> None:
        paths = [
            str(item.data(0, PATH_ROLE))
            for item in self.tree.selectedItems()
            if item.data(0, PATH_ROLE)
        ]
        if not paths:
            return
        QApplication.clipboard().setText("\n".join(paths))
        self.statusBar().showMessage(f"{len(paths)} pad(en) gekopieerd.", 3000)

    # ------------------------------------------------------------------ verwijderen

    def _delete_checked(self) -> None:
        if self.result is None or self.delete_worker is not None:
            return
        checked = self._checked_paths()
        if not checked:
            return

        plan = plan_deletion(self.result.groups, checked)
        if plan.is_empty:
            QMessageBox.information(
                self,
                "Niets te verwijderen",
                "Van elke groep blijft minstens een bestand staan. "
                "Er is daardoor niets geselecteerd om te verwijderen.",
            )
            return

        details = [
            f"{len(plan.to_delete)} bestanden gaan naar de prullenbak.",
            f"Dat maakt {format_bytes(plan.total_bytes)} vrij.",
        ]
        if plan.protected:
            details.append(
                f"\nIn {len(plan.protected)} groep(en) was alles aangevinkt. "
                "Daar blijft het oudste bestand bewust staan."
            )
        if plan.missing:
            details.append(f"\n{len(plan.missing)} bestand(en) bestaan niet meer.")

        confirm = QMessageBox(self)
        confirm.setWindowTitle("Naar prullenbak verplaatsen")
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setText("\n".join(details))
        confirm.setInformativeText(
            "De bestanden gaan naar de systeemprullenbak en kunnen daar hersteld worden."
        )
        confirm.setDetailedText("\n".join(plan.to_delete))
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return

        self.delete_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.progress_bar.setRange(0, len(plan.to_delete))
        self.delete_worker = DeleteWorker(plan.to_delete, parent=self)
        self.delete_worker.progressed.connect(self._on_delete_progress)
        self.delete_worker.completed.connect(self._on_delete_completed)
        self.delete_worker.finished.connect(self._on_delete_thread_finished)
        self.delete_worker.start()

    @Slot(int, int, str)
    def _on_delete_progress(self, done: int, total: int, path: str) -> None:
        self.progress_bar.setValue(done)
        self.progress_bar.setFormat(f"{done} / {total}")
        self.phase_label.setText(f"Verwijderen: {os.path.basename(path)}")

    @Slot(object)
    def _on_delete_completed(self, outcome) -> None:
        self._remove_paths_from_view(outcome.deleted)
        with HashCache(cache_path(), enabled=self.config.use_cache) as cache:
            for path in outcome.deleted:
                cache.forget(path)
        message = (
            f"{len(outcome.deleted)} bestanden naar de prullenbak verplaatst "
            f"({format_bytes(outcome.total_bytes)} vrijgemaakt)."
        )
        self.statusBar().showMessage(message)
        if outcome.failed:
            preview = "\n".join(f"{p}: {m}" for p, m in outcome.failed[:15])
            QMessageBox.warning(
                self,
                "Niet alles is gelukt",
                f"{len(outcome.failed)} bestand(en) konden niet verwijderd worden:\n\n{preview}",
            )

    @Slot()
    def _on_delete_thread_finished(self) -> None:
        self.delete_worker = None
        self.scan_button.setEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("")
        self.phase_label.setText("Klaar")
        self._update_selection_summary()

    def _remove_paths_from_view(self, paths: list[str]) -> None:
        """Haal verwijderde bestanden uit de boom en uit het resultaat."""
        removed = {os.path.normcase(p) for p in paths}
        self._updating_checks = True
        for i in reversed(range(self.tree.topLevelItemCount())):
            group_item = self.tree.topLevelItem(i)
            for j in reversed(range(group_item.childCount())):
                child = group_item.child(j)
                path = str(child.data(0, PATH_ROLE) or "")
                if os.path.normcase(path) in removed:
                    group_item.removeChild(child)
                    self._items_by_path.pop(os.path.normcase(path), None)
            if group_item.childCount() < 2:
                self.tree.takeTopLevelItem(i)
        self._updating_checks = False

        if self.result is not None:
            new_groups: list[DuplicateGroup] = []
            for group in self.result.groups:
                files = [f for f in group.files if os.path.normcase(f.path) not in removed]
                if len(files) > 1:
                    group.files = files
                    new_groups.append(group)
            self.result.groups = new_groups
        self._set_preview(None)
        self._update_selection_summary()

    # ------------------------------------------------------------------ opslaan

    def _save_scan(self) -> None:
        if self.result is None or not self.result.groups:
            QMessageBox.information(self, "Niets op te slaan", "Voer eerst een scan uit.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Scanresultaat opslaan", "duplicaten.json", "JSON-bestand (*.json)"
        )
        if not path:
            return
        try:
            export_json(self.result, path)
        except OSError as exc:
            QMessageBox.warning(self, "Opslaan mislukt", str(exc))
            return
        self.statusBar().showMessage(f"Opgeslagen: {path}", 5000)

    def _load_scan(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Scanresultaat laden", "", "JSON-bestand (*.json)"
        )
        if not path:
            return
        try:
            result = load_json(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Laden mislukt", str(exc))
            return
        self.result = result
        self._populate_tree(result)
        missing = sum(
            1 for group in result.groups for f in group.files if not os.path.exists(f.path)
        )
        message = (
            f"Scan geladen: {len(result.groups)} groepen, "
            f"{format_bytes(result.reclaimable_bytes)} te winnen"
        )
        if missing:
            message += f" | {missing} bestand(en) bestaan niet meer"
        self.statusBar().showMessage(message)

    def _export_csv(self) -> None:
        if self.result is None or not self.result.groups:
            QMessageBox.information(self, "Niets te exporteren", "Voer eerst een scan uit.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporteren naar CSV", "duplicaten.csv", "CSV-bestand (*.csv)"
        )
        if not path:
            return
        try:
            export_csv(self.result, path)
        except OSError as exc:
            QMessageBox.warning(self, "Export mislukt", str(exc))
            return
        self.statusBar().showMessage(f"Geexporteerd: {path}", 5000)

    # ------------------------------------------------------------------ extra

    def _clear_hash_cache(self) -> None:
        with HashCache(cache_path(), enabled=True) as cache:
            cache.clear()
        self.statusBar().showMessage("Hashcache gewist.", 4000)

    def _clear_thumb_cache(self) -> None:
        clear_thumbnail_cache()
        self._pixmaps.clear()
        self._no_preview.clear()
        self.statusBar().showMessage("Miniatuurcache gewist.", 4000)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            f"Over {__app_name__}",
            f"<b>{__app_name__} {__version__}</b><br><br>"
            "Vindt byte-identieke foto- en videobestanden via SHA-256.<br>"
            "Alleen bestanden met exact dezelfde inhoud worden als duplicaat "
            "gemeld; er zijn geen valse positieven.<br><br>"
            "Verwijderde bestanden gaan naar de systeemprullenbak.",
        )

    # ------------------------------------------------------------------ afsluiten

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt-naamgeving
        if self.scan_worker is not None:
            self.scan_worker.cancel()
            self.scan_worker.wait(3000)
        if self.delete_worker is not None:
            self.delete_worker.cancel()
            self.delete_worker.wait(3000)
        self.thumbnails.shutdown()
        self._sync_roots_to_config()
        super().closeEvent(event)
