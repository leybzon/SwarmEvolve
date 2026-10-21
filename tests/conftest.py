"""Suite-wide test configuration.

``scripts/fitness.py`` defaults to the sandbox backend so that LLM-written
code only ever runs in the eval container. The suite's fixtures are
trusted repo files, so it runs them on the host unless a test asks for the
sandbox explicitly (``backend="sandbox"``). ``setdefault`` lets a caller
force the sandbox for the whole suite with
``SWARMEVOLVE_FITNESS_BACKEND=sandbox``; subprocesses inherit the value.
"""

from __future__ import annotations

import os

os.environ.setdefault("SWARMEVOLVE_FITNESS_BACKEND", "host")
