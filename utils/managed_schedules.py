"""Schedules whose cron and active flag are owned by another plugin's admin settings."""


PENDING_BINDING = {'section': None, 'fields': []}


def resolve_managed_binding(managed_schedules, name, project_id, rpc_func=None):
    if project_id is not None:
        return None
    entry = managed_schedules.get(name)
    if entry is None:
        return None
    if rpc_func is not None and rpc_func != entry['rpc_func']:
        return None
    return entry['managed_by']


def resolve_managed_handler(managed_schedules, name):
    entry = managed_schedules.get(name)
    return entry['rpc_func'] if entry else None


def is_config_managed(managed_schedules, name, project_id, rpc_func=None):
    return resolve_managed_binding(
        managed_schedules, name, project_id, rpc_func,
    ) is not None


def is_managed_name(managed_schedules, name):
    return name in managed_schedules


def plan_reconciliation(schedules, rpc_func):
    by_age = sorted(schedules, key=lambda item: item.id)
    if rpc_func is None:
        return (by_age[0] if by_age else None), []
    driven = [item for item in by_age if item.rpc_func == rpc_func]
    if not driven:
        return None, []
    return driven[0], driven[1:]


CANONICAL = 'canonical'
SURPLUS = 'surplus'
REGISTERED = 'registered'
ORPHAN = 'orphan'

ORPHANING_VERDICTS = frozenset({SURPLUS, ORPHAN})

VERDICT_REASONS = {
    CANONICAL: 'config-managed, skipping',
    SURPLUS: 'surplus duplicate of a config-managed schedule -- ORPHANED',
    REGISTERED: 'registered, skipping',
    ORPHAN: 'NOT registered -- ORPHANED',
}


def classify_schedule(schedule, canonical_ids, managed_schedules, registered_rpcs):
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
    global_rows_by_name = {}
    for schedule in schedules:
        if schedule.project_id is None:
            global_rows_by_name.setdefault(schedule.name, []).append(schedule)

    canonical_ids = set()
    for name, rows in global_rows_by_name.items():
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
    return {
        'error': (
            f"Schedule '{name}' is managed by the platform configuration "
            f"and cannot be edited here"
        ),
        'managed_by': managed_by,
    }
