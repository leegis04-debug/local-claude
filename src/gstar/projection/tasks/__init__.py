"""Projection 트랙 플러그인.

import 만 해도 register 데코레이터가 작동해 `get_task()` 로 접근 가능.
"""

from gstar.projection.tasks.base import (  # noqa: F401
    TaskDefinition,
    CoherenceRule,
    IngestHook,
    get_task,
    list_tasks,
    register,
)
from gstar.projection.tasks import proposal as _proposal  # noqa: F401
from gstar.projection.tasks import research as _research  # noqa: F401
from gstar.projection.tasks import coding as _coding  # noqa: F401
from gstar.projection.tasks import document as _document  # noqa: F401
