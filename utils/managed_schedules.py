"""Schedules whose configuration is owned by another plugin's admin settings.

The owning plugin re-pushes cron and active onto the row at every boot and
reconfig, so an edit made anywhere else survives only until the next restart.
"""


# Stands in for a real binding while ownership is still being resolved, so
# the admin surfaces lock rather than hand out controls they will then refuse.
# No section, so the UI renders the lock without a link it cannot follow.
PENDING_BINDING = {'section': None, 'fields': []}


def resolve_managed_binding(managed_schedules, name, project_id, rpc_func=None):
    """Find the configuration that owns this particular row, if any.

    Identity is the name, the project scope and the handler together, because
    the name alone is not unique. A project is free to create a schedule called
    ``index_scheduling``, and that row belongs to the project. A global row
    calling something other than what the owner registered is a stray that
    merely borrows the name -- claiming it would make it immortal: parked by
    reconciliation, yet skipped by orphan cleanup, refused by delete and 409'd
    by the admin API, leaving SQL as the only way out.

    Every decision about whether a row is managed goes through here, so the
    listing cannot disagree with what the write paths enforce. Registration
    requires a handler, so an entry that exists always knows which row it
    means; ``rpc_func`` is optional only for callers with no row in hand.
    """
    if project_id is not None:
        return None
    entry = managed_schedules.get(name)
    if entry is None:
        return None
    if rpc_func is not None and rpc_func != entry['rpc_func']:
        return None
    return entry['managed_by']


def resolve_managed_handler(managed_schedules, name):
    """The handler the owning plugin expects this schedule to call."""
    entry = managed_schedules.get(name)
    return entry['rpc_func'] if entry else None


def is_config_managed(managed_schedules, name, project_id, rpc_func=None):
    """Whether a plugin's admin configuration owns this particular row."""
    return resolve_managed_binding(
        managed_schedules, name, project_id, rpc_func,
    ) is not None


def is_managed_name(managed_schedules, name):
    """Whether any configuration owns schedules under this name.

    For callers that identify a schedule by name alone and so cannot say which
    row they mean. Picking one and checking it would examine a namesake on a
    foreign handler and pass, leaving the owned row untouched and unreported.
    """
    return name in managed_schedules


def plan_reconciliation(schedules, rpc_func):
    """Which row to drive, and which to park, oldest first.

    With a handler to match on, a row that merely borrows the name calls
    something else entirely: neither a candidate to drive nor a duplicate to
    park. Parking it would force its Active flag off on every reconcile while
    the listing reports it unmanaged and the tab offers a live switch -- an
    operator setting it and finding it unset after a restart is the very
    complaint this exists to fix.

    Without one -- an ordinary schedule, or a replica whose registry has not
    been populated yet -- nothing here owns the name, so the caller has
    expressed no opinion about duplicates and none are parked. Deactivating
    rows on a name match alone is the same mistake from the other side.

    Orders its own input rather than trusting the caller's: the query behind it
    has no ORDER BY, Postgres heap order reshuffles after an UPDATE, and the
    cleanup planner sorts separately -- so a caller-side sort lets the two
    disagree about which row is canonical and one sweep deletes the row the
    other is driving.
    """
    ordered = sorted(schedules, key=lambda item: item.id)
    if rpc_func is None:
        return (ordered[0] if ordered else None), []
    driven = [item for item in ordered if item.rpc_func == rpc_func]
    if not driven:
        return None, []
    return driven[0], driven[1:]


CANONICAL = 'canonical'
SURPLUS = 'surplus'
REGISTERED = 'registered'
ORPHAN = 'orphan'

# The verdicts that mean "remove this row". Kept beside them so the caller
# cannot quietly disagree about which ones delete.
ORPHANING_VERDICTS = frozenset({SURPLUS, ORPHAN})

VERDICT_REASONS = {
    CANONICAL: 'config-managed, skipping',
    SURPLUS: 'surplus duplicate of a config-managed schedule -- ORPHANED',
    REGISTERED: 'registered, skipping',
    ORPHAN: 'NOT registered -- ORPHANED',
}


def classify_schedule(schedule, canonical_ids, managed_schedules, registered_rpcs):
    """What the orphan cleanup should do with one row.

    Split out because ``SURPLUS`` means *delete this*, and expressed inline it
    reads almost identically to the checks that mean *protect this*. An
    unmanaged name resolves to no handler, so the comparison must be equality:
    inverted, every ordinary schedule becomes surplus and one non-dry run
    empties the table.
    """
    if schedule.id in canonical_ids:
        return CANONICAL
    if schedule.project_id is None and schedule.rpc_func == resolve_managed_handler(
            managed_schedules, schedule.name,
    ):
        return SURPLUS
    if schedule.rpc_func in registered_rpcs:
        return REGISTERED
    return ORPHAN


def plan_cleanup(schedules, managed_schedules, registered_rpcs):
    """Rows the orphan cleanup should delete.

    The whole decision, not just the per-row verdict: which row a configuration
    drives is what makes every *other* row of that name surplus, so grouping
    and canonical selection cannot be left to the caller. Dropped or inverted
    there, the two config-owned rows classify as surplus and one non-dry run
    deletes exactly the rows this exists to protect.
    """
    by_name = {}
    for schedule in schedules:
        if schedule.project_id is None:
            by_name.setdefault(schedule.name, []).append(schedule)

    canonical_ids = set()
    for name, rows in by_name.items():
        expected = resolve_managed_handler(managed_schedules, name)
        if expected is None:
            continue
        canonical, _ = plan_reconciliation(rows, expected)
        if canonical is not None:
            canonical_ids.add(canonical.id)

    return [
        (schedule, classify_schedule(
            schedule, canonical_ids, managed_schedules, registered_rpcs,
        ))
        for schedule in schedules
    ]


def build_managed_conflict(name, managed_by):
    """Response body telling the caller where this schedule is really edited.

    Every field is refused, not just cron/active: renaming the row would
    detach it from the configuration that keeps re-pushing its cadence.
    """
    return {
        'error': (
            f"Schedule '{name}' is managed by the platform configuration "
            f"and cannot be edited here"
        ),
        'managed_by': managed_by,
    }
