"""The exit-code contract — `HANDOFF §8.4`.

`meridian gate` is the only command that returns non-zero for a *product*
verdict. Everywhere else, non-zero means the tool broke. CI must never confuse
"the agent got worse" with "the harness crashed", because the response to each is
completely different: one is a code review, the other is a page.
"""

from __future__ import annotations

from typing import Final

OK: Final = 0
GATE_FAIL: Final = 1  # reserved exclusively for `meridian gate` returning FAIL
HARNESS_ERROR: Final = 2  # the tool could not do its job
