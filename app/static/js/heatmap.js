// ============================================================
// Mapa de la cartera: superficie = peso, color = variación del día.
//
// Se dibuja con divs y el algoritmo "squarified" (Bruls et al.), no con un
// plugin de Chart.js: son unas pocas decenas de líneas, evita vendorizar un
// bundle de terceros y deja los rectángulos como elementos del DOM, así que
// heredan los estilos y el modo privacidad de la app sin trabajo extra.
// ============================================================
(function () {
    "use strict";

    const box = document.getElementById("heatmap");
    if (!box) return;

    const datos = JSON.parse(box.dataset.items || "[]");
    if (!datos.length) return;

    // Verde/rojo según la variación del día, saturando a ±3%: más allá el ojo
    // ya no distingue y todo se vuelve un bloque del mismo color.
    const TOPE = 3;
    function color(variacion) {
        if (variacion === null || variacion === undefined) return "var(--surface-2, #2a2f3a)";
        const fuerza = Math.min(Math.abs(variacion) / TOPE, 1);
        const luz = 26 + (1 - fuerza) * 14;      // más plano = más apagado
        const sat = 20 + fuerza * 45;
        return "hsl(" + (variacion >= 0 ? 145 : 5) + ", " + sat + "%, " + luz + "%)";
    }

    // ---------- Squarified treemap ----------
    // Coloca las piezas en franjas, eligiendo cuándo cerrar cada franja para que
    // los rectángulos queden lo más cuadrados posible (una franja de rectángulos
    // muy alargados es ilegible).
    function peorRatio(fila, lado, escala) {
        const suma = fila.reduce((a, v) => a + v, 0) * escala;
        const max = Math.max.apply(null, fila) * escala;
        const min = Math.min.apply(null, fila) * escala;
        if (suma === 0) return Infinity;
        return Math.max((lado * lado * max) / (suma * suma), (suma * suma) / (lado * lado * min));
    }

    function repartir(valores, x, y, ancho, alto, escala, salida) {
        if (!valores.length) return;
        if (valores.length === 1) {
            salida.push({ x: x, y: y, w: ancho, h: alto });
            return;
        }
        const lado = Math.min(ancho, alto);
        let fila = [valores[0]];
        let corte = 1;
        while (corte < valores.length) {
            const candidata = fila.concat([valores[corte]]);
            if (peorRatio(candidata, lado, escala) > peorRatio(fila, lado, escala)) break;
            fila = candidata;
            corte += 1;
        }

        const sumaFila = fila.reduce((a, v) => a + v, 0) * escala;
        const grosor = sumaFila / lado;
        let avance = 0;
        for (const valor of fila) {
            const largo = (valor * escala) / grosor;
            if (ancho >= alto) {
                salida.push({ x: x, y: y + avance, w: grosor, h: largo });
            } else {
                salida.push({ x: x + avance, y: y, w: largo, h: grosor });
            }
            avance += largo;
        }

        const resto = valores.slice(corte);
        if (ancho >= alto) {
            repartir(resto, x + grosor, y, ancho - grosor, alto, escala, salida);
        } else {
            repartir(resto, x, y + grosor, ancho, alto - grosor, escala, salida);
        }
    }

    function pintar() {
        const ancho = box.clientWidth;
        const alto = box.clientHeight;
        if (!ancho || !alto) return;

        const valores = datos.map((d) => d.valor);
        const total = valores.reduce((a, v) => a + v, 0);
        if (total <= 0) return;

        const cajas = [];
        repartir(valores, 0, 0, ancho, alto, (ancho * alto) / total, cajas);

        box.textContent = "";
        cajas.forEach(function (caja, i) {
            const d = datos[i];
            const pct = (100 * d.valor) / total;
            const el = document.createElement("div");
            el.className = "heatmap-cell";
            el.style.cssText =
                "left:" + caja.x + "px;top:" + caja.y + "px;" +
                "width:" + Math.max(caja.w - 2, 0) + "px;height:" + Math.max(caja.h - 2, 0) + "px;" +
                "background:" + color(d.variacion);
            const variacion = d.variacion === null || d.variacion === undefined
                ? "sin variación"
                : (d.variacion >= 0 ? "+" : "") + d.variacion.toFixed(2) + "%";
            el.title = d.nombre + " · " + pct.toFixed(1) + "% de la cartera · " + variacion;

            // El texto solo cabe en las piezas grandes; en las pequeñas queda el
            // tooltip, que es mejor que un amasijo de letras recortadas.
            if (caja.w > 62 && caja.h > 34) {
                const nombre = document.createElement("strong");
                nombre.textContent = d.ticker || d.nombre;
                el.appendChild(nombre);
                if (caja.h > 52) {
                    const dato = document.createElement("span");
                    dato.textContent = variacion;
                    el.appendChild(dato);
                }
            }
            box.appendChild(el);
        });
    }

    pintar();
    // El contenedor nace con altura 0 dentro de un <details> cerrado: hay que
    // repintar cuando se abre, y al cambiar el tamaño de la ventana.
    let temporizador;
    window.addEventListener("resize", function () {
        clearTimeout(temporizador);
        temporizador = setTimeout(pintar, 150);
    });
    const plegable = box.closest("details");
    if (plegable) plegable.addEventListener("toggle", function () { if (plegable.open) pintar(); });
})();
