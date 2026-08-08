"""Detección y fusión de activos duplicados.

El mismo valor comprado en dos sitios acaba como dos activos: Trade Republic
exporta por ISIN y lo llama "Apple Inc.", Revolut exporta por ticker y lo llama
"AAPL". Cada uno lleva su posición y su coste medio, así que la cartera enseña
dos líneas de lo mismo, los pesos del X-Ray salen partidos y el P&L no se puede
leer de un vistazo.

Fusionar consiste en colgar todas las operaciones de un solo activo. No se
recalcula nada: la posición y el coste medio se derivan de las operaciones, así
que al juntarlas el resultado sale solo y sigue cuadrando con los extractos.
"""
from sqlalchemy.orm import Session, selectinload

from ..models import Asset, AssetType, Operation
from .portfolio import compute_position, posicion_cerrada

INVERTIBLE = (AssetType.ACCION, AssetType.CRIPTO)


def _clave_isin(asset: Asset) -> str | None:
    return asset.isin.strip().upper() if asset.isin else None


def _clave_ticker(asset: Asset) -> str | None:
    return asset.ticker.strip().upper() if asset.ticker else None


def _clave_nombre(asset: Asset) -> str | None:
    from ..routers.imports import _normalize_name  # mismo criterio que al importar

    normalizado = _normalize_name(asset.name or "")
    return normalizado or None


# De más a menos fiable. El ISIN identifica el valor sin ambigüedad; el ticker
# puede repetirse entre plazas; el nombre es una pista y por eso se avisa.
CRITERIOS = (
    ("ISIN", _clave_isin),
    ("ticker", _clave_ticker),
    ("nombre", _clave_nombre),
)


def candidatos(db: Session) -> list[dict]:
    """Grupos de activos que parecen el mismo valor.

    Cada grupo trae el motivo por el que se han juntado y si todos comparten
    divisa: fusionar activos en divisas distintas mezclaría dos escalas de
    precio en el mismo coste medio, así que esos se marcan y no se dejan fusionar.

    Las posiciones cerradas quedan fuera: un activo vendido entero es un registro
    histórico, no una línea repetida de la cartera. Un traspaso de bróker —vender
    en uno y comprar en otro el mismo día— dejaba aquí las dos etapas como si
    fueran un duplicado, y fusionarlas habría juntado dos escalas de precio bajo
    un solo coste medio. El aviso era falso.
    """
    assets = (
        db.query(Asset)
        .options(selectinload(Asset.operations))  # evita N+1: hay que calcular la posición de cada uno
        .filter(Asset.asset_type.in_(INVERTIBLE))
        .all()
    )
    assets = [a for a in assets if not posicion_cerrada(compute_position(a.operations))]

    vistos: set[int] = set()
    grupos: list[dict] = []
    for motivo, clave_de in CRITERIOS:
        por_clave: dict[str, list[Asset]] = {}
        for asset in assets:
            if asset.id in vistos:
                continue
            clave = clave_de(asset)
            if clave:
                por_clave.setdefault(clave, []).append(asset)

        for clave, miembros in por_clave.items():
            if len(miembros) < 2:
                continue
            vistos.update(a.id for a in miembros)
            divisas = {a.currency.value for a in miembros}
            grupos.append({
                "motivo": motivo,
                "clave": clave,
                "activos": sorted(miembros, key=lambda a: a.id),
                "divisas": sorted(divisas),
                "fusionable": len(divisas) == 1,
            })
    return grupos


def puede_fusionar(destino: Asset, origenes: list[Asset]) -> str | None:
    """Motivo por el que NO se puede fusionar, o None si se puede."""
    if not origenes:
        return "No se ha indicado ningún activo que fusionar"
    if any(a.id == destino.id for a in origenes):
        return "Un activo no se puede fusionar consigo mismo"

    todos = [destino] + origenes
    divisas = {a.currency.value for a in todos}
    if len(divisas) > 1:
        # Las operaciones no guardan divisa: heredan la del activo. Juntar dos
        # escalas de precio bajo una sola divisa falsearía el coste medio sin
        # dejar rastro, igual que pasaba al importar.
        return "Están en divisas distintas (%s): fusionarlos falsearía el coste medio" % (
            ", ".join(sorted(divisas))
        )

    tipos = {a.asset_type for a in todos}
    if len(tipos) > 1:
        return "No se puede fusionar una acción con una criptomoneda"
    return None


def fusionar(db: Session, destino: Asset, origenes: list[Asset]) -> dict:
    """Cuelga las operaciones de `origenes` del activo `destino` y los borra.

    Devuelve un resumen. No recalcula posiciones: se derivan de las operaciones,
    así que juntarlas basta. Completa el ISIN y el ticker del destino si le
    faltan y alguno de los otros lo traía, que es lo que evita que el duplicado
    vuelva a aparecer en la siguiente importación.
    """
    movidas = 0
    for origen in origenes:
        # Se reasigna el padre (op.asset), no op.asset_id a secas: Asset.operations
        # va con cascade="all, delete-orphan", así que si la colección del origen
        # las sigue conteniendo, al borrarlo se las lleva por delante y la fusión
        # destruye justo lo que venía a conservar. Reasignar actualiza las dos
        # colecciones a la vez (back_populates) sin marcarlas como huérfanas,
        # que es lo que pasaría con un remove() previo.
        for op in list(origen.operations):
            op.asset = destino
            movidas += 1
        if not destino.isin and origen.isin:
            destino.isin = origen.isin
        if not destino.ticker and origen.ticker:
            destino.ticker = origen.ticker
        if destino.region is None and origen.region is not None:
            destino.region = origen.region
        if destino.sector is None and origen.sector is not None:
            destino.sector = origen.sector

    # La cantidad manual heredada de la v2 deja de tener sentido en cuanto el
    # activo vive de operaciones: si se quedara, se sumaría a la posición real.
    if destino.operations or movidas:
        destino.quantity = None

    nombres = [a.name for a in origenes]
    db.flush()
    for origen in origenes:
        db.delete(origen)
    db.commit()
    return {"movidas": movidas, "absorbidos": nombres}


def posicion_por_cuenta(asset: Asset) -> list[dict]:
    """Desglose de la posición por cuenta/bróker.

    Es lo que se pierde al fusionar si no se enseña: después de juntar "Apple"
    de Trade Republic con "AAPL" de Revolut, saber cuánto hay en cada sitio
    sigue haciendo falta para cuadrar con los extractos.
    """
    from .portfolio import compute_position

    por_cuenta: dict[int | None, list[Operation]] = {}
    for op in asset.operations:
        por_cuenta.setdefault(op.account_id, []).append(op)
    if len(por_cuenta) < 2 and None in por_cuenta:
        return []  # todo en la misma cuenta (o sin cuenta): no hay nada que desglosar

    filas = []
    for ops in por_cuenta.values():
        posicion = compute_position(ops)
        if not posicion.quantity:
            continue
        cuenta = next((op.account for op in ops if op.account is not None), None)
        filas.append({
            "cuenta": cuenta.name if cuenta else "Sin cuenta",
            "cantidad": posicion.quantity,
            "coste_medio": posicion.avg_cost,
            "coste": posicion.cost_open,
            "valor": (asset.current_price * posicion.quantity) if asset.current_price else None,
        })
    return sorted(filas, key=lambda f: f["coste"], reverse=True)
