"""
agents/parse_agent.py
=====================
Subagente responsável por ingerir dados brutos de despesas de múltiplas fontes
e produzir uma lista validada de objetos de domínio ``Expense``.

Fontes suportadas
-----------------
1. Arquivo CSV  — colunas: description, amount, category[, date][, tags]
2. Terminal     — prompt interativo, uma despesa por vez
3. Pipe stdin   — dados CSV vindos de outro processo

O agente é tolerante a pequenas variações de formatação (veja ``_parse_row``)
e coleta todos os erros de parsing ao invés de falhar na primeira linha inválida,
permitindo relatórios posteriores sobre a qualidade dos dados.
"""

from __future__ import annotations

import csv
import logging
import sys
from dataclasses import dataclass, field
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Optional

from agents.base import BaseAgent, ValidationResult, ValidationStatus
from core.exceptions import ParseError, ValidationError
from core.models import Category, Expense, InputSource, Money

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contratos de entrada / saída
# ---------------------------------------------------------------------------

@dataclass
class ParseInput:
    """
    Contrato de entrada para o ParseAgent.

    Atributos
    ----------
    source : InputSource
        Origem dos dados.
    csv_path : Optional[Path]
        Obrigatório quando source == CSV_FILE.
    raw_csv : Optional[str]
        Texto CSV inline, usado para testes ou stdin.
    default_currency : str
        Código da moeda aplicado a todos os registros.
    """
    source: InputSource
    csv_path: Optional[Path] = None
    raw_csv: Optional[str] = None
    default_currency: str = "BRL"


@dataclass
class ParseResult:
    """
    Contrato de saída para o ParseAgent.

    Atributos
    ----------
    expenses : list[Expense]
        Despesas válidas parseadas com sucesso.
    errors : list[ParseError]
        Linhas que falharam no parsing; nunca lança exceção por dados inválidos.
    duplicates_skipped : int
        Quantidade de linhas duplicadas ignoradas.
    """
    expenses: list[Expense] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)
    duplicates_skipped: int = 0


# ---------------------------------------------------------------------------
# Colunas obrigatórias do CSV e aliases
# ---------------------------------------------------------------------------

_COLUMN_ALIASES: dict[str, list[str]] = {
    "description": ["description", "descricao", "desc", "item", "nome"],
    "amount":      ["amount", "valor", "value", "preco", "price", "total"],
    "category":    ["category", "categoria", "cat", "grupo", "group"],
    "date":        ["date", "data", "dt", "expense_date"],
    "tags":        ["tags", "tag", "label", "labels"],
}


def _normalise_header(raw: str) -> str:
    """Mapeia o nome do cabeçalho para o formato canônico (ou mantém original)."""
    normalised = raw.strip().lower().replace(" ", "_")
    for canonical, aliases in _COLUMN_ALIASES.items():
        if normalised in aliases:
            return canonical
    return normalised


# ---------------------------------------------------------------------------
# Implementação do agente
# ---------------------------------------------------------------------------

