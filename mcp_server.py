
import json
import hashlib
import threading
from pathlib import Path

from fastmcp import FastMCP

DADOS = Path(__file__).resolve().parent.parent / "dados" / "situacao_tempestade.json"

mcp = FastMCP("ocean")

FROTA = {
    n["id"]: n
    for n in json.loads(DADOS.read_text(encoding="utf-8"))["navios"]
}

RESPOSTAS: dict[str, dict] = {}
LOCK = threading.RLock()


def _erro(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


@mcp.tool
def get_fleet_state() -> dict:
    """Snapshot dos navios, incluindo state_version."""
    with LOCK:
        return {"navios": [dict(n) for n in FROTA.values()]}


@mcp.tool
def set_speed(
    ship_id: str,
    speed_kn: float,
    expected_state_version: int,
    idempotency_key: str,
) -> dict:
    """Aplica uma velocidade-alvo.
    Garante controle de versão e idempotência.
    Regras de negócio devem ser validadas antes da chamada.
    """

    request = {
        "ship_id": ship_id,
        "speed_kn": speed_kn,
        "expected_state_version": expected_state_version,
    }

    request_hash = hashlib.sha256(
        json.dumps(request, sort_keys=True).encode("utf-8")
    ).hexdigest()

    with LOCK:

        # 1. Idempotência
        if idempotency_key in RESPOSTAS:
            registro = RESPOSTAS[idempotency_key]

            if registro["request_hash"] != request_hash:
                return _erro(
                    "IDEMPOTENCY_KEY_REUSED",
                    "Chave reutilizada com parâmetros diferentes",
                )

            return {
                **registro["response"],
                "duplicate": True,
            }

        # 2. Existência do recurso
        navio = FROTA.get(ship_id)

        if navio is None:
            return _erro("SHIP_NOT_FOUND", "Navio inexistente")

        # 3. Concorrência otimista
        if navio["state_version"] != expected_state_version:
            return _erro(
                "STATE_VERSION_CONFLICT",
                f"esperado {expected_state_version}, "
                f"atual {navio['state_version']}",
            )

        # 4. Aplicação da alteração
        antes = navio["velocidade_alvo_kn"]

        navio["velocidade_alvo_kn"] = speed_kn
        navio["state_version"] += 1

        resposta = {
            "status": "committed",
            "ship_id": ship_id,
            "before": antes,
            "after": speed_kn,
            "state_version": navio["state_version"],
            "duplicate": False,
        }

        # 5. Registro idempotente
        RESPOSTAS[idempotency_key] = {
            "request_hash": request_hash,
            "response": resposta,
        }

        return resposta


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8000)
