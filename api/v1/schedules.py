from flask import request
from pylon.core.tools import log

from tools import api_tools, db

from pydantic.v1 import ValidationError
from ...models.main_pd import SchedulePutModel
from ...models.schedule import Schedule
from tools import auth


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
        schedules = [i.to_json() for i in Schedule.query.all()]
        return {'total': len(schedules), 'rows': schedules}, 200

    @auth.decorators.check_api(["configuration.scheduling.schedules.edit"])
    def put(self, **kwargs):
        schedule_id = request.json.pop('id')
        try:
            data = SchedulePutModel.parse_obj(request.json)
        except ValidationError as e:
            return e.errors(), 400
        # log.info('UPD %s', data.dict(exclude_unset=True))

        with db.with_project_schema_session(None) as session:
            session.query(Schedule).where(
                Schedule.id == schedule_id
            ).update(data.dict(exclude_unset=True))
            session.commit()
        return None, 204


class API(api_tools.APIBase):
    url_params = [
        '<string:project_id>',
        '<string:mode>/<string:project_id>'
    ]

    mode_handlers = {
        'default': ProjectAPI,
        'administration': AdminAPI
    }
