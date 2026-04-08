"""
agents/base.py
==============
Abstract base class for all pipeline subagents.

Architecture
------------
Each processing step is encapsulated in its own Agent class.  Agents are
single-responsibility workers that receive typed inputs and return typed
outputs.  The orchestrator (``agents/orchestrator.py``) wires them together,
calling ``validate()`` before ``run()`` on each agent.

Agent lifecycle
---------------
  1. Instantiation  — inject optional configuration via ``__init__``
  2. ``validate()`` — pre-flight checks; returns a ``ValidationResult``
                      (raises ``ConfigurationError`` via ``raise_if_error()``)
  3. ``run(input)`` — public entry point; wraps ``_execute()`` with timing,
                      structured logging and error normalisation
  4. ``_execute()`` — subclasses implement ONLY this method
  5. Properties     — ``last_duration_s``, ``run_count``, ``last_error``

Design rationale
----------------
- A single ``run()`` implementation in the base class ensures every agent gets
  identical timing, logging and exception handling — no duplication.
- ``validate()`` returns a ``ValidationResult`` (not None) so the orchestrator
  can inspect warnings without catching exceptions.
- Generic[InputT, OutputT] gives type-safe orchestration without sacrificing
  flexibility: each agent declares its own contract via the type parameters.
- ``MockAgent`` at the bottom of this module makes unit-testing the orchestrator
  trivial without instantiating real agents.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Generic, Optional, TypeVar

from core.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

InputT  = TypeVar("InputT")
OutputT = TypeVar("OutputT")


# ---------------------------------------------------------------------------
# Validation result — returned by validate() so callers can inspect results
# ---------------------------------------------------------------------------

class ValidationStatus(Enum):
    OK      = auto()
    WARNING = auto()
    ERROR   = auto()


@dataclass
class ValidationResult:
    """
    Structured result of ``BaseAgent.validate()``.

    Attributes
    ----------
    status : ValidationStatus
    messages : list[str]
        Human-readable notes, warnings or error descriptions.
    """
    status: ValidationStatus = ValidationStatus.OK
    messages: list[str] = field(default_factory=list)

    def add_warning(self, msg: str) -> None:
        if self.status != ValidationStatus.ERROR:
            self.status = ValidationStatus.WARNING
        self.messages.append(f"[WARNING] {msg}")

    def add_error(self, msg: str) -> None:
        self.status = ValidationStatus.ERROR
        self.messages.append(f"[ERROR] {msg}")

    @property
    def is_ok(self) -> bool:
        return self.status != ValidationStatus.ERROR

    def raise_if_error(self) -> None:
        """Raise ``ConfigurationError`` if validation did not pass."""
        if not self.is_ok:
            detail = "; ".join(self.messages)
            raise ConfigurationError(detail)


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------

class BaseAgent(ABC, Generic[InputT, OutputT]):
    """
    Abstract base for all pipeline agents.

    Subclasses MUST:
      - Set the class-level ``name`` attribute to a human-readable identifier.
      - Implement ``_execute(input_data)`` with their core logic.

    Subclasses MAY:
      - Override ``validate()`` to perform pre-flight checks.
      - Override ``__init__`` to accept configuration, calling ``super().__init__()``.

    Usage
    -----
    >>> class MyAgent(BaseAgent[MyInput, MyOutput]):
    ...     name = "MyAgent"
    ...     def _execute(self, inp: MyInput) -> MyOutput:
    ...         return MyOutput(value=inp.value * 2)
    """

    #: Override in every subclass — used in logs and error messages.
    name: str = "UnnamedAgent"

    def __init__(self) -> None:
        self._last_duration_s: float = 0.0
        self._run_count: int = 0
        self._last_error: Optional[Exception] = None

    # ------------------------------------------------------------------
    # Public interface — called by the orchestrator
    # ------------------------------------------------------------------

    def validate(self) -> ValidationResult:
        """
        Pre-flight validation hook.

        Returns ``ValidationStatus.OK`` by default.  Subclasses override this
        to check that required files exist, config values are in range, etc.

        Returns
        -------
        ValidationResult
            Call ``result.raise_if_error()`` to convert errors into exceptions.
        """
        return ValidationResult(status=ValidationStatus.OK)

    def run(self, input_data: InputT) -> OutputT:
        """
        Execute the agent.

        Wraps ``_execute()`` with:
          - Structured logging at INFO level on start and completion
          - Wall-clock timing via ``time.perf_counter()``
          - ``run_count`` incremented on every successful execution
          - On failure: logs at ERROR, stores exception, re-raises unchanged

        Parameters
        ----------
        input_data : InputT
            Agent-specific input payload.

        Returns
        -------
        OutputT
            Agent-specific output payload.

        Raises
        ------
        Exception
            Any exception raised inside ``_execute()`` is propagated after logging.
        """
        logger.info("[%s] starting (run #%d)", self.name, self._run_count + 1)
        start = time.perf_counter()
        try:
            result = self._execute(input_data)
            self._last_duration_s = time.perf_counter() - start
            self._run_count += 1
            self._last_error = None
            logger.info(
                "[%s] completed in %.3fs (total runs: %d)",
                self.name, self._last_duration_s, self._run_count,
            )
            return result
        except Exception as exc:
            self._last_duration_s = time.perf_counter() - start
            self._last_error = exc
            logger.error(
                "[%s] failed after %.3fs — %s: %s",
                self.name, self._last_duration_s, type(exc).__name__, exc,
            )
            raise

    # ------------------------------------------------------------------
    # Introspection properties
    # ------------------------------------------------------------------

    @property
    def last_duration_s(self) -> float:
        """Wall-clock seconds consumed by the most recent ``run()`` call."""
        return self._last_duration_s

    @property
    def run_count(self) -> int:
        """Number of *successful* ``run()`` invocations on this instance."""
        return self._run_count

    @property
    def last_error(self) -> Optional[Exception]:
        """The exception from the last failed ``run()``, or ``None``."""
        return self._last_error

    @property
    def has_run(self) -> bool:
        """``True`` if ``run()`` has been called at least once successfully."""
        return self._run_count > 0

    # ------------------------------------------------------------------
    # Abstract — subclasses implement ONLY this
    # ------------------------------------------------------------------

    @abstractmethod
    def _execute(self, input_data: InputT) -> OutputT:
        """
        Core agent logic.

        Called exclusively by ``run()``.  Timing, logging and error wrapping
        are handled by the base class — do not duplicate them here.
        """

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} name={self.name!r} "
            f"runs={self._run_count} last={self._last_duration_s:.3f}s>"
        )


# ---------------------------------------------------------------------------
# MockAgent — for testing the orchestrator without real side effects
# ---------------------------------------------------------------------------

class MockAgent(BaseAgent[InputT, OutputT]):
    """
    Test double for ``BaseAgent``.

    Accepts a fixed return value or a callable at construction time.
    Validates the full ``validate() → run() → _execute()`` lifecycle through
    the base-class machinery without any real I/O.

    Examples
    --------
    >>> agent = MockAgent(name="TestAgent", return_value=42)
    >>> agent.run("any input")
    42
    >>> agent.run_count
    1
    >>> repr(agent)
    "<MockAgent name='TestAgent' runs=1 last=0.000s>"

    >>> failing = MockAgent(name="Failing", side_effect=ValueError("boom"))
    >>> failing.run("x")   # raises ValueError, last_error is set
    """

    def __init__(
        self,
        name: str = "MockAgent",
        return_value: OutputT = None,
        side_effect: Optional[Exception] = None,
        validation_result: Optional[ValidationResult] = None,
    ) -> None:
        super().__init__()
        self.name = name
        self._return_value = return_value
        self._side_effect = side_effect
        self._validation_result = validation_result or ValidationResult()
        #: All inputs received across every ``run()`` call — inspect in tests.
        self.received_inputs: list[InputT] = []

    def validate(self) -> ValidationResult:
        return self._validation_result

    def _execute(self, input_data: InputT) -> OutputT:
        self.received_inputs.append(input_data)
        if self._side_effect is not None:
            raise self._side_effect
        return self._return_value