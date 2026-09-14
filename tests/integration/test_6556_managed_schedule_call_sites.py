"""The read-only guard has to sit on the HTTP path and nowhere else.

Parsed from source rather than asserted on behaviour because the call sites
are what regress: a refactor that drops the registry lookup, or one that
"helpfully" adds it to the RPC, both leave every behavioural test green while
breaking the fix.
"""

import ast
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).parents[2]


def _parse(relative_path):
    return ast.parse((PLUGIN_ROOT / relative_path).read_text())


def _find_method(tree, class_name, method_name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"{class_name}.{method_name} not found")


def _reads_registry(node):
    return any(
        isinstance(child, ast.Attribute) and child.attr == "managed_schedules"
        for child in ast.walk(node)
    )


@pytest.fixture(scope="module")
def schedules_api():
    return _parse("api/v2/schedules.py")
def test_admin_put_refuses_before_it_writes(schedules_api):
    put = _find_method(schedules_api, "AdminAPI", "put")
    lookup_line = min(
        child.lineno for child in ast.walk(put)
        if isinstance(child, ast.Attribute) and child.attr == "managed_binding_for"
    )
    update_line = min(
        child.lineno for child in ast.walk(put)
        if isinstance(child, ast.Attribute) and child.attr == "update"
    )
    assert lookup_line < update_line


def test_admin_put_returns_409_for_a_managed_row(schedules_api):
    put = _find_method(schedules_api, "AdminAPI", "put")
    returned_codes = {
        child.value for node in ast.walk(put)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple)
        for child in node.value.elts
        if isinstance(child, ast.Constant) and isinstance(child.value, int)
    }
    assert 409 in returned_codes
def test_the_config_write_path_never_refuses():
    update_schedule = _find_method(_parse("rpc/main.py"), "RPC", "update_schedule")
    refusals = {"is_config_managed", "build_managed_conflict"}
    called = {
        child.func.id for child in ast.walk(update_schedule)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    assert not (called & refusals)


def test_the_config_write_path_keeps_a_signature_an_older_caller_can_use():
    update_schedule = _find_method(_parse("rpc/main.py"), "RPC", "update_schedule")
    args = [a.arg for a in update_schedule.args.args if a.arg != "self"]
    assert args == ["name", "cron", "active"]


def test_the_registry_is_created_before_init_can_fail():
    init = _find_method(_parse("module.py"), "Module", "__init__")
    assigned = {
        target.attr for node in ast.walk(init)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute)
    }
    assert "managed_schedules" in assigned


def test_registration_is_not_exposed_over_rpc():
    rpc_names = {
        arg.value
        for node in ast.walk(_parse("rpc/main.py"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rpc"
        for arg in node.args
        if isinstance(arg, ast.Constant)
    }
    assert not any("managed" in name for name in rpc_names)


def _calls(node, func_name):
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == func_name
        for child in ast.walk(node)
    )


def _scopes_to_global_rows(node):
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "is_"
        and isinstance(child.func.value, ast.Attribute)
        and child.func.value.attr == "project_id"
        for child in ast.walk(node)
    )


@pytest.mark.parametrize(
    "method", ["create_if_not_exists", "make_active", "update_schedule"]
)
def test_global_schedule_lookups_exclude_project_rows(method):
    assert _scopes_to_global_rows(_find_method(_parse("rpc/main.py"), "RPC", method))
def test_ready_rebuilds_the_registry_from_the_owning_plugins():
    ready = _find_method(_parse("module.py"), "Module", "ready")
    assert any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "collect_managed_schedules"
        for child in ast.walk(ready)
    )


def test_reconciliation_reads_every_duplicate_global_row():
    update_schedule = _find_method(_parse("rpc/main.py"), "RPC", "update_schedule")
    terminators = {
        child.func.attr for child in ast.walk(update_schedule)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
        and child.func.attr in {"first", "all"}
    }
    assert terminators == {"all"}


def test_reconciliation_parks_duplicates_instead_of_activating_them():
    src = (PLUGIN_ROOT / "rpc" / "main.py").read_text()
    body = src[src.index("def update_schedule"):src.index("def time_to_run")]
    assert "duplicate.active = False" in body
    assert "plan_reconciliation" in body, (
        "parking must be restricted to rows the config drives"
    )


def test_create_matches_on_the_handler_only_when_asked():
    create = _find_method(_parse("rpc/main.py"), "RPC", "create_if_not_exists")
    clause_appends = [
        node for node in ast.walk(create)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "append"
    ]
    assert clause_appends, "the handler clause is unconditional"
    for node in clause_appends:
        enclosing = [
            parent for parent in ast.walk(create)
            if isinstance(parent, ast.If)
            and any(node is child for child in ast.walk(parent))
        ]
        assert enclosing, "the handler clause is not gated"


