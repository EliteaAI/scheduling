import time as time_module
from datetime import datetime
from queue import Empty

from pylon.core.tools import log

from sqlalchemy import Integer, Column, String, Boolean, JSON, DateTime
from croniter import croniter

from tools import db, db_tools, rpc_tools


class Schedule(db_tools.AbstractBaseMixin, rpc_tools.RpcMixin, db.Base):
    __tablename__ = 'schedule'

    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=False, nullable=False)
    project_id = Column(Integer, unique=False, nullable=True, default=None)
    cron = Column(String(64), unique=False, nullable=False)
    active = Column(Boolean, default=True)
    rpc_func = Column(String(64), unique=False, nullable=False)
    rpc_kwargs = Column(JSON, nullable=False, default={})
    last_run = Column(DateTime, nullable=True)

    @property
    def time_to_run(self) -> bool:
        if not self.last_run:
            return True
        return croniter(self.cron, self.last_run, datetime).get_next() <= datetime.now()

    @staticmethod
    def _get_tracer():
        """ Get OpenTelemetry tracer if tracing is enabled """
        try:
            from tools import this  # pylint: disable=C0415
            tracing_mod = this.for_module('tracing').module
            if tracing_mod.enabled:
                return tracing_mod.get_tracer()
        except Exception:  # pylint: disable=W0703
            pass
        return None

    def run(self, debug=False):
        if debug:
            log.info('')
            log.info(f'Trying to run schedule {self.id}')
            log.info(f'Is it time_to_run? {self.time_to_run}')
        #
        if self.time_to_run:
            log.debug('Running now: Schedule(id=%s, name=%s)', self.id, self.name)
            tracer = self._get_tracer()
            if tracer:
                self._run_traced(tracer)
            else:
                self._run_untraced()
        #
        if self.last_run:
            next_run = croniter(self.cron, self.last_run, datetime).get_next() - datetime.now()
            #
            if debug:
                log.info('Next run in: [%s: %s] -> [%s]', self.id, self.name, next_run)
        #
        if debug:
            log.info('')

    def _run_traced(self, tracer):
        """ Execute schedule RPC with OTEL tracing span """
        from opentelemetry.trace import SpanKind, Status, StatusCode  # pylint: disable=C0415
        #
        attributes = {
            'telemetry.data_type': 'schedule_execution',
            'schedule.id': self.id,
            'schedule.name': self.name,
            'schedule.cron': self.cron,
            'schedule.rpc_func': self.rpc_func,
        }
        if self.project_id is not None:
            attributes['project.id'] = self.project_id
        #
        start = time_module.perf_counter()
        with tracer.start_as_current_span(
            f"Schedule: {self.name} -> {self.rpc_func}",
            kind=SpanKind.INTERNAL,
            attributes=attributes,
        ) as span:
            try:
                self.rpc.call_function_with_timeout(
                    func=self.rpc_func,
                    timeout=5,
                    **self.rpc_kwargs
                )
                duration_ms = (time_module.perf_counter() - start) * 1000
                span.set_attribute('schedule.duration_ms', duration_ms)
                span.set_status(Status(StatusCode.OK))
                self.last_run = datetime.now()
                self.commit()
            except Empty:
                duration_ms = (time_module.perf_counter() - start) * 1000
                span.set_attribute('schedule.duration_ms', duration_ms)
                span.set_status(Status(StatusCode.ERROR, f'RPC timeout: {self.rpc_func}'))
                log.critical(f'Schedule func failed to run {self.rpc_func}')

    def _run_untraced(self):
        """ Execute schedule RPC without tracing (fallback) """
        try:
            self.rpc.call_function_with_timeout(
                func=self.rpc_func,
                timeout=5,
                **self.rpc_kwargs
            )
            self.last_run = datetime.now()
            self.commit()
        except Empty:
            log.critical(f'Schedule func failed to run {self.rpc_func}')
