"""Acties op gevonden bestanden: prullenbak, Verkenner, veiligheidscontroles."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .hashing import full_hash
from .scanner import DuplicateGroup


class ActionError(Exception):
    """Een bestandsactie is mislukt."""


# ---------------------------------------------------------------------------
# Verwijderplan
# ---------------------------------------------------------------------------


@dataclass
class DeletionPlan:
    """Wat er verwijderd wordt, en wat er bewust wordt tegengehouden."""

    to_delete: list[str] = field(default_factory=list)
    #: Groepen waarin *alles* was aangevinkt; daar houden we er een achter.
    protected: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    total_bytes: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.to_delete


def _normalise(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def plan_deletion(
    groups: Sequence[DuplicateGroup],
    selected_paths: Iterable[str],
    keep_at_least_one: bool = True,
) -> DeletionPlan:
    """Zet een selectie om in een veilig verwijderplan.

    Wanneer binnen een groep alle bestanden zijn aangevinkt, wordt het oudste
    bestand automatisch bewaard en in ``protected`` gemeld. Zo kan een
    onoplettende klik nooit het laatste exemplaar wissen.
    """
    selected = {_normalise(p) for p in selected_paths}
    plan = DeletionPlan()
    for group in groups:
        chosen = [f for f in group.files if _normalise(f.path) in selected]
        if not chosen:
            continue
        if keep_at_least_one and len(chosen) == len(group.files):
            keeper = group.sorted_files()[0]
            plan.protected.append(keeper.path)
            chosen = [f for f in chosen if f.path != keeper.path]
        for entry in chosen:
            if not os.path.exists(entry.path):
                plan.missing.append(entry.path)
                continue
            plan.to_delete.append(entry.path)
            plan.total_bytes += entry.size
    return plan


# ---------------------------------------------------------------------------
# Verwijderen naar de prullenbak
# ---------------------------------------------------------------------------


def move_to_trash(path: str | os.PathLike[str]) -> None:
    """Verplaats een bestand naar de systeemprullenbak (niet definitief wissen)."""
    try:
        from send2trash import send2trash  # type: ignore import-not-found
    except ImportError as exc:  # pragma: no cover - afhankelijk van omgeving
        raise ActionError(
            "Module 'send2trash' ontbreekt; installeer met: pip install send2trash"
        ) from exc
    try:
        send2trash(os.fspath(Path(path)))
    except Exception as exc:  # send2trash gooit platformspecifieke fouten
        raise ActionError(str(exc)) from exc


@dataclass
class DeletionOutcome:
    deleted: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    total_bytes: int = 0


def delete_paths(
    paths: Sequence[str],
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> DeletionOutcome:
    """Verplaats een reeks bestanden naar de prullenbak."""
    outcome = DeletionOutcome()
    total = len(paths)
    for index, path in enumerate(paths, start=1):
        if should_cancel and should_cancel():
            break
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        try:
            move_to_trash(path)
            outcome.deleted.append(path)
            outcome.total_bytes += size
        except ActionError as exc:
            outcome.failed.append((path, str(exc)))
        if on_progress:
            on_progress(index, total, path)
    return outcome


def verify_still_duplicate(group: DuplicateGroup, keeper_path: str, candidate_path: str) -> bool:
    """Controleer vlak voor verwijderen dat beide bestanden nog identiek zijn."""
    try:
        if os.path.getsize(keeper_path) != os.path.getsize(candidate_path):
            return False
        return full_hash(Path(keeper_path)) == full_hash(Path(candidate_path))
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Verkenner
# ---------------------------------------------------------------------------


def reveal_in_file_manager(path: str | os.PathLike[str]) -> None:
    """Open de map van het bestand en selecteer het bestand indien mogelijk."""
    target = Path(path)
    if sys.platform == "win32":
        # Verkenner verwacht backslashes en een komma zonder spatie erna.
        argument = f'/select,"{os.path.normpath(str(target))}"'
        try:
            subprocess.Popen(f"explorer {argument}")  # noqa: S607 - vaste opdracht
            return
        except OSError as exc:
            raise ActionError(f"Kon Verkenner niet openen: {exc}") from exc
    open_path(target.parent if target.is_file() else target)


def open_path(path: str | os.PathLike[str]) -> None:
    """Open een bestand of map met de standaardtoepassing van het systeem."""
    target = Path(path)
    try:
        if sys.platform == "win32":
            os.startfile(os.path.normpath(str(target)))  # type: ignore[attr-defined] # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        raise ActionError(f"Kon de map niet openen: {exc}") from exc


def copy_to_clipboard_text(paths: Iterable[str]) -> str:
    """Bouw de tekst die naar het klembord gaat (een pad per regel)."""
    return "\n".join(paths)


def open_folder(path: str | os.PathLike[str]) -> None:
    """Open een map (of de map van een bestand) in de bestandsbeheerder."""
    target = Path(path)
    open_path(target.parent if target.is_file() else target)