class ParseAgent(BaseAgent[ParseInput, ParseResult]):
    """
    Converte dados brutos de despesas em objetos de domínio ``Expense``.

    Herda timing, logging e tratamento de erro de ``BaseAgent``.
    A lógica principal está em ``_execute()``, que delega para o leitor correto.
    """

    name = "ParseAgent"

    def validate(self) -> ValidationResult:
        """Sem validação global; verificações ocorrem durante a execução."""
        return ValidationResult(status=ValidationStatus.OK)

    def _execute(self, input_data: ParseInput) -> ParseResult:
        logger.info("[%s] source=%s", self.name, input_data.source)

        if input_data.source == InputSource.CSV_FILE:
            return self._from_csv_file(input_data)
        elif input_data.source == InputSource.TERMINAL:
            return self._from_terminal(input_data)
        elif input_data.source == InputSource.STDIN_PIPE:
            return self._from_stdin(input_data)
        else:
            raise ValueError(f"Unsupported source: {input_data.source}")

    # ------------------------------------------------------------------
    # Leitores por tipo de fonte
    # ------------------------------------------------------------------

    def _from_csv_file(self, inp: ParseInput) -> ParseResult:
        if inp.csv_path is None:
            raise ValueError("csv_path must be provided for CSV_FILE source")
        path = Path(inp.csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")
        text = path.read_text(encoding="utf-8-sig")  # trata BOM
        logger.info("[%s] read %d bytes from %s", self.name, len(text), path)
        return self._parse_csv_text(text, inp.default_currency, InputSource.CSV_FILE)

    def _from_stdin(self, inp: ParseInput) -> ParseResult:
        text = sys.stdin.read()
        return self._parse_csv_text(text, inp.default_currency, InputSource.STDIN_PIPE)

    def _from_terminal(self, inp: ParseInput) -> ParseResult:
        """Sessão interativa no terminal — coleta despesas até o usuário sair."""
        result = ParseResult()
        print("\n" + "═" * 60)
        print("  EXPENSE TRACKER — Entrada Manual")
        print("  Digite 'fim' na descrição para encerrar")
        print("═" * 60)

        line_no = 0
        while True:
            line_no += 1
            print(f"\n── Despesa #{line_no} ──")
            try:
                desc = input("  Descrição (ou 'fim'): ").strip()
                if desc.lower() in ("fim", "exit", "quit", "q"):
                    break
                amount_raw = input("  Valor (ex: 45,90): ").strip()
                category_raw = input("  Categoria (ex: Alimentação): ").strip()
                date_raw = input("  Data (AAAA-MM-DD) [Enter=hoje]: ").strip()
                tags_raw = input("  Tags (vírgula, opcional): ").strip()

                expense = self._build_expense(
                    desc=desc,
                    amount_raw=amount_raw,
                    category_raw=category_raw,
                    date_raw=date_raw,
                    tags_raw=tags_raw,
                    currency=inp.default_currency,
                    source=InputSource.TERMINAL,
                    line_no=line_no,
                )
                result.expenses.append(expense)
                print(f"  ✓ Registrado: {expense.category} — {expense.amount}")
            except (ParseError, ValidationError, ValueError) as exc:
                logger.warning("Terminal parse error: %s", exc)
                print(f"  ✗ Erro: {exc}")
                result.errors.append(
                    ParseError(str(exc), raw_line="<terminal>", line_number=line_no)
                )

        print(f"\n✓ {len(result.expenses)} despesas registradas.\n")
        return result

    # ------------------------------------------------------------------
    # Helpers de parsing CSV
    # ------------------------------------------------------------------

    def _parse_csv_text(
        self,
        text: str,
        currency: str,
        source: InputSource,
    ) -> ParseResult:
        result = ParseResult()
        seen: set[tuple] = set()  # usado para detectar duplicatas

        reader = csv.DictReader(StringIO(text))
        # Normaliza os headers
        if reader.fieldnames:
            reader.fieldnames = [_normalise_header(h) for h in reader.fieldnames]

        for line_no, row in enumerate(reader, start=2):  # linha 1 = header
            try:
                expense = self._parse_row(row, currency, source, line_no)
                # Duplicado: mesma (descrição, valor, data, categoria)
                fingerprint = (
                    expense.description.lower(),
                    expense.amount.amount,
                    expense.expense_date,
                    expense.category.name.lower(),
                )
                if fingerprint in seen:
                    logger.debug("Duplicate row at line %d — skipping", line_no)
                    result.duplicates_skipped += 1
                    continue
                seen.add(fingerprint)
                result.expenses.append(expense)
            except (ParseError, ValidationError) as exc:
                logger.warning("Row %d skipped: %s", line_no, exc)
                result.errors.append(exc)

        logger.info(
            "[%s] parsed %d valid, %d errors, %d dupes",
            self.name, len(result.expenses), len(result.errors), result.duplicates_skipped,
        )
        return result

    def _parse_row(
        self,
        row: dict,
        currency: str,
        source: InputSource,
        line_no: int,
    ) -> Expense:
        """Converte uma linha do CSV em um objeto ``Expense``."""
        desc = (row.get("description") or "").strip()
        if not desc:
            raise ParseError("Missing description", line_number=line_no)

        amount_raw = (row.get("amount") or "").strip()
        if not amount_raw:
            raise ParseError("Missing amount", raw_line=str(row), line_number=line_no)

        cat_raw = (row.get("category") or "Outros").strip()
        date_raw = (row.get("date") or "").strip()
        tags_raw = (row.get("tags") or "").strip()

        return self._build_expense(
            desc=desc,
            amount_raw=amount_raw,
            category_raw=cat_raw,
            date_raw=date_raw,
            tags_raw=tags_raw,
            currency=currency,
            source=source,
            line_no=line_no,
        )

    @staticmethod
    def _build_expense(
        desc: str,
        amount_raw: str,
        category_raw: str,
        date_raw: str,
        tags_raw: str,
        currency: str,
        source: InputSource,
        line_no: int,
    ) -> Expense:
        try:
            money = Money.from_string(amount_raw, currency)
        except ValueError as exc:
            raise ParseError(str(exc), raw_line=amount_raw, line_number=line_no)

        category = Category(name=category_raw)

        if date_raw:
            try:
                expense_date = date.fromisoformat(date_raw)
            except ValueError:
                raise ParseError(
                    f"Invalid date format '{date_raw}' — expected YYYY-MM-DD",
                    raw_line=date_raw,
                    line_number=line_no,
                )
        else:
            expense_date = date.today()

        tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

        return Expense(
            description=desc,
            amount=money,
            category=category,
            expense_date=expense_date,
            tags=tags,
            source=source,
        )