def test_no_matching_row_means_no_write_at_all():
    update_schedule = _find_method(_parse("rpc/main.py"), "RPC", "update_schedule")

    assignments = [
        node for node in ast.walk(update_schedule)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Tuple)
            and any(isinstance(e, ast.Name) and e.id == "duplicates" for e in t.elts)
            for t in node.targets
        )
    ]
    assert len(assignments) == 1, "the plan is recomputed; a fallback crept in"
    assert isinstance(assignments[0].value, ast.Call)
    assert assignments[0].value.func.id == "plan_reconciliation"

    guard = next(
        node for node in ast.walk(update_schedule)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and getattr(node.test.left, "id", None) == "schedule"
    )
    assert any(isinstance(stmt, ast.Raise) for stmt in guard.body)


def test_ambiguous_name_with_no_registered_owner_writes_nothing():
    update_schedule = _find_method(_parse("rpc/main.py"), "RPC", "update_schedule")
    guard = next(
        node for node in ast.walk(update_schedule)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.BoolOp)
        and any(
            isinstance(v, ast.Compare)
            and getattr(v.left, "id", None) == "expected"
            for v in node.test.values
        )
    )
    assert any(isinstance(stmt, ast.Raise) for stmt in guard.body)


def test_cleanup_reclaims_surplus_duplicates_of_a_managed_schedule():
    cleanup = _find_method(
        _parse("methods/admin_tasks.py"), "Method", "cleanup_orphaned_schedules"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "plan_cleanup"
        for node in ast.walk(cleanup)
    ), "cleanup does not delegate to the tested planner"
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location(
        "ms", PLUGIN_ROOT / "utils" / "managed_schedules.py"
    )
    ms = module_from_spec(spec)
    spec.loader.exec_module(ms)
    assert "surplus duplicate" in ms.VERDICT_REASONS[ms.SURPLUS]


def _module_method(name):
    return _find_method(_parse("module.py"), "Module", name)


def test_the_reload_window_treats_global_rows_as_owned():
    guard = next(
        node for node in ast.walk(_module_method("managed_binding_for"))
        if isinstance(node, ast.If)
    )
    assert any(
        isinstance(child, ast.UnaryOp) and isinstance(child.op, ast.Not)
        for child in ast.walk(guard.test)
    ), ast.dump(guard.test)
    returned = [
        stmt.value.id for stmt in guard.body
        if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name)
    ]
    assert returned == ["PENDING_BINDING"], ast.dump(guard)


def test_readiness_is_set_unconditionally_and_always():
    collect = _module_method("collect_managed_schedules")

    tries = [node for node in collect.body if isinstance(node, ast.Try)]
    assert len(tries) == 1, "the collect body is no longer one try/finally"

    finally_assigns = [
        stmt for stmt in tries[0].finalbody
        if isinstance(stmt, ast.Assign)
        and any(
            isinstance(t, ast.Attribute) and t.attr == "managed_schedules_collected"
            for t in stmt.targets
        )
    ]
    assert len(finally_assigns) == 1, (
        "readiness is not set as a direct statement of the finally block"
    )
    assert isinstance(finally_assigns[0].value, ast.Constant), (
        "readiness is derived from something instead of simply set"
    )
    assert finally_assigns[0].value.value is True

    all_assigns = [
        node for node in ast.walk(collect)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Attribute) and t.attr == "managed_schedules_collected"
            for t in node.targets
        )
    ]
    assert len(all_assigns) == 1, "readiness is set in more than one place"


def test_descriptor_iteration_is_snapshotted():
    collect = _module_method("collect_managed_schedules")
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "list"
        for node in ast.walk(collect)
    )


@pytest.mark.parametrize(
    "path,cls,method,helper",
    [
        ("api/v2/schedules.py", "AdminAPI", "get", "managed_binding_for"),
        ("api/v2/schedules.py", "AdminAPI", "put", "managed_binding_for"),
        ("rpc/main.py", "RPC", "delete_schedules", "is_row_protected"),
        ("rpc/main.py", "RPC", "make_active", "is_name_protected"),
    ],
)
def test_every_non_owner_path_uses_the_shared_protected_state(path, cls, method, helper):
    node = _find_method(_parse(path), cls, method)
    assert any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == helper
        for child in ast.walk(node)
    ), f"{cls}.{method} does not go through {helper}"


def test_the_owner_write_path_is_exempt():
    update_schedule = _find_method(_parse("rpc/main.py"), "RPC", "update_schedule")
    assert not any(
        isinstance(child, ast.Attribute)
        and child.attr in {"managed_binding_for", "is_row_protected", "is_name_protected"}
        for child in ast.walk(update_schedule)
    )


def test_put_refuses_when_owned_not_when_free():
    put = _find_method(_parse("api/v2/schedules.py"), "AdminAPI", "put")
    guard = next(
        node for node in ast.walk(put)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and getattr(node.test.left, "id", None) == "managed_by"
    )
    assert isinstance(guard.test.ops[0], ast.IsNot), ast.dump(guard.test)
    codes = {
        c.value for stmt in guard.body if isinstance(stmt, ast.Return)
        and isinstance(stmt.value, ast.Tuple)
        for c in stmt.value.elts
        if isinstance(c, ast.Constant) and isinstance(c.value, int)
    }
    assert codes == {409}, codes


