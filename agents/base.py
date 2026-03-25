"""
agents/base.py
==============
Classe base abstrata para todos os subagentes do pipeline.

Arquitetura
-----------
Cada etapa de processamento é encapsulada em sua própria classe Agent. Os agentes
são componentes de responsabilidade única que recebem entradas tipadas e retornam
saídas tipadas. O orquestrador (``agents/orchestrator.py``) conecta esses agentes,
chamando ``validate()`` antes de ``run()`` em cada um.

Ciclo de vida do agente
----------------------
  1. Instanciação  — injeta configuração opcional via ``__init__``
  2. ``validate()`` — verificações pré-execução; retorna um ``ValidationResult``
                      (lança ``ConfigurationError`` via ``raise_if_error()``)
  3. ``run(input)`` — ponto de entrada público; envolve ``_execute()`` com timing,
                      logging estruturado e normalização de erros
  4. ``_execute()`` — subclasses implementam APENAS este método
  5. Propriedades   — ``last_duration_s``, ``run_count``, ``last_error``

Justificativa de design
----------------------
- Uma única implementação de ``run()`` na classe base garante que todos os agentes tenham
  o mesmo comportamento de timing, logging e tratamento de exceções, sem duplicação.
- ``validate()`` retorna um ``ValidationResult`` (não None) para que o orquestrador
  possa inspecionar avisos sem precisar capturar exceções.
- Generic[InputT, OutputT] permite orquestração com tipagem segura sem perder
  flexibilidade: cada agente define seu próprio contrato via parâmetros de tipo.
- ``MockAgent`` no final deste módulo torna o teste do orquestrador simples,
  sem precisar instanciar agentes reais.
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
# Resultado de validação — retornado por validate() para inspeção
# ---------------------------------------------------------------------------

class ValidationStatus(Enum):
    OK      = auto()
    WARNING = auto()
    ERROR   = auto()


@dataclass
class ValidationResult:
    """
    Resultado estruturado de ``BaseAgent.validate()``.

    Atributos
    ----------
    status : ValidationStatus
    messages : list[str]
        Mensagens legíveis contendo avisos, erros ou observações.
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
        """Lança ``ConfigurationError`` se a validação falhar."""
        if not self.is_ok:
            detail = "; ".join(self.messages)
            raise ConfigurationError(detail)


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------

class BaseAgent(ABC, Generic[InputT, OutputT]):
    """
    Classe base abstrata para todos os agentes do pipeline.

    Subclasses DEVEM:
      - Definir o atributo de classe ``name`` com um identificador legível.
      - Implementar ``_execute(input_data)`` com a lógica principal.

    Subclasses PODEM:
      - Sobrescrever ``validate()`` para verificações pré-execução.
      - Sobrescrever ``__init__`` para aceitar configuração, chamando ``super().__init__()``.

    Uso
    -----
    >>> class MyAgent(BaseAgent[MyInput, MyOutput]):
    ...     name = "MyAgent"
    ...     def _execute(self, inp: MyInput) -> MyOutput:
    ...         return MyOutput(value=inp.value * 2)
    """

    #: Sobrescrever em toda subclasse — usado em logs e mensagens de erro.
    name: str = "UnnamedAgent"

    def __init__(self) -> None:
        self._last_duration_s: float = 0.0
        self._run_count: int = 0
        self._last_error: Optional[Exception] = None

    # ------------------------------------------------------------------
    # Interface pública — chamada pelo orquestrador
    # ------------------------------------------------------------------

    def validate(self) -> ValidationResult:
        """
        Hook de validação pré-execução.

        Retorna ``ValidationStatus.OK`` por padrão. Subclasses podem sobrescrever
        para validar arquivos, configurações, etc.

        Returns
        -------
        ValidationResult
            Use ``result.raise_if_error()`` para transformar erros em exceções.
        """
        return ValidationResult(status=ValidationStatus.OK)

    def run(self, input_data: InputT) -> OutputT:
        """
        Executa o agente.

        Envolve ``_execute()`` com:
          - Logging estruturado em nível INFO no início e fim
          - Medição de tempo com ``time.perf_counter()``
          - Incremento de ``run_count`` a cada execução bem-sucedida
          - Em caso de erro: log em nível ERROR, armazena exceção e relança

        Parameters
        ----------
        input_data : InputT
            Dados de entrada específicos do agente.

        Returns
        -------
        OutputT
            Saída específica do agente.

        Raises
        ------
        Exception
            Qualquer exceção em ``_execute()`` é relançada após logging.
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
    # Propriedades de introspecção
    # ------------------------------------------------------------------

    @property
    def last_duration_s(self) -> float:
        """Tempo em segundos da última execução de ``run()``."""
        return self._last_duration_s

    @property
    def run_count(self) -> int:
        """Quantidade de execuções bem-sucedidas de ``run()``."""
        return self._run_count

    @property
    def last_error(self) -> Optional[Exception]:
        """Exceção da última falha, ou ``None``."""
        return self._last_error

    @property
    def has_run(self) -> bool:
        """``True`` se ``run()`` foi executado com sucesso ao menos uma vez."""
        return self._run_count > 0

    # ------------------------------------------------------------------
    # Abstrato — subclasses implementam APENAS isso
    # ------------------------------------------------------------------

    @abstractmethod
    def _execute(self, input_data: InputT) -> OutputT:
        """
        Lógica principal do agente.

        Chamado exclusivamente por ``run()``. Timing, logging e tratamento
        de erros já são gerenciados pela classe base.
        """

    # ------------------------------------------------------------------
    # Métodos especiais
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} name={self.name!r} "
            f"runs={self._run_count} last={self._last_duration_s:.3f}s>"
        )


# ---------------------------------------------------------------------------
# MockAgent — usado para testes sem efeitos colaterais reais
# ---------------------------------------------------------------------------

class MockAgent(BaseAgent[InputT, OutputT]):
    """
    Dublê de teste para ``BaseAgent``.

    Aceita um valor fixo de retorno ou um erro simulado na construção.
    Permite testar o ciclo completo ``validate() → run() → _execute()``
    sem depender de I/O real.

    Exemplos
    --------
    >>> agent = MockAgent(name="TestAgent", return_value=42)
    >>> agent.run("any input")
    42
    >>> agent.run_count
    1
    >>> repr(agent)
    "<MockAgent name='TestAgent' runs=1 last=0.000s>"

    >>> failing = MockAgent(name="Failing", side_effect=ValueError("boom"))
    >>> failing.run("x")   # levanta ValueError, last_error é preenchido
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
        #: Todos os inputs recebidos em chamadas de ``run()`` — útil em testes.
        self.received_inputs: list[InputT] = []

    def validate(self) -> ValidationResult:
        return self._validation_result

    def _execute(self, input_data: InputT) -> OutputT:
        self.received_inputs.append(input_data)
        if self._side_effect is not None:
            raise self._side_effect
        return self._return_value