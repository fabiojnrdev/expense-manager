# Expense Manager

[![tests](https://github.com/fabiojnrdev/expense-manager/actions/workflows/tests.yml/badge.svg)](https://github.com/fabiojnrdev/expense-tracker/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

CLI que converte um CSV de despesas em um relatório PDF com sumário executivo, tabela detalhada por despesa, resumo por categoria e gráfico de pizza — usando um pipeline de agentes desacoplados em vez de um script monolítico.

## Por que agentes?

O processamento é dividido em quatro etapas independentes, cada uma isolada em sua própria classe:

```
ParseAgent → AggregationAgent → ChartAgent → RenderAgent
```

Todos herdam de `BaseAgent[InputT, OutputT]`, uma base genérica que centraliza timing, logging estruturado e tratamento de erro — nenhum agente reimplementa isso. Cada agente recebe um contrato de entrada tipado (dataclass) e devolve um contrato de saída tipado; o `Orchestrator` é o único componente que conhece o fluxo completo, o que permite testar cada agente isoladamente (ver `tests/test_agent.py`) sem montar o pipeline inteiro.

A alternativa óbvia — um script sequencial de 300 linhas — seria mais rápida de escrever e mais difícil de testar, estender ou depurar em produção. A troca aqui é indireção por testabilidade, e para esse escopo (cada etapa é claramente separável e reutilizável) a troca compensa.

## Instalação

```bash
git clone https://github.com/fabiojnrdev/expense-manager.git
cd expense-manager
pip install -r requirements.txt
```

Requer Python 3.10+ (usa `list[str]`, `X | None` e `from __future__ import annotations`).

## Uso

```bash
python main.py despesas.csv --output relatorio.pdf --currency BRL --author "Seu Nome"
```

O CSV de entrada precisa das colunas `description`, `amount`, `category` (opcionais: `date`, `tags`). Aliases em português são reconhecidos automaticamente (`descricao`, `valor`, `categoria`, `data`) — ver `_COLUMN_ALIASES` em `agents/parse_agent.py`.

Exemplo mínimo de CSV:

```csv
description,amount,category,date,tags
Supermercado,250.00,Alimentação,2024-01-05,compras
Aluguel,1500.00,Moradia,2024-01-01,fixo
```

## Testes

```bash
python -m pytest tests/ -v
```

A suíte cobre value objects (`Money`, `Category`), cada agente isoladamente, e o pipeline completo ponta a ponta (CSV → PDF), incluindo caminhos de falha (arquivo inexistente, todas as linhas inválidas, apenas duplicatas). Roda automaticamente via GitHub Actions em toda push/PR para `main` — ver `.github/workflows/tests.yml`.

## Estrutura

```
core/
  models.py          # entidades de domínio (Expense, Money, Category, ExpenseReport)
  exceptions.py       # hierarquia de exceções da aplicação
agents/
  base.py              # BaseAgent genérico — timing, logging, tratamento de erro
  parse_agent.py       # CSV/terminal/stdin → list[Expense]
  aggregation_agent.py # groupby por categoria via pandas → ExpenseReport
  chart_agent.py       # ExpenseReport → gráfico de pizza (matplotlib)
  render_agent.py      # ExpenseReport + gráfico → PDF (ReportLab/Platypus)
  orchestrator.py       # conecta os quatro agentes, injeta métricas, trata erros
tests/
  test_agent.py         # testes unitários e de integração
main.py                  # entrada da CLI
```

## Limitações conhecidas

- Formatação de moeda em `render_agent.py` está parcialmente hardcoded para `R$` no bloco de KPIs, mesmo o relatório aceitando `currency` configurável — pendente de correção.
- Sem lint/type-check automatizado no CI (mypy/ruff) ainda.
- Prompts da sessão interativa de terminal (`ParseAgent._from_terminal`) estão em pt-BR por decisão de UX; o restante do código (docstrings, comentários, mensagens de exceção) está em inglês.

## Licença

MIT — ver [LICENSE](LICENSE).
