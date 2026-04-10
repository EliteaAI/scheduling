from typing import List

from pydantic.v1 import AnyUrl

from .main_pd import BaseTestScheduleModel
from ...shared.models.pd.test_parameters import TestParameter


class SecurityScheduleTestParam(TestParameter):
    class Config:
        anystr_lower = True
    _type_mapping_by_name = {'url to scan': List[AnyUrl]}


class SecurityScheduleModel(BaseTestScheduleModel):
    _rpc_func = 'security_run_scheduled_test'
    test_params: List[SecurityScheduleTestParam]

