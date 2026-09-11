#!/usr/bin/env python3
"""
Test runner that installs Pylon stubs before pytest.

Usage:
    python tests/run_tests.py [pytest args...]

Examples:
    python tests/run_tests.py -v
    python tests/run_tests.py -m unit -v
    python tests/run_tests.py unit/test_6556_managed_schedule_guard.py -v
"""
import sys
import types
from pathlib import Path


def install_stubs():
    """Install minimal stubs to prevent ImportError on pylon/tools imports."""

    pylon_stub = types.ModuleType('pylon')
    pylon_core = types.ModuleType('pylon.core')
    pylon_core_tools = types.ModuleType('pylon.core.tools')

    class StubLog:
        @staticmethod
        def info(*a, **kw): pass
        @staticmethod
        def debug(*a, **kw): pass
        @staticmethod
        def warning(*a, **kw): pass
        @staticmethod
        def error(*a, **kw): pass
        @staticmethod
        def critical(*a, **kw): pass

    pylon_core_tools.log = StubLog()
    pylon_core_tools.module = types.ModuleType('pylon.core.tools.module')
    pylon_core_tools.web = types.ModuleType('pylon.core.tools.web')

    sys.modules.setdefault('pylon', pylon_stub)
    sys.modules.setdefault('pylon.core', pylon_core)
    sys.modules.setdefault('pylon.core.tools', pylon_core_tools)
    sys.modules.setdefault('pylon.core.tools.log', pylon_core_tools.log)
    sys.modules.setdefault('pylon.core.tools.module', pylon_core_tools.module)
    sys.modules.setdefault('pylon.core.tools.web', pylon_core_tools.web)

    tools_stub = types.ModuleType('tools')
    tools_stub.db = types.ModuleType('tools.db')
    tools_stub.db_tools = types.ModuleType('tools.db_tools')
    tools_stub.config = types.ModuleType('tools.config')
    tools_stub.auth = types.ModuleType('tools.auth')

    sys.modules.setdefault('tools', tools_stub)
    sys.modules.setdefault('tools.db', tools_stub.db)
    sys.modules.setdefault('tools.db_tools', tools_stub.db_tools)
    sys.modules.setdefault('tools.config', tools_stub.config)
    sys.modules.setdefault('tools.auth', tools_stub.auth)


def main():
    install_stubs()

    import pytest
    tests_dir = Path(__file__).parent
    sys.exit(pytest.main([str(tests_dir)] + sys.argv[1:]))


if __name__ == '__main__':
    main()
