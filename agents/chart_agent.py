"""
agents/chart_agent.py
=====================
Subagente responsável por renderizar um gráfico de pizza com qualidade de publicação
a partir dos dados agregados por categoria.

Utiliza matplotlib com um estilo cuidadosamente ajustado para que o gráfico seja legível
quando incorporado em um PDF A4 em qualquer nível de zoom.

Entrada : ExpenseReport
Saída   : Caminho para o arquivo PNG gerado (temporário)
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # backend não interativo — seguro para servidor / CLI
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from agents.base import BaseAgent, ValidationResult, ValidationStatus
from core.models import ExpenseReport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paleta de cores — acessível e adequada para impressão
# ---------------------------------------------------------------------------
_PALETTE = [
    "#2E86AB",  # Azul
    "#A23B72",  # Roxo
    "#F18F01",  # Laranja
    "#C73E1D",  # Vermelho
    "#3B1F2B",  # Bordô escuro
    "#44BBA4",  # Verde-água
    "#E94F37",  # Coral
    "#393E41",  # Cinza carvão
    "#F5A623",  # Âmbar
    "#7B2D8B",  # Violeta
    "#27AE60",  # Verde
    "#2980B9",  # Azul aço
]


@dataclass
class ChartConfig:
    """Configuração de renderização."""
    figure_size_inches: tuple[float, float] = (8, 6)
    dpi: int = 150
    title_font_size: int = 14
    label_font_size: int = 9
    pct_distance: float = 0.80   # distância dos rótulos de % até o centro
    legend_outside: bool = True   # posiciona a legenda à direita do gráfico
    min_pct_for_label: float = 3.0  # fatias menores que isso não exibem %


class ChartAgent(BaseAgent[ExpenseReport, Path]):
    """
    Renderiza um gráfico de pizza formatado a partir de um ExpenseReport.

    Herda temporização, logging e tratamento de erros de ``BaseAgent``.
    O gráfico é salvo em um arquivo PNG temporário cujo caminho é retornado.
    O orquestrador é responsável por deletar o arquivo após a incorporação.
    """

    name = "ChartAgent"

    def __init__(self, config: ChartConfig | None = None) -> None:
        super().__init__()
        self.config = config or ChartConfig()

    def validate(self) -> ValidationResult:
        result = ValidationResult()
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            result.add_error("matplotlib não está instalado — não é possível renderizar o gráfico")
        return result

    def _execute(self, report: ExpenseReport) -> Path:
        """Gera o PNG do gráfico de pizza e retorna seu caminho."""
        cfg = self.config
        summaries = report.summaries

        if not summaries:
            raise ValueError("Não é possível renderizar o gráfico: não há dados de categorias")

        labels = [s.category_name for s in summaries]
        sizes  = [float(s.total.amount) for s in summaries]
        colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(summaries))]

        # Destaca levemente a maior fatia para ênfase visual
        explode = [0.04 if i == 0 else 0.0 for i in range(len(summaries))]

        fig, ax = plt.subplots(figsize=cfg.figure_size_inches, dpi=cfg.dpi)
        fig.patch.set_facecolor("white")

        # ------------------------------------------------------------------
        # Desenha o gráfico de pizza
        # ------------------------------------------------------------------
        wedges, texts, autotexts = ax.pie(
            sizes,
            labels=None,          # rótulos são exibidos na legenda
            colors=colors,
            explode=explode,
            autopct=lambda pct: f"{pct:.1f}%" if pct >= cfg.min_pct_for_label else "",
            pctdistance=cfg.pct_distance,
            startangle=140,
            wedgeprops={"linewidth": 0.8, "edgecolor": "white"},
            textprops={"fontsize": cfg.label_font_size},
        )

        for at in autotexts:
            at.set_fontsize(cfg.label_font_size - 1)
            at.set_fontweight("bold")
            at.set_color("white")

        # ------------------------------------------------------------------
        # Legenda com nome da categoria + valor + %
        # ------------------------------------------------------------------
        legend_labels = [
            f"{s.category_name}  –  {s.total}  ({s.percentage:.1f}%)"
            for s in summaries
        ]
        legend_patches = [
            mpatches.Patch(facecolor=colors[i], label=legend_labels[i])
            for i in range(len(summaries))
        ]

        if cfg.legend_outside:
            ax.legend(
                handles=legend_patches,
                loc="center left",
                bbox_to_anchor=(1.0, 0.5),
                fontsize=cfg.label_font_size,
                frameon=True,
                framealpha=0.9,
                edgecolor="#cccccc",
            )
        else:
            ax.legend(
                handles=legend_patches,
                loc="lower center",
                ncol=2,
                fontsize=cfg.label_font_size - 1,
            )

        # ------------------------------------------------------------------
        # Título
        # ------------------------------------------------------------------
        currency = report.grand_total.currency
        ax.set_title(
            f"Distribuição de Despesas por Categoria\nTotal: {report.grand_total}",
            fontsize=cfg.title_font_size,
            fontweight="bold",
            pad=18,
            color="#222222",
        )

        plt.tight_layout()

        # ------------------------------------------------------------------
        # Salva em arquivo temporário
        # ------------------------------------------------------------------
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        fig.savefig(tmp.name, bbox_inches="tight", dpi=cfg.dpi, facecolor="white")
        plt.close(fig)

        logger.info("[%s] gráfico salvo em %s", self.name, tmp.name)
        return Path(tmp.name)