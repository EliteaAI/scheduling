"""Issue #6556 — the schedule RPCs, exercised against a real database.

Everything here used to be asserted by reading the source, and each round that
let a one-token change restore the original bug with a green suite. These drive
the actual methods over SQLite rows, so the questions asked are "what is in the
table afterwards" rather than "does this identifier appear".
"""

import types
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import Boolean, Column, Integer, JSON, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

PLUGIN_ROOT = Path(__file__).parents[2]
Base = declarative_base()


class Schedule(Base):
    """Mirrors models/schedule.py's columns; the RPCs only use these."""

    __tablename__ = "schedule"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)
    project_id = Column(Integer, nullable=True)
    cron = Column(String(64), nullable=False)
    active = Column(Boolean, default=True)
    rpc_func = Column(String(64), nullable=False)
    rpc_kwargs = Column(JSON, nullable=False, default=dict)


class _ScheduleModelPD:
    """Stands in for the pydantic model: parse, round-trip, save."""

    _session_factory = None

    def __init__(self, **data):
        self.__dict__.update(data)

    @classmethod
    def parse_obj(cls, data):
        payload = {k: v for k, v in data.items() if k != "match_handler"}
        payload.setdefault("rpc_kwargs", {})
        payload.setdefault("id", None)
        return cls(**payload)

    @classmethod
    def from_orm(cls, row):
        return cls(
            id=row.id, name=row.name, cron=row.cron, active=row.active,
            rpc_func=row.rpc_func, rpc_kwargs=row.rpc_kwargs,
        )

    def save(self):
        with self._session_factory() as session:
            row = Schedule(
                name=self.name, cron=self.cron, active=self.active,
                rpc_func=self.rpc_func, rpc_kwargs=self.rpc_kwargs or {},
            )
            session.add(row)
            session.commit()
            self.id = row.id
            return self.id


def _strip_supplied_imports(source, supplied):
    """Drop the imports whose names the caller is providing.

    The relative ones cannot resolve outside the package, and the pylon/tools
    ones would pull in a runtime this has no business starting.
    """
    out, skipping = [], False
    for line in source.splitlines():
        stripped = line.strip()
        if skipping:
            skipping = not stripped.endswith(")")
            continue
        if stripped.startswith(("from ..", "from .")) or any(
            stripped.startswith(f"from {mod} import") for mod in supplied
        ):
            skipping = stripped.endswith("(")
            continue
        out.append(line)
    return "\n".join(out)


def _load_module(path, name, namespace):
    source = _strip_supplied_imports(
        (PLUGIN_ROOT / path).read_text(), {"pylon.core.tools", "tools"},
    )
    module = types.ModuleType(name)
    module.__dict__.update(namespace)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


