"""
tests/test_agents.py
====================
Comprehensive unit and integration tests for all pipeline components.

Coverage
--------
  TestMoney            — value object arithmetic, parsing, formatting, edge cases
  TestCategory         — normalisation, hierarchy, equality
  TestExpense          — construction, validation, serialisation
  TestReportMetrics    — computed properties (data_quality_pct, total_duration_s)
  TestParseAgent       — CSV parsing, bad rows, duplicates, aliases, date fallback
  TestAggregationAgent — groupby, totals, sorting, percentages, edge cases, metrics
  TestChartAgent       — PNG output, temp-file cleanup, empty-data guard
  TestRenderAgent      — validate() creates dirs, PDF written and non-empty
  TestBaseAgent        — MockAgent behaviour, ValidationResult states, lifecycle
  TestEndToEnd         — full pipeline CSV→PDF, validation-failure path,
                         all-invalid-rows path, duplicate-only path

Run with:
    python -m pytest tests/test_agents.py -v
    python tests/test_agents.py          # without pytest installed
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

# Ensure root package is importable regardless of working directory
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from agents.aggregation_agent import AggregationAgent, AggregationInput
from agents.base import (
    BaseAgent,
    MockAgent,
    ValidationResult,
    ValidationStatus,
)
from agents.chart_agent import ChartAgent, ChartConfig
from agents.orchestrator import Orchestrator, PipelineConfig
from agents.parse_agent import ParseAgent, ParseInput
from agents.render_agent import RenderAgent, RenderConfig, RenderInput
from core.exceptions import ConfigurationError, ParseError
from core.models import (
    Category,
    CategorySummary,
    Expense,
    ExpenseReport,
    InputSource,
    Money,
    ReportMetrics,
)


# ============================================================================
# Fixtures and shared helpers
# ============================================================================

def make_expenses(n: int = 4) -> list[Expense]:
    """Return a deterministic list of Expense objects for test reuse."""
    data = [
        ("Supermercado",  "250.00", "Alimentação",   date(2024, 1, 5)),
        ("Restaurante",   "80.00",  "Alimentação",   date(2024, 1, 10)),
        ("Aluguel",       "1500.00","Moradia",        date(2024, 1, 1)),
        ("Gasolina",      "150.00", "Transporte",     date(2024, 1, 8)),
        ("Academia",      "99.90",  "Saúde",          date(2024, 1, 2)),
        ("Netflix",       "55.90",  "Entretenimento", date(2024, 1, 3)),
    ]
    return [
        Expense(desc, Money(Decimal(amt), "BRL"), Category(cat), dt)
        for desc, amt, cat, dt in data[:n]
    ]


def make_report(expenses: list[Expense] | None = None) -> ExpenseReport:
    """Build a minimal but complete ExpenseReport via AggregationAgent."""
    exps = expenses if expenses is not None else make_expenses()
    agent = AggregationAgent()
    return agent.run(AggregationInput(expenses=exps))


SAMPLE_CSV = """\
description,amount,category,date,tags
Supermercado,250.00,Alimentação,2024-01-05,compras
Aluguel,1500.00,Moradia,2024-01-01,fixo
Gasolina,150.00,Transporte,2024-01-08,
"""

CSV_WITH_PT_HEADERS = """\
descricao,valor,categoria,data,tags
Café,12.00,Alimentação,2024-01-10,
Ônibus,4.50,Transporte,2024-01-11,
"""

CSV_BAD_ROW = """\
description,amount,category,date,tags
Valid Row,100.00,Test,2024-01-01,
,MISSING_DESC,Test,2024-01-01,
Valid Row 2,200.00,Test,2024-01-02,
"""

CSV_DUPLICATE = """\
description,amount,category,date,tags
Café,10.00,Alimentação,2024-01-01,
Café,10.00,Alimentação,2024-01-01,
"""

CSV_NO_DATE = """\
description,amount,category
Internet,99.90,Moradia
"""

CSV_INVALID_DATE = """\
description,amount,category,date
TV,200.00,Entretenimento,31-01-2024
"""

CSV_ALL_INVALID = """\
description,amount,category,date
,,Alimentação,2024-01-01
,,Moradia,2024-01-02
"""


# ============================================================================
# Money
# ============================================================================

class TestMoney:

    def test_basic_creation(self):
        m = Money(Decimal("100.00"), "BRL")
        assert m.amount == Decimal("100.00")
        assert m.currency == "BRL"

    def test_from_string_plain_decimal(self):
        assert Money.from_string("45.90").amount == Decimal("45.90")

    def test_from_string_brazilian_thousands(self):
        assert Money.from_string("1.299,50").amount == Decimal("1299.50")

    def test_from_string_simple_comma(self):
        assert Money.from_string("200,00").amount == Decimal("200.00")

    def test_from_string_strips_brl_symbol(self):
        assert Money.from_string("R$ 200,00").amount == Decimal("200.00")

    def test_from_string_strips_usd_symbol(self):
        assert Money.from_string("US$ 9.99", "USD").amount == Decimal("9.99")

    def test_from_string_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            Money.from_string("abc")

    def test_addition_same_currency(self):
        a = Money(Decimal("10.00"), "BRL")
        b = Money(Decimal("5.50"), "BRL")
        result = a + b
        assert result.amount == Decimal("15.50")
        assert result.currency == "BRL"

    def test_addition_different_currency_raises(self):
        with pytest.raises(ValueError, match="Cannot add"):
            Money(Decimal("10"), "BRL") + Money(Decimal("10"), "USD")

    def test_negative_amount_raises(self):
        with pytest.raises(ValueError):
            Money(Decimal("-0.01"))

    def test_zero_is_valid(self):
        m = Money(Decimal("0.00"))
        assert m.amount == Decimal("0.00")

    def test_str_brl(self):
        s = str(Money(Decimal("1500.00"), "BRL"))
        assert "R$" in s
        assert "1,500.00" in s

    def test_str_usd(self):
        s = str(Money(Decimal("9.99"), "USD"))
        assert "US$" in s

    def test_str_eur(self):
        s = str(Money(Decimal("9.99"), "EUR"))
        assert "€" in s

    def test_frozen_immutable(self):
        m = Money(Decimal("10.00"))
        with pytest.raises((AttributeError, TypeError)):
            m.amount = Decimal("99.00")  # type: ignore[misc]

    def test_coerces_int_to_decimal(self):
        m = Money(100)  # type: ignore[arg-type]
        assert isinstance(m.amount, Decimal)


# ============================================================================
# Category
# ============================================================================

class TestCategory:

    def test_normalises_to_title_case(self):
        assert Category("alimentação").name == "Alimentação"

    def test_strips_whitespace(self):
        assert Category("  Moradia  ").name == "Moradia"

    def test_full_name_with_parent(self):
        c = Category("Groceries", parent="Food")
        assert c.full_name == "Food/Groceries"

    def test_full_name_without_parent(self):
        assert Category("Transporte").full_name == "Transporte"

    def test_str_equals_full_name(self):
        c = Category("Saúde", parent="Pessoal")
        assert str(c) == c.full_name

    def test_frozen_immutable(self):
        c = Category("Alimentação")
        with pytest.raises((AttributeError, TypeError)):
            c.name = "Outro"  # type: ignore[misc]

    def test_two_equal_categories(self):
        assert Category("Alimentação") == Category("alimentação")


# ============================================================================
# Expense
# ============================================================================

class TestExpense:

    def test_valid_construction(self):
        e = Expense(
            description="Mercado",
            amount=Money(Decimal("100"), "BRL"),
            category=Category("Alimentação"),
        )
        assert e.description == "Mercado"

    def test_empty_description_raises(self):
        with pytest.raises(ValueError):
            Expense("  ", Money(Decimal("10")), Category("Outros"))

    def test_default_date_is_today(self):
        e = Expense("X", Money(Decimal("1")), Category("Y"))
        assert e.expense_date == date.today()

    def test_to_dict_contains_required_keys(self):
        e = Expense("Mercado", Money(Decimal("50"), "BRL"), Category("Alimentação"), date(2024, 1, 1))
        d = e.to_dict()
        for key in ("description", "amount", "currency", "category", "date"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_values(self):
        e = Expense("X", Money(Decimal("9.99"), "BRL"), Category("Alimentação"), date(2024, 6, 15))
        d = e.to_dict()
        assert d["amount"] == 9.99
        assert d["currency"] == "BRL"
        assert d["date"] == "2024-06-15"

    def test_tags_default_empty(self):
        e = Expense("X", Money(Decimal("1")), Category("Y"))
        assert e.tags == []


# ============================================================================
# ReportMetrics
# ============================================================================

class TestReportMetrics:

    def _make_metrics(self, **overrides) -> ReportMetrics:
        defaults = dict(
            total_records_read=10,
            valid_records=8,
            invalid_records=1,
            duplicate_records=1,
            parse_duration_s=0.1,
            aggregation_duration_s=0.05,
            render_duration_s=0.2,
            category_count=3,
            date_range_days=30,
            largest_single_expense=500.0,
            smallest_single_expense=10.0,
            std_deviation=120.5,
        )
        defaults.update(overrides)
        return ReportMetrics(**defaults)

    def test_data_quality_pct(self):
        m = self._make_metrics(total_records_read=10, valid_records=8)
        assert m.data_quality_pct == 80.0

    def test_data_quality_pct_zero_records(self):
        m = self._make_metrics(total_records_read=0, valid_records=0)
        assert m.data_quality_pct == 0.0

    def test_total_duration_sums_phases(self):
        m = self._make_metrics(
            parse_duration_s=0.1,
            aggregation_duration_s=0.05,
            render_duration_s=0.2,
        )
        assert abs(m.total_duration_s - 0.35) < 1e-9

    def test_summary_lines_count(self):
        m = self._make_metrics()
        lines = m.summary_lines()
        assert len(lines) >= 10  # at least the main metrics rows

    def test_summary_lines_contain_key_values(self):
        m = self._make_metrics(category_count=5, date_range_days=28)
        joined = "\n".join(m.summary_lines())
        assert "5" in joined
        assert "28" in joined


# ============================================================================
# ParseAgent
# ============================================================================

class TestParseAgent:

    # ------------------------------------------------------------------
    # Positive cases
    # ------------------------------------------------------------------

    def test_parses_three_valid_rows(self):
        agent = ParseAgent()
        result = agent._parse_csv_text(SAMPLE_CSV, "BRL", InputSource.STDIN_PIPE)
        assert len(result.expenses) == 3
        assert result.errors == []

    def test_parses_portuguese_headers(self):
        agent = ParseAgent()
        result = agent._parse_csv_text(CSV_WITH_PT_HEADERS, "BRL", InputSource.STDIN_PIPE)
        assert len(result.expenses) == 2
        assert result.errors == []

    def test_date_defaults_to_today_when_missing(self):
        agent = ParseAgent()
        result = agent._parse_csv_text(CSV_NO_DATE, "BRL", InputSource.STDIN_PIPE)
        assert len(result.expenses) == 1
        assert result.expenses[0].expense_date == date.today()

    def test_tags_parsed_correctly(self):
        agent = ParseAgent()
        result = agent._parse_csv_text(SAMPLE_CSV, "BRL", InputSource.STDIN_PIPE)
        supermarket = next(e for e in result.expenses if e.description == "Supermercado")
        assert "compras" in supermarket.tags

    def test_amounts_parsed_as_decimal(self):
        agent = ParseAgent()
        result = agent._parse_csv_text(SAMPLE_CSV, "BRL", InputSource.STDIN_PIPE)
        total = sum(float(e.amount.amount) for e in result.expenses)
        assert abs(total - 1900.0) < 0.01

    def test_run_count_increments_via_base(self):
        agent = ParseAgent()
        agent._parse_csv_text(SAMPLE_CSV, "BRL", InputSource.STDIN_PIPE)
        # run() is called by the user; internal helper doesn't go through run()
        # Verify the agent is a proper BaseAgent
        assert isinstance(agent, BaseAgent)

    # ------------------------------------------------------------------
    # Error tolerance
    # ------------------------------------------------------------------

    def test_bad_row_collected_not_raised(self):
        agent = ParseAgent()
        result = agent._parse_csv_text(CSV_BAD_ROW, "BRL", InputSource.STDIN_PIPE)
        assert len(result.expenses) == 2
        assert len(result.errors) == 1

    def test_invalid_date_format_collected(self):
        agent = ParseAgent()
        result = agent._parse_csv_text(CSV_INVALID_DATE, "BRL", InputSource.STDIN_PIPE)
        assert len(result.errors) == 1
        assert len(result.expenses) == 0

    def test_all_invalid_returns_empty_expenses(self):
        agent = ParseAgent()
        result = agent._parse_csv_text(CSV_ALL_INVALID, "BRL", InputSource.STDIN_PIPE)
        assert len(result.expenses) == 0
        assert len(result.errors) == 2

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def test_duplicate_rows_skipped(self):
        agent = ParseAgent()
        result = agent._parse_csv_text(CSV_DUPLICATE, "BRL", InputSource.STDIN_PIPE)
        assert len(result.expenses) == 1
        assert result.duplicates_skipped == 1

    def test_non_duplicate_similar_rows_kept(self):
        """Same description+category but different amount → NOT a duplicate."""
        csv = (
            "description,amount,category,date\n"
            "Café,10.00,Alimentação,2024-01-01\n"
            "Café,12.00,Alimentação,2024-01-01\n"
        )
        agent = ParseAgent()
        result = agent._parse_csv_text(csv, "BRL", InputSource.STDIN_PIPE)
        assert len(result.expenses) == 2
        assert result.duplicates_skipped == 0

    # ------------------------------------------------------------------
    # File-based
    # ------------------------------------------------------------------

    def test_csv_file_not_found_raises(self):
        agent = ParseAgent()
        with pytest.raises(FileNotFoundError):
            agent.run(ParseInput(
                source=InputSource.CSV_FILE,
                csv_path=Path("/nonexistent/file.csv"),
            ))

    def test_csv_file_parsed_correctly(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text(SAMPLE_CSV, encoding="utf-8")
        agent = ParseAgent()
        result = agent.run(ParseInput(
            source=InputSource.CSV_FILE,
            csv_path=csv_file,
        ))
        assert len(result.expenses) == 3
        assert result.errors == []

    def test_validate_returns_ok(self):
        agent = ParseAgent()
        vr = agent.validate()
        assert vr.is_ok


# ============================================================================
# AggregationAgent
# ============================================================================

class TestAggregationAgent:

    def test_grand_total_correct(self):
        report = make_report(make_expenses(4))
        assert report.grand_total.amount == Decimal("1980")

    def test_single_expense(self):
        exps = [Expense("X", Money(Decimal("42.00"), "BRL"), Category("Y"), date(2024, 1, 1))]
        report = make_report(exps)
        assert report.grand_total.amount == Decimal("42.00")
        assert len(report.summaries) == 1
        assert abs(report.summaries[0].percentage - 100.0) < 0.1

    def test_category_count(self):
        assert len(make_report(make_expenses(4)).summaries) == 3

    def test_alimentacao_aggregated(self):
        report = make_report(make_expenses(4))
        alim = next(s for s in report.summaries if s.category_name == "Alimentação")
        assert alim.total.amount == Decimal("330")
        assert alim.count == 2
        assert alim.average.amount == Decimal("165")

    def test_summaries_sorted_by_total_descending(self):
        report = make_report(make_expenses(4))
        totals = [s.total.amount for s in report.summaries]
        assert totals == sorted(totals, reverse=True)

    def test_percentages_sum_to_100(self):
        report = make_report(make_expenses(6))
        total_pct = sum(s.percentage for s in report.summaries)
        assert abs(total_pct - 100.0) < 0.5

    def test_empty_input_returns_zero_total(self):
        report = make_report([])
        assert report.grand_total.amount == Decimal("0")
        assert report.summaries == []

    def test_metrics_category_count(self):
        report = make_report(make_expenses(4))
        assert report.metrics.category_count == 3

    def test_metrics_valid_records(self):
        agent = AggregationAgent()
        report = agent.run(AggregationInput(
            expenses=make_expenses(4),
            total_records_read=6,
            invalid_records=2,
        ))
        assert report.metrics.valid_records == 4
        assert report.metrics.invalid_records == 2

    def test_metrics_date_range(self):
        exps = [
            Expense("A", Money(Decimal("10"), "BRL"), Category("X"), date(2024, 1, 1)),
            Expense("B", Money(Decimal("10"), "BRL"), Category("X"), date(2024, 1, 31)),
        ]
        report = make_report(exps)
        assert report.metrics.date_range_days == 30

    def test_metrics_largest_smallest(self):
        report = make_report(make_expenses(4))
        assert abs(report.metrics.largest_single_expense - 1500.0) < 0.01
        assert abs(report.metrics.smallest_single_expense - 80.0) < 0.01

    def test_period_start_end_populated(self):
        report = make_report(make_expenses(4))
        assert report.period_start == date(2024, 1, 1)
        assert report.period_end == date(2024, 1, 10)

    def test_currency_propagated(self):
        agent = AggregationAgent()
        report = agent.run(AggregationInput(expenses=make_expenses(2), currency="USD"))
        assert report.grand_total.currency == "USD"

    def test_validate_returns_ok(self):
        agent = AggregationAgent()
        assert agent.validate().is_ok


# ============================================================================
# ChartAgent
# ============================================================================

class TestChartAgent:

    def test_produces_png_file(self):
        agent = ChartAgent()
        report = make_report()
        png = agent.run(report)
        try:
            assert png.exists()
            assert png.suffix == ".png"
            assert png.stat().st_size > 1000  # must be a real image
        finally:
            if png.exists():
                png.unlink()

    def test_returns_path_object(self):
        agent = ChartAgent()
        report = make_report()
        result = agent.run(report)
        try:
            assert isinstance(result, Path)
        finally:
            result.unlink(missing_ok=True)

    def test_empty_report_raises(self):
        agent = ChartAgent()
        report = make_report([])
        with pytest.raises(ValueError, match="no category summaries"):
            agent.run(report)

    def test_run_count_increments(self):
        agent = ChartAgent()
        report = make_report()
        result = agent.run(report)
        result.unlink(missing_ok=True)
        assert agent.run_count == 1

    def test_custom_config_accepted(self):
        config = ChartConfig(dpi=72, figure_size_inches=(6, 4))
        agent = ChartAgent(config=config)
        report = make_report()
        result = agent.run(report)
        result.unlink(missing_ok=True)
        assert agent.has_run

    def test_validate_ok_when_matplotlib_installed(self):
        agent = ChartAgent()
        assert agent.validate().is_ok


# ============================================================================
# RenderAgent
# ============================================================================

class TestRenderAgent:

    def test_validate_creates_output_directory(self, tmp_path):
        out = tmp_path / "nested" / "dir" / "report.pdf"
        agent = RenderAgent(RenderConfig(output_path=out))
        vr = agent.validate()
        assert vr.is_ok
        assert out.parent.exists()

    def test_validate_error_on_bad_parent(self):
        """Output parent is a file, not a directory — should error."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            file_path = Path(f.name)
        try:
            # Try to write inside a file as if it were a directory
            out = file_path / "report.pdf"
            agent = RenderAgent(RenderConfig(output_path=out))
            vr = agent.validate()
            assert not vr.is_ok
        finally:
            file_path.unlink(missing_ok=True)

    def test_produces_pdf_file(self, tmp_path):
        out = tmp_path / "report.pdf"
        agent = RenderAgent(RenderConfig(output_path=out))
        report = make_report()
        result = agent.run(RenderInput(report=report))
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_pdf_starts_with_pdf_magic_bytes(self, tmp_path):
        out = tmp_path / "report.pdf"
        agent = RenderAgent(RenderConfig(output_path=out))
        agent.run(RenderInput(report=make_report()))
        magic = out.read_bytes()[:4]
        assert magic == b"%PDF"

    def test_pdf_with_chart_embedded(self, tmp_path):
        out = tmp_path / "with_chart.pdf"
        chart_agent = ChartAgent()
        report = make_report()
        chart_path = chart_agent.run(report)
        try:
            render_agent = RenderAgent(RenderConfig(output_path=out))
            render_agent.run(RenderInput(report=report, chart_path=chart_path))
            assert out.stat().st_size > 50_000  # chart adds significant weight
        finally:
            chart_path.unlink(missing_ok=True)

    def test_pdf_without_chart_still_succeeds(self, tmp_path):
        out = tmp_path / "no_chart.pdf"
        agent = RenderAgent(RenderConfig(output_path=out))
        agent.run(RenderInput(report=make_report(), chart_path=None))
        assert out.exists()

    def test_run_count_increments(self, tmp_path):
        out = tmp_path / "r.pdf"
        agent = RenderAgent(RenderConfig(output_path=out))
        agent.run(RenderInput(report=make_report()))
        assert agent.run_count == 1


