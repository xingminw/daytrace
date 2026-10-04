"""Shared event timezone; legacy default retained for existing installations."""
import os
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo(os.environ.get("DAYTRACE_TIMEZONE", "America/Detroit"))
