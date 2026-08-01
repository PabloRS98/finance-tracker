"""Rebalanceo: desviación frente a los pesos objetivo y cuánto comprar.

No ejecuta nada ni propone ventas por defecto: solo dice cuánto falta o sobra de
cada cosa. Vender para rebalancear cristaliza plusvalías y peaje fiscal, así que
la vía por defecto es "compra lo que falta con dinero nuevo", que es como se
rebalancea una cartera en formación.
"""
from sqlalchemy.orm import Session

from ..models import PesoObjetivo
from .xray import invested_rows


def _reparto_actual(db: Session) -> tuple[dict[int, dict], float]:
    """{asset_id: {asset, valor}} y el total invertido, en moneda base."""
    posiciones = {}
    total = 0.0
    for fila in invested_rows(db):
        asset = fila["asset"]
        posiciones[asset.id] = {"asset": asset, "valor": fila["value_base"]}
        total += fila["value_base"]
    return posiciones, total


def plan(db: Session, aportacion: float = 0.0) -> dict:
    """Desviación de cada activo con objetivo y qué comprar para corregirla.

    `aportacion` es dinero nuevo que se va a meter: cambia el reparto, porque
    con él se puede corregir comprando en vez de vendiendo. Sin aportación, las
    cifras dicen simplemente cuánto sobra o falta hoy.
    """
    objetivos = db.query(PesoObjetivo).all()
    posiciones, total_actual = _reparto_actual(db)
    if not objetivos:
        return {"filas": [], "total_actual": total_actual, "objetivo_total": 0.0,
                "aportacion": aportacion, "sin_objetivo": total_actual}

    # Con aportación, el objetivo se calcula sobre la cartera que quedará
    total_futuro = total_actual + max(aportacion, 0.0)

    filas = []
    valor_con_objetivo = 0.0
    suma_objetivo = 0.0
    for objetivo in objetivos:
        posicion = posiciones.get(objetivo.asset_id)
        valor = posicion["valor"] if posicion else 0.0
        valor_con_objetivo += valor
        suma_objetivo += objetivo.porcentaje

        deseado = total_futuro * objetivo.porcentaje / 100.0
        actual_pct = (100.0 * valor / total_actual) if total_actual > 0 else 0.0
        filas.append({
            "asset": objetivo.asset if objetivo.asset is not None else (posicion or {}).get("asset"),
            "objetivo_pct": objetivo.porcentaje,
            "actual_pct": round(actual_pct, 2),
            "desviacion_pct": round(actual_pct - objetivo.porcentaje, 2),
            "valor": round(valor, 2),
            "deseado": round(deseado, 2),
            # Positivo = comprar; negativo = estás por encima del objetivo
            "ajuste": round(deseado - valor, 2),
        })

    # Se ordena por lo que más falta: es el orden en que uno actúa
    filas.sort(key=lambda f: f["ajuste"], reverse=True)
    return {
        "filas": filas,
        "total_actual": round(total_actual, 2),
        "total_futuro": round(total_futuro, 2),
        "objetivo_total": round(suma_objetivo, 2),
        "aportacion": round(max(aportacion, 0.0), 2),
        # Lo que está en cartera pero no tiene objetivo asignado: si no se dice,
        # los porcentajes parecen no cuadrar y no se entiende por qué.
        "sin_objetivo": round(total_actual - valor_con_objetivo, 2),
    }


def reparto_de_aportacion(db: Session, aportacion: float) -> list[dict]:
    """Cómo repartir una aportación entre lo que está por debajo del objetivo.

    Se reparte proporcionalmente a lo que le falta a cada uno, y solo entre los
    que van cortos: meter dinero en el que ya sobrepasa su peso agravaría la
    desviación en vez de corregirla.
    """
    if aportacion <= 0:
        return []

    detalle = plan(db, aportacion)
    faltantes = [f for f in detalle["filas"] if f["ajuste"] > 0]
    if not faltantes:
        return []

    total_falta = sum(f["ajuste"] for f in faltantes)
    reparto = []
    for fila in faltantes:
        parte = aportacion * fila["ajuste"] / total_falta
        reparto.append({
            "asset": fila["asset"],
            "importe": round(parte, 2),
            "pct_de_la_aportacion": round(100.0 * parte / aportacion, 1),
        })
    return reparto
