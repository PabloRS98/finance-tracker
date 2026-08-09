#!/usr/bin/env bash
# Pide todas las páginas y exige 200. Uso: comprobar-rutas.sh <puerto>
#
# Un healthcheck en verde no basta como prueba de despliegue: hay que pedir las
# páginas de verdad, que es lo que devolvía 500 mientras el contenedor figuraba
# como sano.
#
# Desde que la app es multiusuario hay que entrar primero: sin sesión todas las
# páginas contestan 303 a /entrar, que es lo correcto, y comprobarlo así de paso
# verifica que un contenedor recién levantado deja crear la primera cuenta.
set -euo pipefail

puerto="$1"
base="http://localhost:$puerto"
galletas=$(mktemp)
trap 'rm -f "$galletas"' EXIT

# El token de CSRF es el valor de la propia cookie (double-submit), así que se
# lee del tarro en vez de rascarlo del HTML.
token() {
    awk '$6 == "csrftoken" { print $7 }' "$galletas" | tail -n 1
}

curl -s -c "$galletas" -b "$galletas" -o /dev/null "$base/entrar"

curl -s -c "$galletas" -b "$galletas" -o /dev/null \
    -H "X-CSRF-Token: $(token)" \
    -d "nombre=CI" "$base/usuarios"

# El id de la cuenta recién creada, sacado de su propia tarjeta. No se da por
# hecho que sea el 1: la migración de una base con datos ya crea una cuenta, así
# que en una actualización esta es la segunda.
id_ci=$(curl -s -b "$galletas" "$base/entrar" | tr '<' '\n' | awk -F'"' '
    /name="usuario_id" value=/ { id = $6 }
    /span class="nombre">CI$/  { print id; exit }
')
if [ -z "$id_ci" ]; then
    echo "no se pudo crear la cuenta de prueba: no aparece en /entrar"
    exit 1
fi

curl -s -c "$galletas" -b "$galletas" -o /dev/null \
    -H "X-CSRF-Token: $(token)" \
    -d "usuario_id=$id_ci" "$base/entrar"

# Un login fallido también contesta 303 —vuelve a /entrar con el aviso—, así que
# lo que dice si funcionó es que haya cookie de sesión, no el código.
if ! awk '$6 == "sesion"' "$galletas" | grep -q .; then
    echo "no se pudo entrar con la cuenta de prueba: sin cookie de sesión"
    exit 1
fi

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
    codigo=$(curl -s -b "$galletas" -o /dev/null -w '%{http_code}' "$base$ruta")
    if [ "$codigo" = "200" ]; then
        printf '  %-28s %s\n' "$ruta" "$codigo"
    else
        printf '  %-28s %s  <-- FALLO\n' "$ruta" "$codigo"
        fallos=$((fallos + 1))
    fi
done

# Sin sesión no puede verse nada: si esto devolviera 200, el aislamiento entre
# personas no estaría puesto en el contenedor aunque lo esté en los tests.
sin_sesion=$(curl -s -o /dev/null -w '%{http_code}' "$base/")
if [ "$sin_sesion" != "303" ]; then
    printf '  %-28s %s  <-- FALLO (sin sesión debería redirigir)\n' "/ (sin sesión)" "$sin_sesion"
    fallos=$((fallos + 1))
fi

if [ "$fallos" -gt 0 ]; then
    echo "$fallos comprobaciones fallidas"
    exit 1
fi
echo "las ${#rutas[@]} rutas responden 200, y sin sesión redirigen"