@pytest.fixture
def plugin():
    """A stand-in module object carrying the real RPC and registry methods."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    _ScheduleModelPD._session_factory = factory

    @contextmanager
    def session_scope(_project_id=None):
        session = factory()
        try:
            yield session
        finally:
            session.close()

    managed_schedules = _load_module(
        "utils/managed_schedules.py", "ms6556", {},
    )
    quiet = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, exception=lambda *a, **k: None,
        critical=lambda *a, **k: None, debug=lambda *a, **k: None,
    )
    rpc = _load_module("rpc/main.py", "rpc6556", {
        "Schedule": Schedule,
        "ScheduleModelPD": _ScheduleModelPD,
        "db": types.SimpleNamespace(
            with_project_schema_session=session_scope, session=None,
        ),
        "web": types.SimpleNamespace(rpc=lambda *a, **k: (lambda f: f)),
        "log": quiet,
        **{
            name: getattr(managed_schedules, name)
            for name in (
                "plan_reconciliation", "resolve_managed_handler",
                "is_config_managed", "resolve_managed_binding",
                "is_managed_name", "PENDING_BINDING",
            )
        },
    })

    module_src = (PLUGIN_ROOT / "module.py").read_text()
    body = module_src[
        module_src.index("    def managed_binding_for"):
        module_src.index("    def register_managed_schedules")
    ]
    registry_body = module_src[
        module_src.index("    def collect_managed_schedules"):
        module_src.index("    def deinit")
    ]
    ns = {
        "log": quiet,
        "resolve_managed_binding": managed_schedules.resolve_managed_binding,
        "is_managed_name": managed_schedules.is_managed_name,
        "PENDING_BINDING": managed_schedules.PENDING_BINDING,
    }
    exec(compile("class _M:\n" + body + registry_body, "module.py", "exec"), ns)

    obj = types.SimpleNamespace(
        managed_schedules={}, _managed_owners={}, managed_schedules_ready=True,
        session_factory=factory,
        context=types.SimpleNamespace(
            module_manager=types.SimpleNamespace(descriptors={}),
        ),
    )
    for cls, source in ((ns["_M"], None), (rpc.RPC, None)):
        for attr, value in vars(cls).items():
            if callable(value) and not attr.startswith("__"):
                setattr(obj, attr, value.__get__(obj))
    return obj


BINDING = {"section": "runtime", "fields": ["index_scheduling_cron"]}
HANDLER = "applications_check_index_scheduling"


def _own(plugin, rpc_func=HANDLER):
    plugin.register_managed_schedules(
        "elitea_core",
        {"index_scheduling": {"managed_by": BINDING, "rpc_func": rpc_func}},
    )


def _rows(plugin, **filters):
    with plugin.session_factory() as session:
        query = session.query(Schedule)
        for key, value in filters.items():
            query = query.filter(getattr(Schedule, key) == value)
        return query.order_by(Schedule.id).all()


def _insert(plugin, **values):
    with plugin.session_factory() as session:
        row = Schedule(**{"cron": "* * * * *", "active": True,
                          "rpc_kwargs": {}, **values})
        session.add(row)
        session.commit()
        return row.id


PAYLOAD = {
    "name": "index_scheduling", "cron": "* * * * *", "active": True,
    "rpc_func": HANDLER, "rpc_kwargs": {}, "match_handler": True,
}


# --- creation identity (#1, #5) ----------------------------------------------

def test_a_renamed_handler_gets_the_real_row_created_beside_it(plugin):
    """match_handler False restores the deadlock: the stale row is taken for
    the real one, so the platform's schedule is never created."""
    _own(plugin)
    stale = _insert(plugin, name="index_scheduling", rpc_func="old_handler_name")

    plugin.create_if_not_exists(dict(PAYLOAD))

    handlers = {row.rpc_func for row in _rows(plugin)}
    assert handlers == {"old_handler_name", HANDLER}
    assert len(_rows(plugin)) == 2 and stale


def test_an_ordinary_schedule_is_not_duplicated_when_its_handler_changes(plugin):
    """match_handler True for everything inserts a second row the day any
    unmanaged handler is renamed."""
    _insert(plugin, name="mcp_servers_handler", rpc_func="old_mcp_handler")

    plugin.create_if_not_exists({
        "name": "mcp_servers_handler", "cron": "*/1 * * * *", "active": True,
        "rpc_func": "mcp_servers_handler", "rpc_kwargs": {},
    })

    assert len(_rows(plugin, name="mcp_servers_handler")) == 1


def test_creation_never_adopts_a_project_row(plugin):
    """Scoped to global rows: a project schedule of the same name belongs to
    the project, and adopting it would leave the platform without one."""
    _own(plugin)
    _insert(plugin, name="index_scheduling", rpc_func=HANDLER, project_id=2)

    plugin.create_if_not_exists(dict(PAYLOAD))

    assert len(_rows(plugin, project_id=None)) == 1


# --- the read-only guard itself (#2) -----------------------------------------

def test_an_owned_row_is_reported_owned(plugin):
    """The single decision point behind both the listing and the edit."""
    _own(plugin)
    row = _rows(plugin)[0] if _rows(plugin) else None
    _insert(plugin, name="index_scheduling", rpc_func=HANDLER)
    row = _rows(plugin)[0]

    assert plugin.managed_binding_for(row) == BINDING
    assert plugin.is_row_protected(row) is True


