// ============================================================
// Botón flotante: gasto rápido y voz, a un toque desde cualquier pantalla.
// Antes, apuntar un gasto eran dos navegaciones (ir a Movimientos y bajar al
// formulario), y es la acción más repetida del día a día.
// ============================================================
(function () {
    "use strict";

    const wrap = document.getElementById("fab-wrap");
    const boton = document.getElementById("fab");
    const acciones = document.getElementById("fab-actions");
    if (!wrap || !boton || !acciones) return;

    function desplegar(abrir) {
        acciones.hidden = !abrir;
        wrap.classList.toggle("abierto", abrir);
        boton.setAttribute("aria-expanded", abrir ? "true" : "false");
    }

    boton.addEventListener("click", () => desplegar(acciones.hidden));

    // Cerrar al tocar fuera o con Escape: en móvil el FAB tapa contenido y
    // dejarlo abierto estorba.
    document.addEventListener("click", (e) => {
        if (!wrap.contains(e.target)) desplegar(false);
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") desplegar(false);
    });
    acciones.addEventListener("click", () => desplegar(false));

    // ---------- Categorías del gasto rápido ----------
    // El diálogo vive en base.html, que no recibe la lista de categorías: se
    // piden una sola vez, al abrir por primera vez, y se cachean en memoria.
    const dialogo = document.getElementById("dlg-gasto");
    const select = document.getElementById("fab-cat");
    let cargadas = false;

    async function cargarCategorias() {
        if (cargadas || !select) return;
        cargadas = true;
        try {
            const resp = await fetch("/categorias/opciones");
            for (const cat of await resp.json()) {
                const opt = document.createElement("option");
                opt.value = cat.id;
                opt.textContent = cat.name;
                select.appendChild(opt);
            }
        } catch (e) {
            // Sin categorías el gasto se apunta igual, solo que sin clasificar
            cargadas = false;
        }
    }

    if (dialogo) {
        // El foco al importe: es el único campo obligatorio y en móvil abre
        // el teclado numérico sin un toque extra.
        const abridor = document.querySelector('[data-open-dialog="#dlg-gasto"]');
        if (abridor) abridor.addEventListener("click", () => {
            cargarCategorias();
            setTimeout(() => document.getElementById("fab-importe").focus(), 60);
        });
    }
})();
