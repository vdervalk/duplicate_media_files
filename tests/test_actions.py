import os

from dupefinder.actions import DeletionPlan, delete_paths, plan_deletion
from dupefinder.config import ScanConfig
from dupefinder.scanner import scan


def scan_tree(media_tree):
    return scan(ScanConfig(roots=[str(media_tree)], use_cache=False))


def test_plan_keeps_one_file_when_everything_is_checked(media_tree):
    result = scan_tree(media_tree)
    all_paths = [f.path for group in result.groups for f in group.files]

    plan = plan_deletion(result.groups, all_paths)

    assert len(plan.protected) == len(result.groups)
    assert len(plan.to_delete) == result.duplicate_files
    for group in result.groups:
        remaining = [f.path for f in group.files if f.path not in plan.to_delete]
        assert len(remaining) == 1


def test_plan_respects_partial_selection(media_tree):
    result = scan_tree(media_tree)
    group = next(g for g in result.groups if g.count == 3)
    chosen = [group.files[1].path]

    plan = plan_deletion(result.groups, chosen)

    assert plan.to_delete == [group.files[1].path]
    assert plan.protected == []
    assert plan.total_bytes == group.size


def test_plan_reports_missing_files(media_tree):
    result = scan_tree(media_tree)
    group = result.groups[0]
    victim = group.files[1].path
    os.remove(victim)

    plan = plan_deletion(result.groups, [victim])

    assert plan.missing == [victim]
    assert plan.to_delete == []


def test_plan_without_keep_rule_can_delete_everything(media_tree):
    result = scan_tree(media_tree)
    all_paths = [f.path for group in result.groups for f in group.files]

    plan = plan_deletion(result.groups, all_paths, keep_at_least_one=False)

    assert len(plan.to_delete) == len(all_paths)
    assert plan.protected == []


def test_delete_paths_reports_failures(tmp_path):
    missing = str(tmp_path / "bestaat-niet.jpg")
    outcome = delete_paths([missing])
    assert outcome.deleted == []
    assert len(outcome.failed) == 1


def test_empty_plan():
    assert DeletionPlan().is_empty
