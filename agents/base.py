"""Classe base abstrata e definição de protocolo para todos os subagentes.

Arquitetura
------------
Cada etapa de processamento é encapsulada em sua própria classe Agente. Os agentes são
trabalhadores sem estado e com responsabilidade única, que recebem entradas tipadas e
retornam saídas tipadas. O orquestrador (``agents/orchestrator.py``) os interconecta
e mede os tempos de execução.

Ciclo de vida do agente:

1. Instanciação — injeta a configuração

2. ``validate()`` — verificações prévias opcionais

3. ``run(input)`` — executa a única responsabilidade do agente

4. O agente expõe a propriedade ``metrics`` após a conclusão de ``run()``

Justificativa do projeto
----------------
Usar uma classe base explícita (em vez de apenas tipagem dinâmica) permite:

- Logs/rastreamento consistentes em um único local
- Orquestração com segurança de tipos

- Testes unitários mais fáceis via MockAgent"""

 
from __future__ import annotations
 
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Generic, Optional, TypeVar

logger = logging.getLogger(__name__)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")

class BaseAgent(ABC, Generic[InputT, OutputT]):
    """Base abstrata para todos os agentes de pipelines
    As subclasses devem implementar ``_execute()``. O método público ``run()``
    envolve ``_execute()`` com temporização, registro e normalização de erros.
    """
    
    #: Nome legível para humanos exibido em registros e relatórios de métricas
    name: str = "UnnamedAgent"
    
    def __init__(self) -> None:
        self._last_duration_s: float = 0.0
        self._run_count: int = 0
        
    # Interface pública
    
    def run(self,input_data: InputT) -> OutputT:
        """
        Executa o agente, mede o tempo real, registra a entrada/saída.
        Parâmetros
        ----------
        input_data : InputT
        Carga útil de entrada específica do agente.
        Retorna
        -------
        OutputT
        Carga útil de saída específica do agente.
"""
        logger.info("[%s] starting", self.name)
        start = time.perf_counter()
        try:
            result = self._execute(input_data)
            self._last_duration_s = time.perf_counter() - start
            self._run_count += 1
            logger.info(
                "[%s] completed in %.3fs (run #%d)",
                self.name, self._last_duration_s, self._run_count
            )
            return result
        except Exception as exc:
            self._last_duration_s = time.perf_counter() - start
            logger.error("[%s] failed after %.3fs: %s", self.name, self._last_duration_s, exc)
            raise
        