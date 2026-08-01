// ============================================================
// Toggle de divisa secundaria (solo finance-tracker).
// Reescribe los importes marcados con [data-money][data-cur] a la
// otra divisa (EUR<->USD) con el FX actual de /fx (cache 1 h).
// Las cifras convertidas llevan "≈": son orientativas, no contables.
// ============================================================
(function () {
    "use strict";

    const btn = document.getElementById("btn-currency");
    if (!btn) return;

    const fmt = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    let usdToBase = null; // USD -> EUR

    function cachedRate() {
        try {
            const c = JSON.parse(localStorage.getItem("fx_usd_base") || "null");
            if (c && c.rate > 0 && Date.now() - c.ts < 3600e3) return c.rate;
        } catch (e) { /* cache corrupta: se ignora */ }
        return null;
    }

    async function loadRate() {
        if (usdToBase) return usdToBase;
        usdToBase = cachedRate();
        if (usdToBase) return usdToBase;
        const res = await fetch("/fx");
        const data = await res.json();
        if (data.usd_to_base > 0) {
            usdToBase = data.usd_to_base;
            localStorage.setItem("fx_usd_base", JSON.stringify({ rate: usdToBase, ts: Date.now() }));
        }
        return usdToBase;
    }

    function apply(on) {
        document.querySelectorAll("[data-money][data-cur]").forEach((el) => {
            if (el.dataset.orig === undefined) el.dataset.orig = el.innerHTML;
            const amount = parseFloat(el.dataset.money);
            const cur = el.dataset.cur;
            if (!on || !usdToBase || !isFinite(amount) || (cur !== "EUR" && cur !== "USD")) {
                el.innerHTML = el.dataset.orig;
                return;
            }
            const alt = cur === "USD" ? amount * usdToBase : amount / usdToBase;
            const altCur = cur === "USD" ? "EUR" : "USD";
            const small = el.querySelector("small");
            const cls = small && small.className ? ' class="' + small.className + '"' : "";
            el.innerHTML = "≈" + fmt.format(alt) + " <small" + cls + ">" + altCur + "</small>";
        });
        btn.classList.toggle("active", on);
    }

    btn.addEventListener("click", async () => {
        const on = localStorage.getItem("altcur") !== "1";
        if (on) {
            await loadRate();
            if (!usdToBase) {
                if (window.appToast) window.appToast("No se pudo obtener el tipo de cambio", "error");
                return;
            }
        }
        localStorage.setItem("altcur", on ? "1" : "0");
        apply(on);
    });

    // Estado persistido: si el toggle estaba activo, aplicarlo al cargar
    if (localStorage.getItem("altcur") === "1") {
        loadRate().then(() => apply(Boolean(usdToBase)));
    }
})();
