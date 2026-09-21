"""Dialoog met filters en scaninstellingen."""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..config import DEFAULT_EXCLUDED_NAMES, ScanConfig
from ..formats import parse_extension_list


class SettingsDialog(QDialog):
    """Bewerkt alles in :class:`ScanConfig` behalve de te scannen mappen."""

    def __init__(self, config: ScanConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Filters en instellingen")
        self.setMinimumWidth(560)
        self._config = config

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_types_group())
        layout.addWidget(self._build_size_group())
        layout.addWidget(self._build_exclusions_group())
        layout.addWidget(self._build_advanced_group())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self._restore_defaults
        )
        layout.addWidget(buttons)

        self._load(config)

    # -- opbouw ---------------------------------------------------------------

    def _build_types_group(self) -> QGroupBox:
        group = QGroupBox("Bestandstypes")
        form = QFormLayout(group)

        self.images_check = QCheckBox("Foto's (JPG, PNG, HEIC, TIFF, RAW, ...)")
        self.videos_check = QCheckBox("Video's (MP4, MOV, AVI, MKV, MTS, ...)")
        form.addRow(self.images_check)
        form.addRow(self.videos_check)

        self.extra_edit = QLineEdit()
        self.extra_edit.setPlaceholderText("bijv. psd, xmp, aae")
        form.addRow("Extra extensies:", self.extra_edit)

        self.only_edit = QLineEdit()
        self.only_edit.setPlaceholderText("leeg laten = alle geselecteerde types")
        form.addRow("Alleen deze extensies:", self.only_edit)

        hint = QLabel(
            "Scheid extensies met een komma. 'Alleen deze extensies' overschrijft "
            "de keuzes hierboven."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        form.addRow(hint)
        return group

    def _build_size_group(self) -> QGroupBox:
        group = QGroupBox("Bestandsgrootte")
        form = QFormLayout(group)

        self.min_size_spin = QSpinBox()
        self.min_size_spin.setRange(0, 1_000_000)
        self.min_size_spin.setSuffix(" KB")
        self.min_size_spin.setToolTip("Bestanden kleiner dan deze grootte worden overgeslagen.")
        form.addRow("Minimale grootte:", self.min_size_spin)

        self.max_size_spin = QSpinBox()
        self.max_size_spin.setRange(0, 1_000_000)
        self.max_size_spin.setSuffix(" MB")
        self.max_size_spin.setSpecialValueText("geen grens")
        form.addRow("Maximale grootte:", self.max_size_spin)
        return group

    def _build_exclusions_group(self) -> QGroupBox:
        group = QGroupBox("Uitsluitingen")
        layout = QVBoxLayout(group)

        form = QFormLayout()
        self.excluded_names_edit = QLineEdit()
        self.excluded_names_edit.setPlaceholderText("$RECYCLE.BIN, .git, @eaDir")
        self.excluded_names_edit.setToolTip(
            "Mapnamen die overal worden overgeslagen, gescheiden door komma's."
        )
        form.addRow("Mapnamen overslaan:", self.excluded_names_edit)
        layout.addLayout(form)

        layout.addWidget(QLabel("Specifieke mappen overslaan (inclusief submappen):"))
        self.excluded_paths_list = QListWidget()
        self.excluded_paths_list.setMaximumHeight(110)
        layout.addWidget(self.excluded_paths_list)

        buttons = QHBoxLayout()
        add_button = QPushButton("Map toevoegen...")
        add_button.clicked.connect(self._add_excluded_path)
        remove_button = QPushButton("Verwijderen")
        remove_button.clicked.connect(self._remove_excluded_path)
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.hidden_check = QCheckBox("Verborgen en systeembestanden overslaan")
        self.symlink_check = QCheckBox("Snelkoppelingen naar mappen volgen (junctions/symlinks)")
        layout.addWidget(self.hidden_check)
        layout.addWidget(self.symlink_check)
        return group

    def _build_advanced_group(self) -> QGroupBox:
        group = QGroupBox("Prestaties")
        form = QFormLayout(group)

        self.cache_check = QCheckBox("Hashcache gebruiken (tweede scan is veel sneller)")
        form.addRow(self.cache_check)

        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 32)
        self.threads_spin.setToolTip(
            "Aantal bestanden dat tegelijk gelezen wordt. Voor een externe USB-schijf "
            "is 4 tot 8 meestal het snelst."
        )
        form.addRow("Parallelle leesthreads:", self.threads_spin)
        return group

    # -- knoppen --------------------------------------------------------------

    def _add_excluded_path(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Map kiezen om over te slaan")
        if folder:
            self._append_excluded_path(os.path.normpath(folder))

    def _append_excluded_path(self, folder: str) -> None:
        existing = {
            self.excluded_paths_list.item(i).text().lower()
            for i in range(self.excluded_paths_list.count())
        }
        if folder.lower() in existing:
            return
        self.excluded_paths_list.addItem(QListWidgetItem(folder))

    def _remove_excluded_path(self) -> None:
        for item in self.excluded_paths_list.selectedItems():
            self.excluded_paths_list.takeItem(self.excluded_paths_list.row(item))

    def _restore_defaults(self) -> None:
        defaults = ScanConfig(roots=list(self._config.roots))
        self._load(defaults)

    # -- laden en opslaan -----------------------------------------------------

    def _load(self, config: ScanConfig) -> None:
        self.images_check.setChecked(config.include_images)
        self.videos_check.setChecked(config.include_videos)
        self.extra_edit.setText(", ".join(config.extra_extensions))
        self.only_edit.setText(", ".join(config.only_extensions))
        self.min_size_spin.setValue(max(0, config.min_size_bytes // 1024))
        self.max_size_spin.setValue(max(0, config.max_size_bytes // (1024 * 1024)))
        self.excluded_names_edit.setText(", ".join(config.excluded_names))
        self.excluded_paths_list.clear()
        for path in config.excluded_paths:
            self._append_excluded_path(path)
        self.hidden_check.setChecked(config.skip_hidden)
        self.symlink_check.setChecked(config.follow_symlinks)
        self.cache_check.setChecked(config.use_cache)
        self.threads_spin.setValue(config.worker_threads)

    def _on_accept(self) -> None:
        if not (
            self.images_check.isChecked()
            or self.videos_check.isChecked()
            or self.only_edit.text().strip()
            or self.extra_edit.text().strip()
        ):
            QMessageBox.warning(
                self,
                "Geen bestandstypes",
                "Kies minstens foto's of video's, of vul zelf extensies in.",
            )
            return
        self.accept()

    def apply_to(self, config: ScanConfig) -> ScanConfig:
        """Schrijf de ingevulde waarden terug naar een configuratie."""
        config.include_images = self.images_check.isChecked()
        config.include_videos = self.videos_check.isChecked()
        config.extra_extensions = sorted(parse_extension_list(self.extra_edit.text()))
        config.only_extensions = sorted(parse_extension_list(self.only_edit.text()))
        config.min_size_bytes = self.min_size_spin.value() * 1024
        config.max_size_bytes = self.max_size_spin.value() * 1024 * 1024
        names = [n.strip() for n in self.excluded_names_edit.text().split(",") if n.strip()]
        config.excluded_names = names or list(DEFAULT_EXCLUDED_NAMES)
        config.excluded_paths = [
            self.excluded_paths_list.item(i).text()
            for i in range(self.excluded_paths_list.count())
        ]
        config.skip_hidden = self.hidden_check.isChecked()
        config.follow_symlinks = self.symlink_check.isChecked()
        config.use_cache = self.cache_check.isChecked()
        config.worker_threads = self.threads_spin.value()
        return config