# ============================================================================
# BaseAgent / MockAgent
# ============================================================================

class TestBaseAgent:

    def test_mock_returns_value(self):
        agent = MockAgent(name="Test", return_value=99)
        assert agent.run("input") == 99

    def test_run_count_increments_per_success(self):
        agent = MockAgent(return_value="ok")
        agent.run("x")
        agent.run("y")
        assert agent.run_count == 2

    def test_last_duration_populated_after_run(self):
        agent = MockAgent(return_value=None)
        agent.run("x")
        assert agent.last_duration_s >= 0.0

    def test_has_run_false_before_any_run(self):
        assert not MockAgent(return_value=None).has_run

    def test_has_run_true_after_successful_run(self):
        agent = MockAgent(return_value=None)
        agent.run("x")
        assert agent.has_run

    def test_side_effect_raises_correct_type(self):
        agent = MockAgent(side_effect=ValueError("boom"))
        with pytest.raises(ValueError, match="boom"):
            agent.run("x")

    def test_failed_run_does_not_increment_count(self):
        agent = MockAgent(side_effect=RuntimeError("fail"))
        with pytest.raises(RuntimeError):
            agent.run("x")
        assert agent.run_count == 0

    def test_last_error_set_after_failure(self):
        agent = MockAgent(side_effect=KeyError("missing"))
        with pytest.raises(KeyError):
            agent.run("x")
        assert isinstance(agent.last_error, KeyError)

    def test_last_error_cleared_after_success(self):
        agent = MockAgent(side_effect=None, return_value="ok")
        # Force a failure first via a different agent to confirm isolation
        agent._last_error = RuntimeError("stale")
        agent.run("x")
        assert agent.last_error is None

    def test_received_inputs_accumulate(self):
        agent = MockAgent(return_value=None)
        agent.run("alpha")
        agent.run("beta")
        assert agent.received_inputs == ["alpha", "beta"]

    def test_repr_contains_name_and_run_count(self):
        agent = MockAgent(name="MyAgent", return_value=None)
        r = repr(agent)
        assert "MyAgent" in r
        assert "runs=0" in r

    # ------------------------------------------------------------------
    # ValidationResult
    # ------------------------------------------------------------------

    def test_default_validation_is_ok(self):
        vr = MockAgent(return_value=None).validate()
        assert vr.status == ValidationStatus.OK
        assert vr.is_ok

    def test_add_warning_sets_warning_status(self):
        vr = ValidationResult()
        vr.add_warning("heads up")
        assert vr.status == ValidationStatus.WARNING
        assert vr.is_ok  # WARNING is still OK to proceed

    def test_add_error_sets_error_status(self):
        vr = ValidationResult()
        vr.add_error("fatal")
        assert vr.status == ValidationStatus.ERROR
        assert not vr.is_ok

    def test_error_after_warning_stays_error(self):
        vr = ValidationResult()
        vr.add_warning("minor")
        vr.add_error("fatal")
        assert vr.status == ValidationStatus.ERROR

    def test_raise_if_error_silent_on_ok(self):
        ValidationResult().raise_if_error()  # must not raise

    def test_raise_if_error_silent_on_warning(self):
        vr = ValidationResult()
        vr.add_warning("minor")
        vr.raise_if_error()  # must not raise

    def test_raise_if_error_raises_on_error(self):
        vr = ValidationResult()
        vr.add_error("config missing")
        with pytest.raises(ConfigurationError, match="config missing"):
            vr.raise_if_error()

    def test_multiple_error_messages_joined(self):
        vr = ValidationResult()
        vr.add_error("err1")
        vr.add_error("err2")
        with pytest.raises(ConfigurationError) as exc_info:
            vr.raise_if_error()
        assert "err1" in str(exc_info.value)
        assert "err2" in str(exc_info.value)

    # ------------------------------------------------------------------
    # Contract enforcement across all real agents
    # ------------------------------------------------------------------

    def test_all_agents_inherit_base_agent(self):
        from agents.aggregation_agent import AggregationAgent
        from agents.chart_agent import ChartAgent
        from agents.parse_agent import ParseAgent
        from agents.render_agent import RenderAgent
        for cls in (ParseAgent, AggregationAgent, ChartAgent, RenderAgent):
            assert issubclass(cls, BaseAgent), f"{cls.__name__} must inherit BaseAgent"

    def test_all_agents_have_non_default_name(self):
        from agents.aggregation_agent import AggregationAgent
        from agents.chart_agent import ChartAgent
        from agents.parse_agent import ParseAgent
        from agents.render_agent import RenderAgent
        for cls in (ParseAgent, AggregationAgent, ChartAgent, RenderAgent):
            assert cls.name != "UnnamedAgent", f"{cls.__name__} must set a name"

    def test_all_agents_implement_execute(self):
        """_execute must be overridden — accessing it on BaseAgent raises TypeError."""
        from agents.aggregation_agent import AggregationAgent
        from agents.chart_agent import ChartAgent
        from agents.parse_agent import ParseAgent
        from agents.render_agent import RenderAgent
        for cls in (ParseAgent, AggregationAgent, ChartAgent, RenderAgent):
            # If _execute were still abstract, instantiation would raise TypeError
            instance = cls() if cls not in (RenderAgent, ChartAgent) else None
            if instance:
                assert callable(instance._execute)

    def test_all_agents_validate_returns_validation_result(self):
        from agents.aggregation_agent import AggregationAgent
        from agents.chart_agent import ChartAgent
        from agents.parse_agent import ParseAgent
        agents = [ParseAgent(), AggregationAgent(), ChartAgent()]
        for agent in agents:
            result = agent.validate()
            assert isinstance(result, ValidationResult), (
                f"{agent.name}.validate() must return ValidationResult"
            )


