from __future__ import annotations

import pytest

from crewai_invest_reporter.domain.models import AssetClass, Holding, Portfolio


@pytest.fixture
def sample_portfolio() -> Portfolio:
    return Portfolio(
        holdings=(
            Holding("Petrobras PN", AssetClass.STOCK, "NuInvest", 3000.0, ticker="PETR4"),
            Holding("Maxi Renda", AssetClass.REAL_ESTATE_FUND, "NuInvest", 1000.0, ticker="MXRF11"),
            Holding(
                "Petrobras PN fracionario", AssetClass.STOCK, "NuInvest", 500.0, ticker="PETR4"
            ),
            Holding(
                "Fundo Multimercado",
                AssetClass.MUTUAL_FUND,
                "NuInvest",
                800.0,
                cnpj="11222333000181",
            ),
        )
    )
