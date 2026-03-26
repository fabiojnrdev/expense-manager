"""
agents/orchestrator.py
======================
Orquestrador do pipeline que conecta todos os subagentes, injeta métricas
de tempo, trata erros e retorna o resultado final para a camada de CLI.

Pipeline
--------
  ParseAgent → AggregationAgent → ChartAgent → RenderAgent
                                     ↕
                               (métricas coletadas)

O orquestrador é o ÚNICO componente que conhece o fluxo completo.
Cada agente conhece apenas seu contrato de entrada/saída.

Responsabilidades
-----------------
- Instanciar agentes com a configuração correta
- Encadear saída de um agente como entrada do próximo
- Medir tempo de execução de cada fase
- Injetar métricas no ReportMetrics
- Limpar arquivos temporários (gráfico PNG)
- Retornar um PipelineResult estruturado
"""

from __future__ import annotations

import dataclasses
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents.aggregation_agent import AggregationAgent, AggregationInput
from agents.chart_agent import ChartAgent, ChartConfig
from agents.parse_agent import ParseAgent, ParseInput
from agents.render_agent import RenderAgent, RenderConfig, RenderInput
from core.exceptions import ExpenseTrackerError
from core.models import ExpenseReport, ReportMetrics

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Configuração principal para execução do pipeline."""
    parse_input: ParseInput
    output_pdf: Path
    currency: str = "BRL"
    author: str = "Expense Tracker"
    chart_config: Optional[ChartConfig] = None


@dataclass
class PipelineResult:
    """
    Resultado retornado para CLI ou testes.

    Atributos
    ---------
    success : bool
    pdf_path : caminho do PDF gerado (ou None em caso de erro)
    report : objeto completo com métricas
    error : mensagem de erro (se houver)
    warnings : lista de avisos não críticos
    """
    success: bool
    pdf_path: Optional[Path] = None
    report: Optional[ExpenseReport] = None
    error: Optional[str] = None
    warnings: list[str] = dataclasses.field(default_factory=list)


class Orchestrator:
    """Responsável por executar todo o pipeline de despesas."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self._warnings: list[str] = []

    def run(self) -> PipelineResult:
        cfg = self.config
        chart_path: Optional[Path] = None

        try:
            # ------------------------------------------------------------
            # Instanciação dos agentes
            # ------------------------------------------------------------
            parse_agent = ParseAgent()
            agg_agent   = AggregationAgent()
            chart_agent = ChartAgent(config=cfg.chart_config)
            render_cfg  = RenderConfig(output_path=cfg.output_pdf, author=cfg.author)
            render_agent = RenderAgent(render_cfg)

            # ------------------------------------------------------------
            # Validação prévia (antes de executar qualquer fase)
            # ------------------------------------------------------------
            for agent in (parse_agent, agg_agent, chart_agent, render_agent):
                result = agent.validate()

                # Coleta avisos
                for msg in result.messages:
                    self._warnings.append(f"{agent.name}: {msg}")

                # Interrompe se houver erro crítico
                result.raise_if_error()

            # ------------------------------------------------------------
            # Fase 1 — Parse (leitura e validação dos dados)
            # ------------------------------------------------------------
            logger.info("=== Phase 1: Parse ===")
            t0 = time.perf_counter()

            parse_result = parse_agent.run(cfg.parse_input)

            parse_duration = time.perf_counter() - t0

            # Total de registros analisados
            total_attempted = (
                len(parse_result.expenses)
                + len(parse_result.errors)
                + parse_result.duplicates_skipped
            )

            # Registra avisos de linhas inválidas
            if parse_result.errors:
                for err in parse_result.errors:
                    self._warnings.append(f"Parse warning: {err}")
                    logger.warning("Skipped row: %s", err)

            # Se nada válido foi encontrado, aborta
            if not parse_result.expenses:
                return PipelineResult(
                    success=False,
                    error="Nenhuma despesa válida encontrada nos dados de entrada.",
                    warnings=self._warnings,
                )

            # ------------------------------------------------------------
            # Fase 2 — Aggregate (cálculo e agrupamento)
            # ------------------------------------------------------------
            logger.info("=== Phase 2: Aggregate ===")

            agg_input = AggregationInput(
                expenses=parse_result.expenses,
                currency=cfg.currency,
                parse_duration_s=parse_duration,
                total_records_read=total_attempted,
                invalid_records=len(parse_result.errors),
                duplicate_records=parse_result.duplicates_skipped,
            )

            t1 = time.perf_counter()
            report = agg_agent.run(agg_input)
            agg_duration = time.perf_counter() - t1

            # ------------------------------------------------------------
            # Fase 3 — Chart (geração do gráfico)
            # ------------------------------------------------------------
            logger.info("=== Phase 3: Chart ===")
            chart_path = chart_agent.run(report)

            # ------------------------------------------------------------
            # Fase 4 — Render (geração do PDF final)
            # ------------------------------------------------------------
            logger.info("=== Phase 4: Render ===")

            t2 = time.perf_counter()
            pdf_path = render_agent.run(
                RenderInput(report=report, chart_path=chart_path)
            )
            render_duration = time.perf_counter() - t2

            # ------------------------------------------------------------
            # Atualiza métricas com tempos finais
            # (objeto imutável → dataclasses.replace)
            # ------------------------------------------------------------
            if report.metrics:
                updated = dataclasses.replace(
                    report.metrics,
                    aggregation_duration_s=round(agg_duration, 4),
                    render_duration_s=round(render_duration, 4),
                )
                object.__setattr__(report, "metrics", updated)

            # Retorno de sucesso
            return PipelineResult(
                success=True,
                pdf_path=pdf_path,
                report=report,
                warnings=self._warnings,
            )

        # ------------------------------------------------------------
        # Tratamento de erros de domínio
        # ------------------------------------------------------------
        except ExpenseTrackerError as exc:
            logger.error("Pipeline failed (domain error): %s", exc)
            return PipelineResult(
                success=False,
                error=str(exc),
                warnings=self._warnings,
            )

        # ------------------------------------------------------------
        # Tratamento de erros inesperados
        # ------------------------------------------------------------
        except Exception as exc:
            logger.exception("Pipeline failed (unexpected): %s", exc)
            return PipelineResult(
                success=False,
                error=f"Erro inesperado: {exc}",
                warnings=self._warnings,
            )

        # ------------------------------------------------------------
        # Limpeza de arquivos temporários (sempre executa)
        # ------------------------------------------------------------
        finally:
            if chart_path and chart_path.exists():
                try:
                    os.unlink(chart_path)
                    logger.debug("Arquivo temporário removido: %s", chart_path)
                except OSError:
                    pass