from flask import request
from pylon.core.tools import log

from tools import api_tools, db

from pydantic.v1 import ValidationError
from ...models.main_pd import SchedulePutModel
from ...models.schedule import Schedule
from ...utils.managed_schedules import build_managed_conflict
from tools import auth

RUN_NOW_TIMEOUT = 30


class ProjectAPI(api_tools.APIModeHandler):
    @auth.decorators.check_api(["configuration.scheduling.schedules.view"])
    def get(self, project_id: int, **kwargs):
        schedules = [i.to_json() for i in Schedule.query.filter(Schedule.project_id == project_id).all()]
        return {'total': len(schedules), 'rows': schedules}, 200

    @auth.decorators.check_api(["configuration.scheduling.schedules.edit"])
    def put(self, project_id: int, **kwargs):
        schedule_id = request.json.pop('id')
        try:
            data = SchedulePutModel.parse_obj(request.json)
        except ValidationError as e:
            return e.errors(), 400
        # log.info('UPD %s', data.dict(exclude_unset=True))

        with db.with_project_schema_session(None) as session:
            session.query(Schedule).where(
                Schedule.project_id == project_id,
                Schedule.id == schedule_id
            ).update(data.dict(exclude_unset=True))
            session.commit()
        return None, 204


class AdminAPI(api_tools.APIModeHandler):
    @auth.decorators.check_api(["configuration.scheduling.schedules.view"])
    def get(self, project_id: int, **kwargs):
        schedules = []
        for schedule in Schedule.query.all():
            row = schedule.to_json()
            row['managed_by'] = self.module.managed_binding_for(schedule)
            schedules.append(row)
        return {'total': len(schedules), 'rows': schedules}, 200

    @auth.decorators.check_api(["configuration.scheduling.schedules.edit"])
    def put(self, **kwargs):
        schedule_id = request.json.pop('id')
        try:
            data = SchedulePutModel.parse_obj(request.json)
        except ValidationError as e:
            return e.errors(), 400
        # log.info('UPD %s', data.dict(exclude_unset=True))

        changes = data.dict(exclude_unset=True)

        with db.with_project_schema_session(None) as session:
            schedule = session.query(Schedule).where(
                Schedule.id == schedule_id
            ).first()
            if not schedule:
                return {'error': f'Schedule {schedule_id} not found'}, 404

            managed_by = self.module.managed_binding_for(schedule)
            if managed_by is not None:
                log.info(
                    'Refused managed schedule edit: name=%s fields=%s',
                    schedule.name, sorted(changes),
                )
                return build_managed_conflict(schedule.name, managed_by), 409

            session.query(Schedule).where(
                Schedule.id == schedule_id
            ).update(changes)
            session.commit()
        return None, 204

    @auth.decorators.check_api(["configuration.scheduling.schedules.edit"])
    def post(self, **kwargs):
        schedule_id = (request.json or {}).get('id')
        if schedule_id is None:
            return {'error': 'id is required'}, 400
        schedule = Schedule.query.filter(Schedule.id == schedule_id).first()
        if not schedule:
            return {'error': f'Schedule {schedule_id} not found'}, 404
        if schedule.run_now(timeout=RUN_NOW_TIMEOUT):
            return {'ok': True, 'last_run': schedule.last_run.isoformat()}, 200
        return {'error': f'{schedule.rpc_func} did not respond within {RUN_NOW_TIMEOUT}s'}, 504


class API(api_tools.APIBase):
    url_params = [
        '<string:project_id>',
        '<string:mode>/<string:project_id>'
    ]

    mode_handlers = {
        'default': ProjectAPI,
        'administration': AdminAPI
    }
