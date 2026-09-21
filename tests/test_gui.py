"""Smoketests voor de interface; draaien headless via het 'offscreen' platform."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dupefinder.actions import plan_deletion  # noqa: E402
from dupefinder.config import ScanConfig  # noqa: E402
from dupefinder.gui.main_window import MainWindow  # noqa: E402
from dupefinder.scanner import scan  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr("dupefinder.gui.main_window.ScanConfig.load", lambda *a, **k: ScanConfig())
    monkeypatch.setattr("dupefinder.config.ScanConfig.save", lambda *a, **k: None)
    win = MainWindow()
    yield win
    win.thumbnails.shutdown()
    win.close()


def populate(window, media_tree):
    result = scan(ScanConfig(roots=[str(media_tree)], use_cache=False))
    window.result = result
    window._populate_tree(result)
    return result


def test_tree_shows_one_row_per_group(window, media_tree):
    result = populate(window, media_tree)
    assert window.tree.topLevelItemCount() == len(result.groups)
    total_children = sum(
        window.tree.topLevelItem(i).childCount() for i in range(window.tree.topLevelItemCount())
    )
    assert total_children == sum(group.count for group in result.groups)


def test_select_duplicates_keeps_one_per_group(window, media_tree):
    result = populate(window, media_tree)
    window._select_all_duplicates()

    checked = window._checked_paths()
    assert len(checked) == result.duplicate_files

    plan = plan_deletion(result.groups, checked)
    assert plan.protected == []
    assert len(plan.to_delete) == result.duplicate_files


def test_group_checkbox_toggles_children(window, media_tree):
    populate(window, media_tree)
    group_item = window.tree.topLevelItem(0)
    group_item.setCheckState(0, Qt.CheckState.Checked)

    states = [group_item.child(i).checkState(0) for i in range(group_item.childCount())]
    assert all(state == Qt.CheckState.Checked for state in states)

    # Alles aanvinken mag nooit een heel groepje wissen.
    plan = plan_deletion(window.result.groups, window._checked_paths())
    assert len(plan.protected) == 1


def test_clear_checks(window, media_tree):
    populate(window, media_tree)
    window._select_all_duplicates()
    assert window._checked_paths()
    window._clear_checks()
    assert window._checked_paths() == []
    assert not window.delete_button.isEnabled()


def test_filter_hides_non_matching_rows(window, media_tree):
    populate(window, media_tree)
    window._apply_filter("film")

    visible = [
        window.tree.topLevelItem(i)
        for i in range(window.tree.topLevelItemCount())
        if not window.tree.topLevelItem(i).isHidden()
    ]
    assert len(visible) == 1
    assert all(
        "film" in str(visible[0].child(j).data(0, Qt.ItemDataRole.UserRole)).lower()
        for j in range(visible[0].childCount())
        if not visible[0].child(j).isHidden()
    )


def test_keep_only_checks_the_others(window, media_tree):
    populate(window, media_tree)
    group_item = window.tree.topLevelItem(0)
    keeper = group_item.child(1)
    window._keep_only(keeper)

    assert keeper.checkState(0) == Qt.CheckState.Unchecked
    checked = window._checked_paths()
    assert len(checked) == group_item.childCount() - 1


def test_removing_paths_updates_view_and_result(window, media_tree):
    result = populate(window, media_tree)
    group = next(g for g in result.groups if g.count == 3)
    victim = group.files[2].path

    window._remove_paths_from_view([victim])

    remaining = [f.path for g in window.result.groups for f in g.files]
    assert victim not in remaining
    assert window.tree.topLevelItemCount() == len(window.result.groups)


def test_empty_result_clears_tree(window, media_tree):
    populate(window, media_tree)
    window._clear_results()
    assert window.tree.topLevelItemCount() == 0
    assert window.result is None
