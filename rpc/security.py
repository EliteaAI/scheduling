from typing import List
from pydantic.v1 import parse_obj_as

from ..models.schedule import Schedule
from ..models.security_pd import SecurityScheduleModel

from pylon.core.tools import web


class RPC:

    @web.rpc('security_test_create_scheduling')
    def test_create(self, data: list, skip_validation_if_undefined: bool = True, **kwargs) -> dict:
        '''
            Used for validating section "SCHEDULING"
        '''
        scheduling_data = parse_obj_as(List[SecurityScheduleModel], data)
        return {'scheduling': [i.dict() for i in scheduling_data]}

    @web.rpc('scheduling_security_create_schedule')
    def create_schedule(self, data: dict) -> int:
        pd_obj = SecurityScheduleModel(**data)
        schedule_id = pd_obj.save()
        return schedule_id

    @web.rpc('scheduling_security_load_from_db_by_ids')
    def load_from_db_by_ids(self, ids: List[int]) -> List[dict]:
        if not ids:
            return []
        return [
            SecurityScheduleModel.from_orm(s).dict()
            for s in
            Schedule.query.filter(Schedule.id.in_(ids)).all()
        ]
