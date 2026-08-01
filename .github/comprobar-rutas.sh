#!/usr/bin/env bash
# Pide todas las páginas y exige 200. Uso: comprobar-rutas.sh <puerto>
#
# Un healthcheck en verde no basta como prueba de despliegue: hay que pedir las
# páginas de verdad, que es lo que devolvía 500 mientras el contenedor figuraba
# como sano.
set -euo pipefail

puerto="$1"
rutas=(
    /
    /activos
    /activos/duplicados
    /operaciones
    /operaciones/importar
    /transacciones
    /analisis
    /analisis/rebalanceo
    /recurrentes
    /categorias
    /salud
)

fallos=0
for ruta in "${rutas[@]}"; do
    codigo=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$puerto$ruta")
    if [ "$codigo" = "200" ]; then
        printf '  %-28s %s\n' "$ruta" "$codigo"
    else
        printf '  %-28s %s  <-- FALLO\n' "$ruta" "$codigo"
        fallos=$((fallos + 1))
    fi
done

if [ "$fallos" -gt 0 ]; then
    echo "$fallos rutas no responden 200"
    exit 1
fi
echo "las ${#rutas[@]} rutas responden 200"
