"""Verificación/setup de allowances del contrato CTF antes de operar (Fase 3).

`py-clob-client-v2` expone esto como una llamada de API (`get_balance_allowance`
/ `update_balance_allowance`), no como una transacción on-chain manual que este
proyecto tenga que construir -- el cliente ya abstrae la firma y el envío de la
transacción de aprobación cuando hace falta. Sólo se verifica/asegura el
allowance de COLLATERAL (USDC): la estrategia de arb sólo compra y nunca vende
antes de la resolución, así que nunca hace falta transferir tokens
condicionales de vuelta al exchange -- no se gestiona allowance de CONDITIONAL.
"""
from __future__ import annotations

import logging

from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

logger = logging.getLogger(__name__)


def _min_allowance(response: dict) -> float:
    """La respuesta real de `get_balance_allowance` trae `allowances` (plural):
    un dict `{contract_address: allowance}`, uno por cada contrato del exchange
    (v1, v2, neg-risk, etc.) -- no un único campo `allowance` (verificado
    empíricamente en la VPS contra la cuenta real; ver CLAUDE.md, sección
    Fase 3). Se usa el mínimo de todos: si cualquiera de esos contratos no
    tiene allowance, una orden que intente pasar por ese exchange fallaría
    igual, así que "operable" significa que TODOS lo están, no sólo uno."""
    allowances = response.get("allowances") or {}
    if not allowances:
        return 0.0
    return min(float(v or 0) for v in allowances.values())


def ensure_collateral_allowance(client) -> bool:
    """Verifica el allowance de COLLATERAL y lo actualiza si hace falta.

    Devuelve True si, al terminar, el allowance está en un estado operable.
    No lanza excepción por un allowance insuficiente -- lo loguea con
    severidad alta para que quien opera el kill-switch manual decida, porque
    intentar "arreglarlo solo" indefinidamente no es el comportamiento que se
    quiere para dinero real sin supervisión.
    """
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    current = client.get_balance_allowance(params)
    allowance = _min_allowance(current)

    if allowance > 0:
        logger.info("Allowance de COLLATERAL ya está seteado (mínimo entre contratos: %.4g)", allowance)
        return True

    logger.warning("Allowance de COLLATERAL en 0 -- intentando actualizar vía update_balance_allowance")
    client.update_balance_allowance(params)
    refreshed = client.get_balance_allowance(params)
    refreshed_allowance = _min_allowance(refreshed)

    if refreshed_allowance <= 0:
        logger.critical(
            "Allowance de COLLATERAL sigue en 0 tras update_balance_allowance -- "
            "no se puede operar con capital real hasta resolver esto manualmente."
        )
        return False

    logger.info("Allowance de COLLATERAL actualizado a %.4g", refreshed_allowance)
    return True