def test_an_ordinary_row_is_reported_free(plugin):
    _own(plugin)
    _insert(plugin, name="mcp_servers_handler", rpc_func="mcp_servers_handler")
    row = _rows(plugin, name="mcp_servers_handler")[0]

    assert plugin.managed_binding_for(row) is None
    assert plugin.is_row_protected(row) is False


def test_a_project_row_is_reported_free(plugin):
    _own(plugin)
    _insert(plugin, name="index_scheduling", rpc_func=HANDLER, project_id=2)
    row = _rows(plugin, project_id=2)[0]

    assert plugin.managed_binding_for(row) is None


def test_while_ownership_is_unresolved_global_rows_read_owned(plugin):
    """The reload window: answering "free" hands out controls whose writes the
    next reconcile reverts."""
    plugin.managed_schedules_ready = False
    _insert(plugin, name="mcp_servers_handler", rpc_func="mcp_servers_handler")
    _insert(plugin, name="anything", rpc_func="x", project_id=2)

    glob = _rows(plugin, name="mcp_servers_handler")[0]
    proj = _rows(plugin, name="anything")[0]
    assert plugin.is_row_protected(glob) is True
    assert plugin.is_row_protected(proj) is False


# --- delete commits (#3) ------------------------------------------------------

def test_delete_actually_removes_the_rows(plugin):
    """The session helper is closing(...) with no commit, so without one the
    RPC reports ids deleted that survive the rollback."""
    first = _insert(plugin, name="a", rpc_func="a")
    second = _insert(plugin, name="b", rpc_func="b")

    assert sorted(plugin.delete_schedules([first, second])) == sorted([first, second])
    assert _rows(plugin) == []


def test_delete_refuses_an_owned_row_and_removes_the_rest(plugin):
    _own(plugin)
    owned = _insert(plugin, name="index_scheduling", rpc_func=HANDLER)
    other = _insert(plugin, name="b", rpc_func="b")

    assert plugin.delete_schedules([owned, other]) == [other]
    assert [row.id for row in _rows(plugin)] == [owned]


# --- canonical ordering (#4) --------------------------------------------------

def test_the_cadence_lands_on_the_oldest_owned_row(plugin):
    """Reversed, the newest row takes the cadence while the canonical one is
    parked -- index scheduling silently stops."""
    _own(plugin)
    oldest = _insert(plugin, name="index_scheduling", rpc_func=HANDLER)
    newest = _insert(plugin, name="index_scheduling", rpc_func=HANDLER)

    plugin.update_schedule("index_scheduling", cron="*/15 * * * *", active=True)

    by_id = {row.id: row for row in _rows(plugin)}
    assert by_id[oldest].cron == "*/15 * * * *" and by_id[oldest].active is True
    assert by_id[newest].active is False


def test_a_project_row_never_receives_the_platform_cadence(plugin):
    """Unscoped, a project schedule of the same name captures the lookup."""
    _own(plugin)
    project = _insert(
        plugin, name="index_scheduling", rpc_func=HANDLER,
        project_id=2, cron="*/40 * * * *",
    )
    global_row = _insert(plugin, name="index_scheduling", rpc_func=HANDLER)

    plugin.update_schedule("index_scheduling", cron="*/15 * * * *")

    by_id = {row.id: row for row in _rows(plugin)}
    assert by_id[project].cron == "*/40 * * * *"
    assert by_id[global_row].cron == "*/15 * * * *"


def test_make_active_picks_the_oldest_and_refuses_owned_names(plugin):
    _own(plugin)
    _insert(plugin, name="index_scheduling", rpc_func=HANDLER, active=True)
    assert plugin.make_active("index_scheduling", False) is False
    assert _rows(plugin)[0].active is True

    oldest = _insert(plugin, name="free", rpc_func="free", active=False)
    _insert(plugin, name="free", rpc_func="free", active=False)
    assert plugin.make_active("free", True) is True
    by_id = {row.id: row for row in _rows(plugin, name="free")}
    assert by_id[oldest].active is True


