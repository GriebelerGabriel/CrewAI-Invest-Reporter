"""Asset-class specific analysis guidance injected into the asset report prompts.

Keeping this in code (instead of one generic prompt) makes each asset type judged by the
metrics that matter for it: P/L is meaningless for an FII, vacância is meaningless for a bank.
"""

from __future__ import annotations

from crewai_invest_reporter.domain.models import AssetClass

_ASSET_CLASS_LABELS: dict[AssetClass, str] = {
    AssetClass.STOCK: "Ação (empresa listada na B3)",
    AssetClass.REAL_ESTATE_FUND: "Fundo Imobiliário (FII)",
    AssetClass.ETF: "ETF (fundo de índice)",
    AssetClass.BDR: "BDR (recibo de ação estrangeira)",
    AssetClass.MUTUAL_FUND: "Fundo de investimento (não listado)",
}

_GUIDELINES: dict[AssetClass, str] = {
    AssetClass.STOCK: (
        "Company stock. Key reports: earnings releases (category 'Dados Econômico-Financeiros'), "
        "fatos relevantes, comunicados and dividend notices. Assess revenue/EBITDA/net income "
        "trends, margins, net debt/EBITDA, guidance, payout and dividends, P/L, P/VP and DY."
    ),
    AssetClass.REAL_ESTATE_FUND: (
        "Real estate fund (FII). Key reports: the latest 'Relatório Gerencial' and 'Informe "
        "Mensal', fatos relevantes and avisos aos cotistas (rendimentos). Assess the portfolio "
        "(properties or CRIs), vacancy (física/financeira), default (inadimplência), lease "
        "maturity/indexers, leverage, distributions per quota and their sustainability, P/VP "
        "and DY. P/L does not apply to FIIs."
    ),
    AssetClass.ETF: (
        "Exchange traded fund. There are usually no issuer filings: rely on the market data and "
        "news, and on the manager's factsheet (lâmina) only if provided. Assess the underlying "
        "index, management fee, liquidity, currency exposure and concentration. P/L and P/VP of "
        "the ETF itself do not apply."
    ),
    AssetClass.BDR: (
        "BDR of a foreign company. Key reports: the underlying company's SEC filings (earnings "
        "press release in the 8-K, other 8-Ks), written in English: report their figures in "
        "pt-BR keeping the original currency. Assess results, guidance and the BRL/USD exposure."
    ),
    AssetClass.MUTUAL_FUND: (
        "Unlisted investment fund identified by its CNPJ. Key reports: the monthly management "
        "report (carta/relatório do gestor) and informes, when published on FundosNET. Assess "
        "strategy, returns vs benchmark (CDI, Ibovespa), volatility, fees, liquidity "
        "(cotização/resgate) and the manager. Market multiples do not apply: write "
        "'Não aplicável'."
    ),
}


def asset_class_label(asset_class: AssetClass) -> str:
    return _ASSET_CLASS_LABELS.get(asset_class, asset_class.value)


def analysis_guidelines(asset_class: AssetClass) -> str:
    return _GUIDELINES.get(asset_class, _GUIDELINES[AssetClass.STOCK])
