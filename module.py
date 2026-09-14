#!/usr/bin/python3
# coding=utf-8

#   Copyright 2026 EPAM Systems
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

""" Module """
import time
from datetime import datetime
from functools import partial
from queue import Empty
from threading import Thread
from traceback import format_exc

from pylon.core.tools import log, web  # pylint: disable=E0611,E0401
from pylon.core.tools import module  # pylint: disable=E0611,E0401
from pylon.core.tools import db_support  # pylint: disable=E0611,E0401

from .init_db import init_db

from tools import VaultClient
from tools import config as c
from tools import this

from .models.schedule import Schedule
from .utils.managed_schedules import (
    PENDING_BINDING,
    is_managed_name,
    resolve_managed_binding,
)


class Module(module.ModuleModel):
    """ Task module """

    def __init__(self, context, descriptor):
        self.context = context
        self.descriptor = descriptor
        self.thread = None
        self.managed_schedules = {}
        self._managed_owners = {}
        self.managed_schedules_collected = False

    def init(self):
        """ Init module """
        log.info("Initializing module")

        init_db()

        self.descriptor.init_blueprint()

        self.descriptor.init_rpcs()

        self.descriptor.init_methods()

        # self.context.slot_manager.register_callback('security_scheduling_test_create', render_security_test_create)
        self.descriptor.init_slots()

        if c.ARBITER_RUNTIME == "rabbitmq":
            self.create_rabbit_schedule()

        self.create_retention_schedules()

        self.descriptor.init_api()
        self.init_ui()

        self.thread = Thread(
            target=partial(
                self.execute_schedules,
                self.descriptor.config['task_poll_period'],
                self.descriptor.config['debug'],
            )
        )
        self.thread.daemon = True
        self.thread.name = 'scheduling_thread'

    def ready(self):
        """ Ready callback """
        self.collect_managed_schedules()

        log.info("Starting scheduling thread")
        self.thread.start()
        try:
            this.for_module("admin").module.register_admin_task(
                "cleanup_orphaned_schedules",
                self.cleanup_orphaned_schedules,
            )
        except Exception:  # pylint: disable=W0703
            log.exception("Failed to register scheduling admin tasks")

    def collect_managed_schedules(self) -> None:
        """ Rebuild the managed-schedule registry from the owning plugins """
        try:
            descriptors_snapshot = list(
                self.context.module_manager.descriptors.items()
            )
            for name, descriptor in descriptors_snapshot:
                module = getattr(descriptor, "module", None)
                collect = getattr(module, "get_managed_schedules", None)
                if collect is None:
                    continue
                try:
                    self.register_managed_schedules(name, collect())
                except Exception:  # pylint: disable=W0703
                    log.exception("Failed to collect managed schedules from %s", name)
        finally:
            self.managed_schedules_collected = True

    def managed_binding_for(self, schedule):
        """ The configuration owning this row, or a marker while unresolved """
        if schedule.project_id is None and not self.managed_schedules_collected:
            return PENDING_BINDING
        return resolve_managed_binding(
            self.managed_schedules, schedule.name,
            schedule.project_id, schedule.rpc_func,
        )

    def is_row_protected(self, schedule) -> bool:
        return self.managed_binding_for(schedule) is not None

    def is_name_protected(self, name: str) -> bool:
        return is_managed_name(self.managed_schedules, name)

    def register_managed_schedules(self, owner: str, bindings: dict) -> None:
        """ Declare which schedules a plugin's admin configuration owns """
        replacement = {
            name: {
                'managed_by': entry['managed_by'],
                'rpc_func': entry['rpc_func'],
            }
            for name, entry in bindings.items()
        }
        for name in self._managed_owners.get(owner, set()) - set(replacement):
            self.managed_schedules.pop(name, None)
            log.info("Managed schedule released: name=%s owner=%s", name, owner)
        self._managed_owners[owner] = set(replacement)
        for name, entry in replacement.items():
            self.managed_schedules[name] = entry
            log.info(
                "Managed schedule registered: name=%s owner=%s rpc_func=%s",
                name, owner, entry['rpc_func'],
            )

    def deinit(self):  # pylint: disable=R0201
        """ De-init module """
        log.info("De-initializing")

    @staticmethod
    def execute_schedules(poll_period: int = 60, debug=False):
        from .models.schedule import Schedule
        from tools import db
        while True:
            try:
                db_support.create_local_session()
                try:
                    time.sleep(poll_period)
                    #
                    if debug:
                        log.info(f'Running schedules... with poll_period {poll_period}')
                    #
                    retrieval_started = time.monotonic()
                    log.info(f'Schedules retrieval started at {datetime.utcnow().isoformat()}Z')
                    with db.with_project_schema_session(None) as session:
                        schedules = session.query(Schedule).filter(Schedule.active == True).all()
                        log.info(
                            f'Schedules retrieved: count={len(schedules)} '
                            f'in {time.monotonic() - retrieval_started:.3f}s'
                        )
                        for sc in schedules:
                            try:
                                sc.run(debug)
                                session.commit()
                            except Exception as e:
                                log.critical(e)
                    log.info(
                        f'Schedules retrieval finished at {datetime.utcnow().isoformat()}Z '
                        f'(total {time.monotonic() - retrieval_started:.3f}s)'
                    )
                except:  # pylint: disable=W0702
                    log.exception("Error in scheduler loop, continuing in 5 seconds")
                    time.sleep(5)
                finally:
                    db_support.close_local_session()
            except:  # pylint: disable=W0702
                log.exception("Critical error in scheduler loop, continuing in 15 seconds")
                time.sleep(15)

    def create_rabbit_schedule(self) -> dict:
        pd = self.create_if_not_exists({
            'name': 'rabbit_queue_schedule',
            'cron': '*/10 * * * *',
            'rpc_func': 'tasks_check_rabbit_queues'
        })
        return pd.dict()

    def create_retention_schedules(self):
        for i in self.descriptor.config.get('results_retention_plugins', []):
            try:
                data = self.context.rpc_manager.call_function_with_timeout(
                    func=f'{i}_get_retention_schedule_data',
                    timeout=2,
                )
                log.info('Got retention schedule data from %s : %s', i, data)
                try:
                    self.create_if_not_exists(data)
                except:
                    log.critical('Failed creating retention schedule\n%s', format_exc())
            except Empty:
                ...

    def init_ui(self):
        from tools import auth  # pylint: disable=E0401,C0415

        auth.register_permissions({
            "permissions": ["configuration.scheduling"],
            "recommended_roles": {
                "administration": {"admin": True, "viewer": True, "editor": True},
                "default": {"admin": True, "viewer": True, "editor": True},
            }
        })
