"""
agents/aggregation_agent.py
===========================
Subagente responsável por agrupar despesas por categoria e calcular
todas as métricas estatísticas utilizadas pelo agente de renderização.

Utiliza ``pandas`` para operações eficientes de groupby mesmo em datasets pequenos,
fornecendo um mecanismo de agregação consistente e testado.

Entrada : list[Expense]
Saída   : ExpenseReport (com resumos, total geral e métricas)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

import pandas as pd

from agents.base import BaseAgent, ValidationResult, ValidationStatus
from core.models import (
    CategorySummary,
    Expense,
    ExpenseReport,
    Money,
    ReportMetrics,
)

logger = logging.getLogger(__name__)


@dataclass
class AggregationInput:
    """Contrato de entrada para o AggregationAgent."""
    expenses: list[Expense]
    currency: str = "BRL"
    # Dados de tempo injetados pelo orquestrador após a fase de parsing
    parse_duration_s: float = 0.0
    total_records_read: int = 0
    invalid_records: int = 0
    duplicate_records: int = 0


class AggregationAgent(BaseAgent[AggregationInput, ExpenseReport]):
    """
    Agrupa despesas por categoria e calcula estatísticas por categoria e globais.

    Herda timing, logging e tratamento de erros de ``BaseAgent``.

    Principais responsabilidades
    ---------------------------
    - Construir um DataFrame pandas a partir da lista de despesas
    - Agrupar por nome da categoria, calculando soma / contagem / média
    - Calcular total geral e percentual por categoria
    - Capturar métricas estatísticas (desvio padrão, mínimo, máximo, intervalo de datas)
    - Retornar um ``ExpenseReport`` completo

    Por que pandas?
    ---------------
    Mesmo para datasets pequenos, pandas oferece operações vetorizadas,
    groupby robusto em uma linha e funções estatísticas consistentes.
    """

    name = "AggregationAgent"

    def validate(self) -> ValidationResult:
        return ValidationResult(status=ValidationStatus.OK)

    def _execute(self, inp: AggregationInput) -> ExpenseReport:
        logger.info("[%s] aggregating %d expenses", self.name, len(inp.expenses))

        if not inp.expenses:
            return self._empty_report(inp)

        # ----------------------------------------------------------------
        # Construção do DataFrame
        # ----------------------------------------------------------------
        records = [
            {
                "description": e.description,
                "amount": float(e.amount.amount),
                "category": e.category.name,
                "date": e.expense_date,
            }
            for e in inp.expenses
        ]
        df = pd.DataFrame(records)

        # ----------------------------------------------------------------
        # Total geral
        # ----------------------------------------------------------------
        grand_float = df["amount"].sum()
        grand_total = Money(Decimal(str(round(grand_float, 2))), inp.currency)

        # ----------------------------------------------------------------
        # Agregação por categoria
        # ----------------------------------------------------------------
        grouped = df.groupby("category")["amount"].agg(
            total="sum", count="count", average="mean"
        ).reset_index()

        summaries: list[CategorySummary] = []
        for _, row in grouped.iterrows():
            pct = (row["total"] / grand_float * 100) if grand_float else 0.0
            summaries.append(CategorySummary(
                category_name=row["category"],
                total=Money(Decimal(str(round(row["total"], 2))), inp.currency),
                count=int(row["count"]),
                percentage=round(pct, 2),
                average=Money(Decimal(str(round(row["average"], 2))), inp.currency),
            ))

        # Ordena por total decrescente
        summaries.sort(key=lambda s: s.total.amount, reverse=True)

        # ----------------------------------------------------------------
        # Métricas estatísticas
        # ----------------------------------------------------------------
        amounts = df["amount"]
        dates = pd.to_datetime(df["date"])
        date_range_days = (dates.max() - dates.min()).days if len(dates) > 1 else 0
        std_dev = amounts.std(ddof=1) if len(amounts) > 1 else 0.0

        metrics = ReportMetrics(
            total_records_read=inp.total_records_read or len(inp.expenses),
            valid_records=len(inp.expenses),
            invalid_records=inp.invalid_records,
            duplicate_records=inp.duplicate_records,
            parse_duration_s=inp.parse_duration_s,
            aggregation_duration_s=0.0,   # preenchido pelo orquestrador
            render_duration_s=0.0,        # preenchido pelo orquestrador
            category_count=len(summaries),
            date_range_days=int(date_range_days),
            largest_single_expense=float(amounts.max()),
            smallest_single_expense=float(amounts.min()),
            std_deviation=float(std_dev) if not math.isnan(std_dev) else 0.0,
        )

        # Limites do período
        period_start: Optional[date] = pd.to_datetime(df["date"]).min().date()
        period_end: Optional[date] = pd.to_datetime(df["date"]).max().date()

        report = ExpenseReport(
            summaries=summaries,
            grand_total=grand_total,
            expenses=inp.expenses,
            period_start=period_start,
            period_end=period_end,
            metrics=metrics,
        )

        logger.info(
            "[%s] done — %d categories, grand total = %s",
            self.name, len(summaries), grand_total,
        )
        return report

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_report(inp: AggregationInput) -> ExpenseReport:
        metrics = ReportMetrics(
            total_records_read=inp.total_records_read,
            valid_records=0,
            invalid_records=inp.invalid_records,
            duplicate_records=inp.duplicate_records,
            parse_duration_s=inp.parse_duration_s,
            aggregation_duration_s=0.0,
            render_duration_s=0.0,
            category_count=0,
            date_range_days=0,
            largest_single_expense=0.0,
            smallest_single_expense=0.0,
            std_deviation=0.0,
        )
        return ExpenseReport(
            summaries=[],
            grand_total=Money(Decimal("0"), inp.currency),
            expenses=[],
            metrics=metrics,
        )