def test_delete_refuses_the_owned_rows_not_the_free_ones():
    delete = _find_method(_parse("rpc/main.py"), "RPC", "delete_schedules")
    comprehensions = [
        node for node in ast.walk(delete)
        if isinstance(node, ast.ListComp) and node.generators[0].ifs
    ]
    assert len(comprehensions) == 2, "the protect/delete split is no longer two passes"

    protected, deleted = comprehensions
    guard = protected.generators[0].ifs[0]
    assert isinstance(guard, ast.Call)
    assert guard.func.attr == "is_row_protected"

    exclusion = deleted.generators[0].ifs[0]
    assert isinstance(exclusion, ast.Compare)
    assert isinstance(exclusion.ops[0], ast.NotIn), ast.dump(exclusion)
    assert exclusion.comparators[0].id == "managed"


def test_make_active_refuses_when_protected_not_when_free():
    make_active = _find_method(_parse("rpc/main.py"), "RPC", "make_active")
    guard = next(
        node for node in ast.walk(make_active)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and getattr(node.test.func, "attr", None) == "is_name_protected"
    )
    assert not isinstance(guard.test, ast.UnaryOp)
    assert any(
        isinstance(stmt, ast.Return)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is False
        for stmt in guard.body
    ), "the refusal is indistinguishable from success"


def test_cleanup_will_not_run_while_ownership_is_unresolved():
    cleanup = _find_method(
        _parse("methods/admin_tasks.py"), "Method", "cleanup_orphaned_schedules"
    )
    guard = next(
        node for node in ast.walk(cleanup)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and getattr(node.test.operand, "attr", None) == "managed_schedules_collected"
    )
    assert any(isinstance(stmt, ast.Return) for stmt in guard.body)


def test_make_active_orders_before_it_picks():
    make_active = _find_method(_parse("rpc/main.py"), "RPC", "make_active")
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "order_by"
        for node in ast.walk(make_active)
    )


def test_the_window_gate_only_claims_global_rows():
    binding_for = _module_method("managed_binding_for")
    guard = next(node for node in ast.walk(binding_for) if isinstance(node, ast.If))
    assert isinstance(guard.test, ast.BoolOp) and isinstance(guard.test.op, ast.And)
    assert any(
        isinstance(v, ast.Compare)
        and getattr(v.left, "attr", None) == "project_id"
        for v in guard.test.values
    ), ast.dump(guard.test)


def test_the_listing_has_no_second_route_to_the_resolver():
    src = (PLUGIN_ROOT / "api" / "v2" / "schedules.py").read_text()
    assert "resolve_managed_binding" not in src


def test_cleanup_delegates_its_whole_decision_to_the_tested_planner():
    cleanup = _find_method(
        _parse("methods/admin_tasks.py"), "Method", "cleanup_orphaned_schedules"
    )
    src = ast.get_source_segment(
        (PLUGIN_ROOT / "methods" / "admin_tasks.py").read_text(), cleanup
    )
    assert "is_row_protected" not in src
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "plan_cleanup"
        for node in ast.walk(cleanup)
    )
    for leaked in ("canonical_ids", "by_name", "resolve_managed_handler"):
        assert leaked not in src, leaked


def test_collection_runs_before_anything_that_can_raise_in_ready():
    ready = _module_method("ready")
    collect_line = min(
        child.lineno for child in ast.walk(ready)
        if isinstance(child, ast.Call)
        and getattr(child.func, "attr", None) == "collect_managed_schedules"
    )
    other_lines = [
        child.lineno for child in ast.walk(ready)
        if isinstance(child, ast.Call)
        and getattr(child.func, "attr", None) in {"start", "register_admin_task"}
    ]
    assert other_lines and collect_line < min(other_lines)


def test_the_delete_list_is_built_from_the_shared_verdict_set():
    cleanup = _find_method(
        _parse("methods/admin_tasks.py"), "Method", "cleanup_orphaned_schedules"
    )
    assigns = [
        node for node in ast.walk(cleanup)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "orphaned" for t in node.targets)
    ]
    built = [a for a in assigns if isinstance(a.value, ast.ListComp)]
    assert built, "the delete list is not a comprehension over the plan"
    guard = built[0].value.generators[0].ifs[0]
    assert isinstance(guard, ast.Compare)
    assert isinstance(guard.ops[0], ast.In)
    assert guard.comparators[0].id == "ORPHANING_VERDICTS"

    src = ast.get_source_segment(
        (PLUGIN_ROOT / "methods" / "admin_tasks.py").read_text(), cleanup
    )
    assert "orphaned.append" not in src


def test_creation_identity_comes_from_the_payload_not_the_registry():
    create = _find_method(_parse("rpc/main.py"), "RPC", "create_if_not_exists")
    guard = next(
        node for node in ast.walk(create)
        if isinstance(node, ast.If)
        and getattr(node.test, "id", None) == "match_handler"
    )
    assert any(
        isinstance(child, ast.Attribute) and child.attr == "rpc_func"
        for stmt in guard.body for child in ast.walk(stmt)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_managed_handler"
        for node in ast.walk(create)
    ), "creation still asks this process's registry"
