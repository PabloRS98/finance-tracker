// ============================================================
// Gestos táctiles: tirar para refrescar precios.
//
// Solo se activa con puntero grueso (dedo). Con ratón o trackpad el gesto no
// existe y engancharlo al scroll daría saltos raros al llegar arriba del todo.
// ============================================================
(function () {
    "use strict";

    if (!window.matchMedia("(pointer: coarse)").matches) return;

    // Solo donde refrescar precios significa algo: dashboard y activos.
    const ruta = location.pathname;
    if (ruta !== "/" && ruta !== "/activos") return;

    const UMBRAL = 70;      // px que hay que arrastrar para que cuente
    const MAX = 110;        // tope del arrastre, para que no se estire sin fin

    const aviso = document.createElement("div");
    aviso.className = "ptr";
    aviso.innerHTML = '<span class="ptr-texto">Tira para actualizar</span>';
    document.body.appendChild(aviso);

    let inicioY = 0;
    let arrastre = 0;
    let activo = false;
    let refrescando = false;

    function pintar(altura) {
        aviso.style.transform = "translateY(" + altura + "px)";
        aviso.classList.toggle("listo", altura >= UMBRAL);
        aviso.querySelector(".ptr-texto").textContent =
            altura >= UMBRAL ? "Suelta para actualizar" : "Tira para actualizar";
    }

    function soltar() {
        aviso.style.transform = "";
        aviso.classList.remove("listo", "visible");
    }

    document.addEventListener("touchstart", (e) => {
        // Solo si ya estamos arriba del todo: si no, el gesto es un scroll normal
        if (refrescando || window.scrollY > 0 || e.touches.length !== 1) return;
        inicioY = e.touches[0].clientY;
        activo = true;
        arrastre = 0;
    }, { passive: true });

    document.addEventListener("touchmove", (e) => {
        if (!activo) return;
        arrastre = e.touches[0].clientY - inicioY;
        if (arrastre <= 0) {
            activo = false;
            soltar();
            return;
        }
        // Resistencia: cuanto más tiras, menos avanza. Da la sensación de goma
        // y evita que el aviso se despegue del borde.
        const altura = Math.min(MAX, arrastre * 0.45);
        aviso.classList.add("visible");
        pintar(altura);
    }, { passive: true });

    document.addEventListener("touchend", async () => {
        if (!activo) return;
        activo = false;
        const disparar = arrastre * 0.45 >= UMBRAL;
        if (!disparar) {
            soltar();
            return;
        }

        refrescando = true;
        aviso.classList.add("cargando");
        aviso.querySelector(".ptr-texto").textContent = "Actualizando precios…";
        try {
            const resp = await fetch("/api/refresh-prices", {
                method: "POST", headers: window.csrfHeader(),
            });
            const data = await resp.json();
            window.appFlash(data.message || "Precios actualizados", "success");
            location.reload();
        } catch (e) {
            soltar();
            aviso.classList.remove("cargando");
            refrescando = false;
            window.appToast("No se pudieron actualizar los precios", "error");
        }
    }, { passive: true });
})();
