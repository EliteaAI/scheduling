"""Which schedules the platform configuration owns, and what the refusal says.

`index_scheduling` and `pipeline_scheduling` take their cadence from the
elitea_core plugin configuration, which is re-pushed on every boot and
reconfig. The refusal has to say where the setting actually lives, or the
admin is left with a disabled control and no destination.
"""

import importlib.util
from pathlib import Path


_MODULE_PATH = Path(__file__).parents[2] / "utils" / "managed_schedules.py"
_SPEC = importlib.util.spec_from_file_location("managed_schedules", _MODULE_PATH)
managed_schedules = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(managed_schedules)



HANDLER_NAME = "applications_check_index_scheduling"
BINDING = {
    "section": "runtime",
    "fields": ["index_scheduling_enabled", "index_scheduling_cron"],
}
ENTRY = {"managed_by": BINDING, "rpc_func": "applications_check_index_scheduling"}


def test_conflict_names_the_schedule():
    body = managed_schedules.build_managed_conflict("index_scheduling", BINDING)
    assert "index_scheduling" in body["error"]


def test_conflict_carries_the_config_location():
    body = managed_schedules.build_managed_conflict("index_scheduling", BINDING)
    assert body["managed_by"] == BINDING
    assert body["managed_by"]["section"] == "runtime"
    assert body["managed_by"]["fields"] == [
        "index_scheduling_enabled",
        "index_scheduling_cron",
    ]


def test_a_global_schedule_matching_a_binding_is_managed():
    registry = {"index_scheduling": ENTRY}
    assert managed_schedules.resolve_managed_binding(
        registry, "index_scheduling", None
    ) == BINDING


def test_a_project_schedule_of_the_same_name_is_not_managed():
    registry = {"index_scheduling": ENTRY}
    assert managed_schedules.resolve_managed_binding(
        registry, "index_scheduling", 2
    ) is None


def test_project_zero_is_still_a_project():
    registry = {"index_scheduling": ENTRY}
    assert managed_schedules.resolve_managed_binding(
        registry, "index_scheduling", 0
    ) is None


def test_an_unbound_global_schedule_is_not_managed():
    assert managed_schedules.resolve_managed_binding(
        {"index_scheduling": ENTRY}, "mcp_servers_handler", None
    ) is None


def test_is_config_managed_follows_the_same_scoping():
    registry = {"index_scheduling": ENTRY}
    assert managed_schedules.is_config_managed(registry, "index_scheduling", None)
    assert not managed_schedules.is_config_managed(registry, "index_scheduling", 2)
    assert not managed_schedules.is_config_managed(registry, "eval_run_reap", None)


class _Row:
    def __init__(self, id, rpc_func, name="index_scheduling", project_id=None):
        self.id = id
        self.rpc_func = rpc_func
        self.name = name
        self.project_id = project_id


def test_only_rows_on_the_expected_handler_are_driven():
    rows = [_Row(3, "hand_made"), _Row(9, "applications_check_index_scheduling")]
    driven, parked = managed_schedules.plan_reconciliation(
        rows, "applications_check_index_scheduling"
    )
    assert driven.id == 9
    assert parked == []


def test_duplicates_on_the_same_handler_are_parked():
    rows = [_Row(3, "the_handler"), _Row(9, "the_handler")]
    driven, parked = managed_schedules.plan_reconciliation(rows, "the_handler")
    assert driven.id == 3
    assert [item.id for item in parked] == [9]


def test_an_unknown_handler_drives_the_oldest_and_parks_nothing():
    rows = [_Row(3, "whatever"), _Row(9, "something_else")]
    driven, parked = managed_schedules.plan_reconciliation(rows, None)
    assert driven.id == 3
    assert parked == []


def test_no_row_on_the_expected_handler_drives_nothing():
    rows = [_Row(3, "hand_made"), _Row(9, "also_wrong")]
    assert managed_schedules.plan_reconciliation(rows, "expected") == (None, [])


def test_handler_is_resolved_from_the_registry():
    registry = {"index_scheduling": ENTRY}
    assert managed_schedules.resolve_managed_handler(registry, "index_scheduling") == (
        "applications_check_index_scheduling"
    )
    assert managed_schedules.resolve_managed_handler(registry, "eval_run_reap") is None


class _Registry:
    def __init__(self):
        self.managed_schedules = {}
        self._managed_owners = {}

    register_managed_schedules = None


def _make_registry():
    src = (Path(__file__).parents[2] / "module.py").read_text()
    body = src[src.index("    def register_managed_schedules"):src.index("    def deinit")]
    namespace = {"log": type("L", (), {"info": staticmethod(lambda *a, **k: None)})()}
    exec(compile("class _R:\n" + body, "module.py", "exec"), namespace)
    registry = _Registry()
    registry.register_managed_schedules = (
        namespace["_R"].register_managed_schedules.__get__(registry)
    )
    return registry


