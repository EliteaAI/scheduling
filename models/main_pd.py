from datetime import datetime
from typing import Optional, List

from pydantic.v1 import BaseModel, constr
from pydantic.v1.class_validators import validator

from croniter import croniter

from ...shared.models.pd.test_parameters import TestParameter  # todo: try to find workaround for this import
from .schedule import Schedule

from abc import ABC, abstractmethod
from tools import db


class SchedulePutModel(BaseModel):
    name: Optional[str] = None
    cron: Optional[str] = None
    active: Optional[bool] = None
    rpc_kwargs: Optional[dict] = None

    @validator('cron')
    def validate_cron(cls, value: str):
        assert croniter.is_valid(value), 'Cron expression is invalid'
        return value


class BaseScheduleModel(SchedulePutModel, ABC):
    id: Optional[int] = None
    name: constr(min_length=1)
    cron: str
    active: bool = True

    @abstractmethod
    def save(self) -> int:
        raise NotImplementedError


class ScheduleModelPD(BaseScheduleModel):
    rpc_func: str
    rpc_kwargs: dict = {}
    last_run: Optional[datetime] = None

    class Config:
        orm_mode = True

    def save(self) -> int:
        with db.with_project_schema_session(None) as session:
            if self.id:
                session.query(Schedule).where(
                    Schedule.id == self.id
                ).update(
                    self.dict(exclude_unset=True, exclude={'id', 'last_run'})
                )
            else:
                db_obj = Schedule(**self.dict(exclude_unset=True, exclude={'id', 'last_run'}))
                session.add(db_obj)
                session.commit()
                self.id = db_obj.id
            return self.id


class BaseTestScheduleModel(BaseScheduleModel, ABC):
    project_id: Optional[int] = None
    test_id: Optional[int] = None
    test_params: List[TestParameter]

    @staticmethod
    @abstractmethod
    def _rpc_func():
        raise NotImplementedError

    @property
    def _db_schedule(self):
        return Schedule(
            project_id=self.project_id,
            name=self.name,
            cron=self.cron,
            active=self.active,
            rpc_func=self._rpc_func,
            rpc_kwargs={
                'test_id': self.test_id,
                'test_params': [i.dict() for i in self.test_params],
            }
        )

    def save(self) -> int:
        assert self.test_id, 'Test id is required'
        with db.with_project_schema_session(None) as session:
            if self.id:
                session.query(Schedule).where(
                    Schedule.id == self.id
                ).update(
                    self._db_schedule.to_json(exclude_fields=('id', 'last_run',))
                )
            else:
                db_obj = self._db_schedule
                session.add(db_obj)
                session.commit()
                self.id = db_obj.id
            return self.id

    @classmethod
    def from_orm(cls, db_obj: Schedule):
        return cls(
            project_id=db_obj.project_id,
            name=db_obj.name,
            cron=db_obj.cron,
            active=db_obj.active,
            test_params=db_obj.rpc_kwargs.get('test_params', []),
            id=db_obj.id,
            test_id=db_obj.rpc_kwargs.get('test_id'),
        )
