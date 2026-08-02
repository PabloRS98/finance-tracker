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

    // ---------- Pintado ----------
    // `caja` decide el tamaño, así que la misma función sirve para el mapa de
    // la portada y para el ampliado del diálogo. En el ampliado hay sitio para
    // el nombre completo y el peso, que en 260px de alto no cabían.
    function pintar(contenedor) {
        const datos = JSON.parse(contenedor.dataset.items || "[]");
        if (!datos.length) return;
        const detallado = contenedor.dataset.detallado === "1";

        const ancho = contenedor.clientWidth;
        const alto = contenedor.clientHeight;
        if (!ancho || !alto) return;

        const valores = datos.map((d) => d.valor);
        const total = valores.reduce((a, v) => a + v, 0);
        if (total <= 0) return;

        const cajas = [];
        repartir(valores, 0, 0, ancho, alto, (ancho * alto) / total, cajas);

        contenedor.textContent = "";
        cajas.forEach(function (caja, i) {
            const d = datos[i];
            const pct = (100 * d.valor) / total;
            const variacion = d.variacion === null || d.variacion === undefined
                ? "sin variación"
                : (d.variacion >= 0 ? "+" : "") + d.variacion.toFixed(2) + "%";

            // En el ampliado cada pieza lleva a su ficha: mirar el mapa y
            // querer abrir lo que se está mirando es el gesto siguiente.
            const el = document.createElement(detallado && d.id ? "a" : "div");
            if (el.tagName === "A") el.href = "/activos/" + d.id;
            el.className = "heatmap-cell";
            el.style.cssText =
                "left:" + caja.x + "px;top:" + caja.y + "px;" +
                "width:" + Math.max(caja.w - 2, 0) + "px;height:" + Math.max(caja.h - 2, 0) + "px;" +
                "background:" + color(d.variacion);
            el.title = d.nombre + " · " + pct.toFixed(1) + "% de la cartera · " + variacion;

            // El texto solo se pinta donde cabe; donde no, queda el tooltip,
            // que es mejor que un amasijo de letras recortadas.
            const peso = pct.toFixed(1) + "%";
            if (detallado) {
                // Los umbrales van bajos a propósito: se intenta escribir en
                // casi todas y luego se retira lo que no haya cabido. Medir
                // después es más fiable que adivinar antes con un ancho fijo,
                // porque el nombre de un fondo ocupa el triple que un ticker.
                if (caja.w > 34 && caja.h > 18) {
                    el.appendChild(linea("strong", caja.w > 118 ? d.nombre : (d.ticker || d.nombre)));
                }
                if (caja.h > 34) el.appendChild(linea("span", caja.w > 96 ? peso + " · " + variacion : peso));
            } else if (caja.w > 62 && caja.h > 34) {
                el.appendChild(linea("strong", d.ticker || d.nombre));
                if (caja.h > 52) el.appendChild(linea("span", variacion));
            }
            contenedor.appendChild(el);
        });

        // Con la pieza ya pintada se sabe qué se ha recortado. Media palabra
        // cortada es peor que nada, así que se retira: primero la línea de
        // datos, que es la prescindible, y solo después el nombre.
        contenedor.querySelectorAll(".heatmap-cell").forEach(function (celda) {
            const sobra = () => celda.scrollHeight > celda.clientHeight + 1;
            const dato = celda.querySelector("span");
            if (dato && (sobra() || dato.scrollWidth > dato.clientWidth + 1)) dato.remove();
            const nombre = celda.querySelector("strong");
            if (nombre && (sobra() || nombre.scrollWidth > nombre.clientWidth + 1)) nombre.remove();
        });
    }

    function linea(etiqueta, texto) {
        const el = document.createElement(etiqueta);
        el.textContent = texto;
        return el;
    }

    // La lista completa, con todas las posiciones y su peso. Las piezas más
    // pequeñas del mapa no admiten rótulo por mucho que se amplíe, y un mapa de
    // colores sin texto no sirve de nada a quien no distingue el verde del rojo.
    function pintarLeyenda(lista, datos) {
        const total = datos.reduce((a, d) => a + d.valor, 0);
        if (total <= 0) return;
        lista.textContent = "";
        datos.forEach(function (d) {
            const pct = (100 * d.valor) / total;
            const item = document.createElement("li");
            const enlace = document.createElement(d.id ? "a" : "span");
            if (d.id) enlace.href = "/activos/" + d.id;

            const punto = document.createElement("i");
            punto.style.background = color(d.variacion);
            enlace.appendChild(punto);
            enlace.appendChild(linea("strong", d.nombre));
            enlace.appendChild(linea("span", pct.toFixed(1) + "%"));
            item.appendChild(enlace);
            lista.appendChild(item);
        });
    }

    const grande = document.getElementById("heatmap-grande");

    function pintarTodo() {
        pintar(box);
        if (grande && grande.clientWidth) pintar(grande);
    }

    pintarTodo();
    // El contenedor nace con altura 0 dentro de un <details> cerrado: hay que
    // repintar cuando se abre, y al cambiar el tamaño de la ventana.
    let temporizador;
    window.addEventListener("resize", function () {
        clearTimeout(temporizador);
        temporizador = setTimeout(pintarTodo, 150);
    });
    const plegable = box.closest("details");
    if (plegable) plegable.addEventListener("toggle", function () { if (plegable.open) pintar(box); });

    // ---------- Ampliar ----------
    // El diálogo mide 0 mientras está cerrado, así que hay que pintar justo
    // después de abrirlo. <dialog> no emite ningún evento al abrirse: se vigila
    // el atributo, que funciona lo abra quien lo abra.
    const dialogo = document.getElementById("dlg-mapa");
    if (dialogo && grande) {
        const leyenda = document.getElementById("heatmap-leyenda");
        new MutationObserver(function () {
            if (!dialogo.open) return;
            pintar(grande);
            if (leyenda) pintarLeyenda(leyenda, JSON.parse(grande.dataset.items || "[]"));
        }).observe(dialogo, { attributes: true, attributeFilter: ["open"] });
    }

    // El mapa pequeño abre el diálogo al pulsarlo (lo gestiona app.js por el
    // data-open-dialog). Como es un div y no un botón, el teclado hay que
    // atenderlo aquí o la ampliación quedaría solo para quien use ratón.
    box.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            box.click();
        }
    });
})();