def test_registering_an_owner_replaces_its_previous_bindings():
    registry = _make_registry()
    registry.register_managed_schedules("elitea_core", {
        "index_scheduling": ENTRY,
        "pipeline_scheduling": {"managed_by": BINDING, "rpc_func": "pipelines_check_scheduling"},
    })
    assert set(registry.managed_schedules) == {"index_scheduling", "pipeline_scheduling"}

    registry.register_managed_schedules("elitea_core", {"index_scheduling": ENTRY})
    assert set(registry.managed_schedules) == {"index_scheduling"}


def test_owners_do_not_release_each_others_bindings():
    registry = _make_registry()
    registry.register_managed_schedules("elitea_core", {"index_scheduling": ENTRY})
    registry.register_managed_schedules("other_plugin", {"other_thing": ENTRY})
    registry.register_managed_schedules("elitea_core", {})
    assert set(registry.managed_schedules) == {"other_thing"}


def test_a_row_borrowing_a_managed_name_is_not_managed():
    registry = {"index_scheduling": ENTRY}
    assert not managed_schedules.is_config_managed(
        registry, "index_scheduling", None, "hand_made_wrong_rpc"
    )
    assert managed_schedules.is_config_managed(
        registry, "index_scheduling", None, "applications_check_index_scheduling"
    )


def test_a_caller_with_no_row_in_hand_still_gets_protection():
    registry = {"index_scheduling": ENTRY}
    assert managed_schedules.is_config_managed(registry, "index_scheduling", None)


def test_a_malformed_binding_set_releases_nothing():
    registry = _make_registry()
    registry.register_managed_schedules("elitea_core", {"index_scheduling": ENTRY})
    try:
        registry.register_managed_schedules("elitea_core", {"pipeline_scheduling": {}})
    except KeyError:
        pass
    assert set(registry.managed_schedules) == {"index_scheduling"}


def test_listing_and_enforcement_agree_on_a_stray_row():
    registry = {"index_scheduling": ENTRY}
    stray = ("index_scheduling", None, "hand_made_wrong_rpc")
    real = ("index_scheduling", None, "applications_check_index_scheduling")

    assert managed_schedules.resolve_managed_binding(registry, *stray) is None
    assert not managed_schedules.is_config_managed(registry, *stray)

    assert managed_schedules.resolve_managed_binding(registry, *real) == BINDING
    assert managed_schedules.is_config_managed(registry, *real)


def test_a_binding_without_a_handler_is_refused_at_registration():
    registry = _make_registry()
    registry.register_managed_schedules("elitea_core", {"index_scheduling": ENTRY})
    try:
        registry.register_managed_schedules(
            "elitea_core", {"my_tick": {"managed_by": BINDING}}
        )
    except KeyError:
        pass
    else:
        raise AssertionError("a handler-less binding was accepted")
    assert set(registry.managed_schedules) == {"index_scheduling"}


def test_make_active_and_delete_agree_on_what_they_refuse():
    src = (Path(__file__).parents[2] / "rpc" / "main.py").read_text()
    for name, nxt in (
        ("def make_active", "@web.rpc('scheduling_update_schedule')"),
        ("def delete_schedules", "@web.rpc('get_schedules')"),
    ):
        body = src[src.index(name):src.index(nxt)]
        assert "is_row_protected" in body or "is_name_protected" in body, name


def test_a_managed_name_is_refused_whichever_row_would_be_picked():
    registry = {"index_scheduling": ENTRY}
    assert managed_schedules.is_managed_name(registry, "index_scheduling")
    assert not managed_schedules.is_managed_name(registry, "mcp_servers_handler")


def test_make_active_refuses_before_it_reads_a_row():
    src = (Path(__file__).parents[2] / "rpc" / "main.py").read_text()
    body = src[src.index("def make_active"):src.index("@web.rpc('scheduling_update_schedule')")]
    assert body.index("is_name_protected") < body.index("session.query")


def test_an_unknown_owner_with_one_row_still_updates_it():
    rows = [_Row(3, "whatever")]
    driven, parked = managed_schedules.plan_reconciliation(rows, None)
    assert driven.id == 3 and parked == []



REGISTRY = {"index_scheduling": ENTRY}
REGISTERED_RPCS = {"applications_check_index_scheduling", "mcp_servers_handler"}


def _classify(row, canonical_ids=frozenset()):
    return managed_schedules.classify_schedule(
        row, canonical_ids, REGISTRY, REGISTERED_RPCS
    )


def test_the_canonical_row_is_kept():
    row = _Row(23, "applications_check_index_scheduling")
    assert _classify(row, {23}) == managed_schedules.CANONICAL


def test_a_second_row_on_the_owned_handler_is_surplus():
    row = _Row(27, "applications_check_index_scheduling")
    assert _classify(row, {23}) == managed_schedules.SURPLUS


def test_an_ordinary_schedule_is_never_surplus():
    row = _Row(4, "mcp_servers_handler", name="mcp_servers_handler")
    assert _classify(row) == managed_schedules.REGISTERED