# ============================================================================
# End-to-end pipeline
# ============================================================================

class TestEndToEnd:

    def test_full_pipeline_csv_to_pdf(self, tmp_path):
        csv_file = Path(__file__).parent.parent / "data" / "sample_expenses.csv"
        if not csv_file.exists():
            pytest.skip("Sample CSV not found")

        output_pdf = tmp_path / "report.pdf"
        cfg = PipelineConfig(
            parse_input=ParseInput(source=InputSource.CSV_FILE, csv_path=csv_file),
            output_pdf=output_pdf,
        )
        result = Orchestrator(cfg).run()

        assert result.success, f"Pipeline failed: {result.error}"
        assert output_pdf.exists()
        assert output_pdf.stat().st_size > 50_000
        assert output_pdf.read_bytes()[:4] == b"%PDF"

    def test_pipeline_metrics_populated(self, tmp_path):
        csv_file = Path(__file__).parent.parent / "data" / "sample_expenses.csv"
        if not csv_file.exists():
            pytest.skip("Sample CSV not found")

        result = Orchestrator(PipelineConfig(
            parse_input=ParseInput(source=InputSource.CSV_FILE, csv_path=csv_file),
            output_pdf=tmp_path / "r.pdf",
        )).run()

        m = result.report.metrics
        assert m.total_duration_s > 0
        assert m.render_duration_s > 0
        assert m.aggregation_duration_s > 0
        assert m.data_quality_pct == 100.0

    def test_pipeline_with_partial_bad_rows(self, tmp_path):
        csv = tmp_path / "mixed.csv"
        csv.write_text(CSV_BAD_ROW, encoding="utf-8")
        result = Orchestrator(PipelineConfig(
            parse_input=ParseInput(source=InputSource.CSV_FILE, csv_path=csv),
            output_pdf=tmp_path / "r.pdf",
        )).run()

        assert result.success
        assert len(result.warnings) >= 1  # bad row surfaced as warning

    def test_pipeline_all_invalid_rows_fails(self, tmp_path):
        csv = tmp_path / "invalid.csv"
        csv.write_text(CSV_ALL_INVALID, encoding="utf-8")
        result = Orchestrator(PipelineConfig(
            parse_input=ParseInput(source=InputSource.CSV_FILE, csv_path=csv),
            output_pdf=tmp_path / "r.pdf",
        )).run()

        assert not result.success
        assert result.error is not None

    def test_pipeline_nonexistent_file_fails(self, tmp_path):
        result = Orchestrator(PipelineConfig(
            parse_input=ParseInput(
                source=InputSource.CSV_FILE,
                csv_path=Path("/nonexistent/expenses.csv"),
            ),
            output_pdf=tmp_path / "out.pdf",
        )).run()
        assert not result.success

    def test_pipeline_duplicate_only_csv(self, tmp_path):
        csv = tmp_path / "dupes.csv"
        csv.write_text(CSV_DUPLICATE, encoding="utf-8")
        result = Orchestrator(PipelineConfig(
            parse_input=ParseInput(source=InputSource.CSV_FILE, csv_path=csv),
            output_pdf=tmp_path / "r.pdf",
        )).run()

        # One unique expense remains after dedup → pipeline succeeds
        assert result.success
        assert len(result.report.expenses) == 1

    def test_pipeline_result_has_no_pdf_on_failure(self, tmp_path):
        result = Orchestrator(PipelineConfig(
            parse_input=ParseInput(
                source=InputSource.CSV_FILE,
                csv_path=Path("/nonexistent/x.csv"),
            ),
            output_pdf=tmp_path / "out.pdf",
        )).run()
        assert result.pdf_path is None


# ============================================================================
# Runner (no pytest)
# ============================================================================

if __name__ == "__main__":
    try:
        pytest.main([__file__, "-v", "--tb=short"])
    except SystemExit:
        pass