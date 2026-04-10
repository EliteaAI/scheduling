from .main_pd import BaseTestScheduleModel


class PerformanceScheduleModel(BaseTestScheduleModel):
    _rpc_func = 'backend_performance_run_scheduled_test'


class UiPerformanceScheduleModel(BaseTestScheduleModel):
    _rpc_func = 'ui_performance_run_scheduled_test'