# --- the call site, not the helper (#1, #3) ----------------------------------

def test_a_foreign_handler_under_an_owned_name_is_not_reported_owned(plugin):
    """Without the row's handler at this call site it reports owned: locked,
    delete-refused, and classified REGISTERED by cleanup -- immortal."""
    _own(plugin)
    _insert(plugin, name="index_scheduling", rpc_func="some_other_registered_rpc")
    row = _rows(plugin)[0]

    assert plugin.managed_binding_for(row) is None
    assert plugin.is_row_protected(row) is False
    assert plugin.delete_schedules([row.id]) == [row.id]
    assert _rows(plugin) == []


def test_make_active_never_reaches_a_project_row(plugin):
    """Unscoped, the lower-id project row captures the lookup and the global
    schedule is never switched on."""
    project = _insert(
        plugin, name="free", rpc_func="free", project_id=2, active=False,
    )
    global_row = _insert(plugin, name="free", rpc_func="free", active=False)

    assert plugin.make_active("free", True) is True

    by_id = {row.id: row for row in _rows(plugin)}
    assert by_id[global_row].active is True
    assert by_id[project].active is False


# --- collection keeps going past a bad plugin (#2) ---------------------------

def test_one_raising_plugin_does_not_cost_the_others_their_bindings(plugin):
    """Readiness is set regardless, so an aborted collection leaves the later
    owners' rows reading unmanaged -- editable, and still overwritten."""
    def _boom():
        raise RuntimeError("this plugin is broken")

    plugin.context.module_manager.descriptors = {
        "broken": types.SimpleNamespace(
            module=types.SimpleNamespace(get_managed_schedules=_boom),
        ),
        "elitea_core": types.SimpleNamespace(
            module=types.SimpleNamespace(get_managed_schedules=lambda: {
                "index_scheduling": {"managed_by": BINDING, "rpc_func": HANDLER},
            }),
        ),
    }

    plugin.collect_managed_schedules()

    assert "index_scheduling" in plugin.managed_schedules
    assert plugin.managed_schedules_ready is True


# --- refusals must raise, not return (#5, #6) --------------------------------

def test_an_ambiguous_name_raises_rather_than_reporting_no_change(plugin):
    """The caller reads False as "nothing needed changing", so a return there
    loses the cadence with only a log line to show for it."""
    _insert(plugin, name="mystery", rpc_func="a")
    _insert(plugin, name="mystery", rpc_func="b")

    with pytest.raises(Exception):
        plugin.update_schedule("mystery", cron="*/15 * * * *")

    assert {row.cron for row in _rows(plugin)} == {"* * * * *"}


def test_no_row_on_the_expected_handler_raises_and_writes_nothing(plugin):
    """Falling back to some row would push the managed cadence onto whatever
    happens to share the name."""
    _own(plugin)
    _insert(plugin, name="index_scheduling", rpc_func="renamed_by_hand",
            cron="*/40 * * * *")

    with pytest.raises(Exception):
        plugin.update_schedule("index_scheduling", cron="*/15 * * * *")

    assert _rows(plugin)[0].cron == "*/40 * * * *"


# --- collection survives a malformed binding (#4) ----------------------------

def test_a_malformed_binding_does_not_cost_the_later_plugins_theirs(plugin):
    """Registration raises on a binding with no handler; narrowing the guard to
    the collect() call alone lets that abort the loop."""
    plugin.context.module_manager.descriptors = {
        "broken": types.SimpleNamespace(
            module=types.SimpleNamespace(
                get_managed_schedules=lambda: {"bad": {"managed_by": BINDING}},
            ),
        ),
        "elitea_core": types.SimpleNamespace(
            module=types.SimpleNamespace(get_managed_schedules=lambda: {
                "index_scheduling": {"managed_by": BINDING, "rpc_func": HANDLER},
            }),
        ),
    }

    plugin.collect_managed_schedules()

    assert "index_scheduling" in plugin.managed_schedules
    assert "bad" not in plugin.managed_schedules
