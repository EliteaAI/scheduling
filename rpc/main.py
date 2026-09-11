from datetime import datetime
from typing import List, Union
from zoneinfo import ZoneInfo

from ..models.schedule import Schedule
from ..models.main_pd import ScheduleModelPD
from ..utils.managed_schedules import (
    plan_reconciliation,
    resolve_managed_handler,
)

from pylon.core.tools import web, log

from croniter import croniter

from tools import db


class RPC:
    @web.rpc('scheduling_delete_schedules')
    def delete_schedules(self, delete_ids: List[int]) -> List[int]:
        """ Delete schedules by id, refusing the ones a plugin config owns

        Deleting a managed row leaves its configuration pointing at nothing
        until the next successful bootstrap, and no caller has a reason to:
        the owning plugin withdraws a schedule by unregistering it.
        """
        with db.with_project_schema_session(None) as session:
            schedules = session.query(Schedule).where(
                Schedule.id.in_(delete_ids)
            ).all()
            managed = [
                item for item in schedules
                if self.is_row_protected(item)
            ]
            if managed:
                log.warning(
                    "delete_schedules: refusing config-managed ids=%s",
                    [item.id for item in managed],
                )
            deleted_ids = [
                item.id for item in schedules
                if item not in managed
            ]
            if deleted_ids:
                session.query(Schedule).where(
                    Schedule.id.in_(deleted_ids)
                ).delete(synchronize_session=False)
                session.commit()
        return deleted_ids

    @web.rpc('get_schedules')
    def get_schedules(self, session=db.session) -> List[Schedule]:
        return session.query(Schedule).all()

    @web.rpc('scheduling_create_schedule', 'create_schedule')
    def create_schedule(self, schedule_data: Union[dict, ScheduleModelPD]) -> ScheduleModelPD:
        if isinstance(schedule_data, dict):
            pd = ScheduleModelPD.parse_obj(schedule_data)
        else:
            pd = schedule_data
        pd.save()
        return pd

    @web.rpc('scheduling_create_if_not_exists', 'create_if_not_exists')
    def create_if_not_exists(self, schedule_data: dict) -> ScheduleModelPD:
        """ Create a platform schedule unless it already exists

        Scoped to global rows: ``ScheduleModelPD`` carries no project_id, so
        this can only ever create one, and schedule names are not unique. A
        project row of the same name must not be mistaken for it.
        """
        match_handler = bool(schedule_data.get('match_handler'))
        with db.with_project_schema_session(None) as session:
            pd = ScheduleModelPD.parse_obj(schedule_data)
            clauses = [
                Schedule.name == pd.name,
                Schedule.project_id.is_(None),
            ]
            if match_handler:
                # Asked for by the caller rather than read from the registry,
                # so every replica answers the same way. Consulting per-process
                # state here meant a replica whose ready() had not run matched
                # on name alone, declined to create the real row, and left the
                # reconcile on a populated replica raising for a row that never
                # gets made.
                #
                # Only owners ask: for an ordinary schedule the name stays the
                # identity, since adding the handler would insert a second row
                # the day one is renamed.
                clauses.append(Schedule.rpc_func == pd.rpc_func)
            bd_schedule = session.query(Schedule).where(*clauses).first()
            if bd_schedule:
                pd = ScheduleModelPD.from_orm(bd_schedule)
                log.info('Schedule already exists: name=%s id=%s', pd.name, pd.id)
            else:
                pd = self.create_schedule(pd)
                log.info('Schedule created: name=%s id=%s', pd.name, pd.id)
            return pd

    @web.rpc()
    def make_active(self, schedule_name, value=True):
        """ Flip a global schedule's active flag

        Refused for any name a configuration owns, for the same reason delete
        is: flipping one behind the configuration's back leaves the tab showing
        a read-only row that disagrees with the setting driving it. The refusal
        is by name because the caller names a schedule without saying which row
        it means -- picking one and checking it would wave through a namesake on
        a foreign handler while never examining the owned row.

        Returns whether the call was applied. False means the name is owned by
        a configuration and always will be -- not a transient condition to
        retry. While the registry is still being collected nothing is refused,
        so a call landing in that window can flip a config-owned row until the
        next reconcile puts it back; see is_name_protected for why that trade
        is the right way round.
        """
        if self.is_name_protected(schedule_name):
            log.warning(
                "make_active: refusing schedule name=%s (ownership %s)",
                schedule_name,
                "resolved" if self.managed_schedules_ready else "still resolving",
            )
            return False
        with db.with_project_schema_session(None) as session:
            schedule = session.query(Schedule).where(
                Schedule.name == schedule_name,
                Schedule.project_id.is_(None),
            ).order_by(Schedule.id).first()
            if schedule and schedule.active != value:
                schedule.active = value
                session.commit()
            return True

    @web.rpc('scheduling_update_schedule')
    def update_schedule(self, name: str, cron: str = None, active: bool = None) -> bool:
        """Update an existing schedule's cron and/or active flag in place.

        Returns True if any field changed, False otherwise (including row not
        found and invalid cron). Either field can be omitted to leave it
        unchanged.

        Raises when the row to write cannot be identified at all, so the owner
        logs a failure instead of reading a False as "nothing needed changing"
        and leaving its configuration silently unapplied.
        """
        if cron is not None:
            try:
                croniter(cron)
            except Exception as error:
                log.error(
                    "update_schedule: invalid cron=%r name=%s error=%r",
                    cron, name, error,
                )
                return False
        with db.with_project_schema_session(None) as session:
            schedules = session.query(Schedule).where(
                Schedule.name == name,
                Schedule.project_id.is_(None),
            ).order_by(Schedule.id).all()
            if not schedules:
                log.warning("update_schedule: schedule not found name=%s", name)
                return False
            # Names are not unique, so a race or a manual repair can leave
            # duplicates. The scheduler runs every active row independently, so
            # one row becomes canonical and the rest are parked: activating
            # them all would dispatch one tick per duplicate.
            expected = resolve_managed_handler(self.managed_schedules, name)
            if expected is None and len(schedules) > 1:
                # This process does not know who owns the name -- an ordinary
                # schedule, or a replica past init() whose ready() has not run
                # yet, since the RPC is dispatched to an arbitrary node. With
                # more than one candidate there is nothing to choose on, and
                # guessing lands the write on whichever row happens to be
                # oldest.
                # Raised, not returned False: the caller reads that as "no
                # change" and moves on, so a cadence push would vanish with
                # nothing but this line while Runtime shows the new value.
                raise RuntimeError(
                    f"ambiguous schedule name with no registered owner: "
                    f"name={name} ids={[item.id for item in schedules]}"
                )
            schedule, duplicates = plan_reconciliation(schedules, expected)
            if schedule is None:
                # Driving an unrelated row would push this config's cadence
                # onto whatever happens to share the name, every boot.
                raise RuntimeError(
                    f"no schedule row calls the expected handler: name={name} "
                    f"expected={expected} ids={[item.id for item in schedules]}"
                )
            if duplicates:
                log.warning(
                    "update_schedule: parking %s duplicate global row(s) "
                    "name=%s canonical=%s duplicates=%s",
                    len(duplicates), name, schedule.id,
                    [item.id for item in duplicates],
                )
            changed = False
            if cron is not None and schedule.cron != cron:
                schedule.cron = cron
                changed = True
            if active is not None and schedule.active != bool(active):
                schedule.active = bool(active)
                changed = True
            for duplicate in duplicates:
                if duplicate.active:
                    duplicate.active = False
                    changed = True
            if changed:
                session.commit()
                log.info(
                    "update_schedule: name=%s cron=%s active=%s",
                    name, schedule.cron, schedule.active,
                )
            return changed

    @web.rpc('scheduling_time_to_run', 'time_to_run')
    def time_to_run(self, cron: str, last_run: str, timezone: str) -> bool:
        """Determine if it is time to run a scheduled task.

        Assumptions:
        - ``last_run`` is an ISO 8601 datetime string with timezone information.
        - ``timezone`` is a valid IANA timezone string.

        Last run is always stored in UTC, but with explicit timezone offset in the
        string representation. Cron is evaluated in the provided timezone, which
        can be different from the timezone of `last_run` (UTC). All comparisons
        are done in the cron timezone.

        Args:
            cron: Cron expression string.
            last_run: The last run time as an ISO 8601 string with timezone.
            timezone: IANA timezone string for the cron schedule.

        Returns:
            True if the task should run now, False otherwise.
        """
        log.debug(f"time_to_run called with {cron=}, {last_run=}, {timezone=}")

        # Parse last_run string (assumed valid ISO 8601 with tzinfo)
        last_run_dt = datetime.fromisoformat(last_run)

        # Use provided timezone for cron evaluation
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        last_run_in_tz = last_run_dt.astimezone(tz)

        log.debug(f"time_to_run: {cron=}, timezone={timezone}, now={now}, last_run_in_tz={last_run_in_tz}")

        try:
            next_run = croniter(cron, last_run_in_tz, datetime).get_next()
        except Exception as error:  # croniter can raise for invalid expressions
            log.error(f"time_to_run: failed to evaluate cron: {cron=}, last_run_in_tz={last_run_in_tz}, {error=!r}")
            return False

        log.debug(f"time_to_run: next_run={next_run}, now={now}")
        return next_run <= now
