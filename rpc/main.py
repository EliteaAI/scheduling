from datetime import datetime
from typing import List, Union
from zoneinfo import ZoneInfo

from ..models.schedule import Schedule
from ..models.main_pd import ScheduleModelPD

from pylon.core.tools import web, log

from croniter import croniter

from tools import db


class RPC:
    @web.rpc('scheduling_delete_schedules')
    def delete_schedules(self, delete_ids: List[int]) -> List[int]:
        with db.with_project_schema_session(None) as session:
            session.query(Schedule).where(Schedule.id.in_(delete_ids)).delete()
        return delete_ids

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
        with db.with_project_schema_session(None) as session:
            pd = ScheduleModelPD.parse_obj(schedule_data)
            bd_schedule = session.query(Schedule).where(Schedule.name == pd.name).first()
            if bd_schedule:
                pd = ScheduleModelPD.from_orm(bd_schedule)
                log.info('Schedule already exists: name=%s id=%s', pd.name, pd.id)
            else:
                pd = self.create_schedule(pd)
                log.info('Schedule created: name=%s id=%s', pd.name, pd.id)
            return pd

    @web.rpc()
    def make_active(self, schedule_name, value=True):
        with db.with_project_schema_session(None) as session:
            schedule = session.query(Schedule).where(Schedule.name == schedule_name).first()
            if schedule and schedule.active != value:
                schedule.active = value
                session.commit()

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
