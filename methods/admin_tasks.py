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

""" Method """

import time

from pylon.core.tools import log  # pylint: disable=E0611,E0401
from pylon.core.tools import web  # pylint: disable=E0611,E0401

from ..models.schedule import Schedule
from ..utils.managed_schedules import (
    ORPHANING_VERDICTS,
    VERDICT_REASONS,
    plan_cleanup,
)


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def cleanup_orphaned_schedules(self, *args, **kwargs):
        """Admin task: remove schedule rows whose RPC function no longer exists.

        Uses the local RPC service registry to check whether each schedule's
        ``rpc_func`` is currently registered. Functions that exist only on a
        remote node would appear missing — this is intentional: a function with
        no local handler is effectively dead in a single-pylon setup.

        This is the one path that removes a config-owned row: a surplus
        duplicate is parked by reconciliation, 409'd by the admin API and
        refused by delete, so nothing else can clear one. The canonical row is
        never touched, and the task refuses to run at all while ownership is
        unresolved.

        Supports an optional ``task=<name>`` param to target a single schedule
        by name instead of scanning all rows.

        Dry-run is ON by default — pass ``dry_run=false`` to actually delete.

        Param format:
            "[task=<schedule_name>][;dry_run=false]"

        Examples:
            ""                                - dry-run scan of all schedules
            "dry_run=false"                   - delete all orphaned schedules
            "task=storage_used_space_check"   - dry-run check of one schedule
            "task=usage_monitor;dry_run=false" - delete one specific schedule
        """
        from tools import db  # pylint: disable=C0415

        param = kwargs.get("param", "") or ""
        dry_run = True
        task_filter = None

        for seg in [s.strip() for s in param.split(";")]:
            seg_lower = seg.lower()
            if seg_lower.startswith("task="):
                task_filter = seg[len("task="):].strip()
            elif seg_lower.startswith("dry_run="):
                dry_run = seg_lower[len("dry_run="):].strip() != "false"

        prefix = "[DRY RUN] " if dry_run else ""
        log.info(
            "%sStarting cleanup_orphaned_schedules (task_filter=%s, dry_run=%s)",
            prefix, task_filter, dry_run,
        )
        start_ts = time.time()

        # Snapshot of locally registered RPC function names — pure dict read, no calls made
        registered_rpcs = set(self.context.rpc_manager.node.service_node.services.keys())
        log.info("%sRegistered RPC functions: %d", prefix, len(registered_rpcs))

        if not self.managed_schedules_ready:
            # This task deletes rows, and while ownership is unresolved every
            # global row looks the same: neither canonical nor clearly a stray.
            log.warning(
                "%sSchedule ownership is still being resolved; refusing to run",
                prefix,
            )
            return

        orphaned = []
        checked = 0

        with db.with_project_schema_session(None) as session:
            query = session.query(Schedule)
            if task_filter:
                query = query.filter(Schedule.name == task_filter)
            schedules = query.all()

            plan = plan_cleanup(
                schedules, self.managed_schedules, registered_rpcs,
            )
            for sc, verdict in plan:
                checked += 1
                log.info(
                    "%sschedule id=%s name=%s rpc_func=%s: %s",
                    prefix, sc.id, sc.name, sc.rpc_func, VERDICT_REASONS[verdict],
                )
            orphaned = [
                {"id": sc.id, "name": sc.name, "rpc_func": sc.rpc_func}
                for sc, verdict in plan if verdict in ORPHANING_VERDICTS
            ]

            if not dry_run and orphaned:
                orphan_ids = [o["id"] for o in orphaned]
                session.query(Schedule).filter(Schedule.id.in_(orphan_ids)).delete(
                    synchronize_session=False
                )
                session.commit()
                log.info(
                    "Deleted %d orphaned schedule(s): %s",
                    len(orphaned), [o["name"] for o in orphaned],
                )

        end_ts = time.time()
        log.info(
            "%sExiting cleanup_orphaned_schedules — checked=%d orphaned=%d %s (duration=%.2fs)",
            prefix, checked, len(orphaned),
            "would delete" if dry_run else "deleted",
            end_ts - start_ts,
        )
        return {
            "dry_run": dry_run,
            "checked": checked,
            "orphaned": orphaned,
        }