def test_every_ordinary_row_survives_a_sweep():
    rows = [
        _Row(1, "projects_create_personal_project", name="projects_create_personal_project"),
        _Row(4, "mcp_servers_handler", name="mcp_servers_handler"),
        _Row(23, "applications_check_index_scheduling"),
    ]
    registered = {r.rpc_func for r in rows}
    verdicts = [
        managed_schedules.classify_schedule(r, {23}, REGISTRY, registered)
        for r in rows
    ]
    assert managed_schedules.SURPLUS not in verdicts
    assert managed_schedules.ORPHAN not in verdicts


def test_an_unregistered_handler_is_an_orphan():
    row = _Row(15, "elitea_core_reclaim_interrupted_indexes", name="index_reclaim")
    assert _classify(row) == managed_schedules.ORPHAN


def test_a_project_row_is_never_surplus():
    row = _Row(30, "applications_check_index_scheduling", project_id=2)
    assert _classify(row) == managed_schedules.REGISTERED


def test_a_name_carries_no_pending_state():
    import ast
    tree = ast.parse((Path(__file__).parents[2] / "module.py").read_text())
    method = next(
        item for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "Module"
        for item in node.body
        if isinstance(item, ast.FunctionDef) and item.name == "is_name_protected"
    )
    statements = [n for n in method.body if not isinstance(n, ast.Expr)]
    assert len(statements) == 1, "the name path grew a branch"
    assert isinstance(statements[0], ast.Return)
    assert isinstance(statements[0].value, ast.Call)
    assert statements[0].value.func.id == "is_managed_name"



def _live_table():
    return [
        _Row(1, "projects_create_personal_project", name="projects_create_personal_project"),
        _Row(4, "mcp_servers_handler", name="mcp_servers_handler"),
        _Row(12, "pipelines_check_scheduling", name="pipeline_scheduling"),
        _Row(15, "elitea_core_reclaim_interrupted_indexes", name="index_reclaim"),
        _Row(23, "applications_check_index_scheduling", name="index_scheduling"),
    ]


FULL_REGISTRY = {
    "index_scheduling": ENTRY,
    "pipeline_scheduling": {
        "managed_by": BINDING, "rpc_func": "pipelines_check_scheduling",
    },
}


def _orphans(rows, registry=None, registered=None):
    registry = FULL_REGISTRY if registry is None else registry
    if registered is None:
        registered = {
            "projects_create_personal_project", "mcp_servers_handler",
            "applications_check_index_scheduling", "pipelines_check_scheduling",
        }
    return [
        row.id for row, verdict in managed_schedules.plan_cleanup(
            rows, registry, registered
        )
        if verdict in (managed_schedules.SURPLUS, managed_schedules.ORPHAN)
    ]


def test_a_healthy_table_loses_only_the_genuinely_unregistered_row():
    assert _orphans(_live_table()) == [15]


def test_the_config_owned_rows_are_never_orphaned():
    orphans = _orphans(_live_table())
    assert 12 not in orphans and 23 not in orphans


def test_a_duplicate_is_orphaned_and_the_oldest_kept():
    rows = _live_table() + [_Row(27, "applications_check_index_scheduling")]
    assert _orphans(rows) == [15, 27]


def test_a_project_row_sharing_a_managed_name_is_not_canonical():
    rows = [
        _Row(5, "applications_check_index_scheduling", project_id=2),
    ] + _live_table()
    orphans = _orphans(rows)
    assert 23 not in orphans, "the global row lost canonical status to a project row"
    assert 5 not in orphans, "a project row was judged against a global binding"


def test_an_empty_registry_orphans_only_unregistered_handlers():
    assert _orphans(_live_table(), registry={}) == [15]


def test_only_surplus_and_orphan_verdicts_delete():
    assert managed_schedules.ORPHANING_VERDICTS == frozenset({
        managed_schedules.SURPLUS, managed_schedules.ORPHAN,
    })
    assert managed_schedules.CANONICAL not in managed_schedules.ORPHANING_VERDICTS
    assert managed_schedules.REGISTERED not in managed_schedules.ORPHANING_VERDICTS


def test_every_verdict_has_a_reason_to_log():
    for verdict in (
        managed_schedules.CANONICAL, managed_schedules.SURPLUS,
        managed_schedules.REGISTERED, managed_schedules.ORPHAN,
    ):
        assert managed_schedules.VERDICT_REASONS[verdict]


def test_canonical_does_not_depend_on_the_order_it_is_given():
    newest = _Row(9, "the_handler")
    oldest = _Row(3, "the_handler")

    driven, parked = managed_schedules.plan_reconciliation(
        [newest, oldest], "the_handler",
    )
    assert driven.id == 3
    assert [item.id for item in parked] == [9]


def test_the_two_planners_agree_on_canonical_whatever_the_order():
    rows = [_Row(9, HANDLER_NAME), _Row(3, HANDLER_NAME)]
    registry = {"index_scheduling": {"managed_by": BINDING, "rpc_func": HANDLER_NAME}}

    driven, _ = managed_schedules.plan_reconciliation(rows, HANDLER_NAME)
    surplus = [
        row.id for row, verdict in managed_schedules.plan_cleanup(
            rows, registry, {HANDLER_NAME},
        )
        if verdict == managed_schedules.SURPLUS
    ]
    assert driven.id == 3 and surplus == [9]
