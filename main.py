"""Expense Manager CLI entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from agents.orchestrator import Orchestrator, PipelineConfig
from agents.parse_agent import ParseInput
from core.exceptions import ExpenseTrackerError
from core.models import InputSource


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Expense Manager - gera relatórios PDF a partir de CSV de despesas"
    )
    parser.add_argument(
        "csv_file",
        type=Path,
        help="Arquivo CSV de entrada com despesas",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("expense-report.pdf"),
        help="Caminho do PDF de saída (padrão: expense-report.pdf)",
    )
    parser.add_argument(
        "--currency",
        default="BRL",
        help="Código da moeda padrão para valores sem moeda explícita",
    )
    parser.add_argument(
        "--author",
        default="Expense Tracker",
        help="Nome do autor a ser incluído no PDF",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if not args.csv_file.exists():
        print(f"Erro: arquivo CSV não encontrado: {args.csv_file}")
        return 2

    config = PipelineConfig(
        parse_input=ParseInput(
            source=InputSource.CSV_FILE,
            csv_path=args.csv_file,
            default_currency=args.currency,
        ),
        output_pdf=args.output,
        author=args.author,
    )

    orchestrator = Orchestrator(config)
    result = orchestrator.run()

    if not result.success:
        print(f"Falha: {result.error}")
        for warning in result.warnings:
            print(f"Aviso: {warning}")
        return 1

    print(f"Relatório gerado com sucesso: {result.pdf_path}")
    if result.warnings:
        print("Avisos:")
        for warning in result.warnings:
            print(f"  - {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
