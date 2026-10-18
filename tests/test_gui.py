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


def test_refresh_removes_handled_groups(window, media_tree):
    import os

    result = populate(window, media_tree)
    groups_before = len(result.groups)
    group = next(g for g in result.groups if g.count == 2)
    os.remove(group.files[1].path)

    window._refresh_list()

    assert window.tree.topLevelItemCount() == groups_before - 1
    assert len(window.result.groups) == groups_before - 1
    assert group.digest not in {g.digest for g in window.result.groups}


def test_refresh_keeps_checkboxes_on_surviving_rows(window, media_tree):
    import os

    result = populate(window, media_tree)
    window._select_all_duplicates()
    checked_before = set(window._checked_paths())

    doomed = next(g for g in result.groups if g.count == 2)
    os.remove(doomed.files[1].path)

    window._refresh_list()

    checked_after = set(window._checked_paths())
    assert checked_after == {p for p in checked_before if os.path.exists(p)}
    assert checked_after


def test_refresh_keeps_expanded_groups_expanded(window, media_tree):
    populate(window, media_tree)
    window.tree.topLevelItem(0).setExpanded(True)
    window.tree.topLevelItem(1).setExpanded(False)
    first_digest = window.tree.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole + 3)

    window._refresh_list()

    for i in range(window.tree.topLevelItemCount()):
        item = window.tree.topLevelItem(i)
        if item.data(0, Qt.ItemDataRole.UserRole + 3) == first_digest:
            assert item.isExpanded()
        else:
            assert not item.isExpanded()


def test_refresh_without_result_does_not_crash(window):
    window._refresh_list()
    assert window.tree.topLevelItemCount() == 0


def test_refresh_after_everything_is_cleaned_up(window, media_tree):
    import os

    result = populate(window, media_tree)
    for group in result.groups:
        for entry in group.files[1:]:
            os.remove(entry.path)

    window._refresh_list()

    assert window.tree.topLevelItemCount() == 0
    assert window.result.groups == []


# --------------------------------------------------------------------------
# Tabblad met onscherpe foto's
# --------------------------------------------------------------------------


def populate_blur(window, photo_tree):
    from dupefinder.blurscan import scan_sharpness

    result = scan_sharpness(ScanConfig(roots=[str(photo_tree)], use_cache=False))
    window.blur_tab.set_result(result)
    return result


def visible_names(tab):
    return sorted(
        tab.tree.topLevelItem(i).text(0)
        for i in range(tab.tree.topLevelItemCount())
        if not tab.tree.topLevelItem(i).isHidden()
    )


def test_blur_tab_lists_every_measured_photo(window, photo_tree):
    result = populate_blur(window, photo_tree)
    assert window.blur_tab.tree.topLevelItemCount() == len(result.entries)


def test_blur_tab_shows_only_suspects_by_default(window, photo_tree):
    populate_blur(window, photo_tree)
    names = visible_names(window.blur_tab)

    assert "vaag.jpg" in names
    assert "heel_vaag.jpg" in names
    assert "scherp.jpg" not in names
    assert "lucht.jpg" not in names  # weinig detail staat standaard uit


def test_low_detail_can_be_shown(window, photo_tree):
    populate_blur(window, photo_tree)
    window.blur_tab.low_detail_check.setChecked(True)
    assert "lucht.jpg" in visible_names(window.blur_tab)


def test_showing_everything(window, photo_tree):
    result = populate_blur(window, photo_tree)
    tab = window.blur_tab
    tab.only_blurry_check.setChecked(False)
    tab.low_detail_check.setChecked(True)
    assert len(visible_names(tab)) == len(result.entries)


def test_threshold_slider_widens_the_selection(window, photo_tree):
    populate_blur(window, photo_tree)
    tab = window.blur_tab

    tab.threshold_slider.setValue(0)
    strict = len(visible_names(tab))
    tab.threshold_slider.setValue(100)
    loose = len(visible_names(tab))

    assert loose > strict
    assert "scherp.jpg" not in visible_names(tab) or loose >= strict


def test_checking_visible_photos_and_delete_signal(window, photo_tree):
    populate_blur(window, photo_tree)
    tab = window.blur_tab
    # Het hoofdvenster opent een bevestigingsvenster; in de test vangen we het
    # signaal zelf op, anders blijft die dialoog wachten.
    tab.delete_requested.disconnect(window._delete_photo_paths)
    received = []
    tab.delete_requested.connect(received.append)

    tab._check_visible()
    checked = tab.checked_paths()
    assert checked
    assert all(path.endswith(".jpg") for path in checked)
    assert tab.delete_button.isEnabled()

    tab.delete_button.click()
    assert received == [checked]

    tab._clear_checks()
    assert tab.checked_paths() == []
    assert not tab.delete_button.isEnabled()


def test_removing_paths_updates_the_blur_tab(window, photo_tree):
    result = populate_blur(window, photo_tree)
    victim = result.entries[0].path

    window.blur_tab.remove_paths([victim])

    assert victim not in [e.path for e in window.blur_tab.result.entries]
    assert window.blur_tab.tree.topLevelItemCount() == len(result.entries)


def test_blur_tab_refresh_drops_missing_files(window, photo_tree):
    import os

    result = populate_blur(window, photo_tree)
    count_before = len(result.entries)
    os.remove(photo_tree / "vaag.jpg")

    window.blur_tab.refresh()

    assert len(window.blur_tab.result.entries) == count_before - 1
    assert "vaag.jpg" not in visible_names(window.blur_tab)


def test_scan_button_label_follows_the_tab(window):
    window.tabs.setCurrentIndex(1)
    assert "Scherpte" in window.scan_button.text()
    window.tabs.setCurrentIndex(0)
    assert "Scan starten" in window.scan_button.text()


def test_f5_refreshes_the_active_tab(window, media_tree, photo_tree):
    import os

    populate(window, media_tree)
    populate_blur(window, photo_tree)
    os.remove(photo_tree / "vaag.jpg")

    window.tabs.setCurrentIndex(1)
    window._refresh_active_tab()

    assert "vaag.jpg" not in [e.name for e in window.blur_tab.result.entries]
