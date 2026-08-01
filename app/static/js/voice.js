// Captura de voz usando la Web Speech API del navegador (gratis, sin API key).
// Solo disponible en navegadores basados en Chromium (Chrome, Edge, Brave...).
//
// Los disparadores se marcan con [data-voice-btn], no por id: además de los
// botones de Movimientos y Operaciones ahora hay uno en el botón flotante, que
// vive en base.html y por tanto sale en todas las páginas.
(function () {
    const botones = [...document.querySelectorAll("[data-voice-btn]")];
    if (!botones.length) return;

    // El estado va en el <span> hermano si lo hay (páginas con formulario); el
    // botón flotante no tiene sitio para texto y se apoya en los toasts.
    const statusEl = document.getElementById("voice-status");
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

    function estado(texto) {
        if (statusEl) statusEl.textContent = texto;
    }

    if (!SpeechRecognition) {
        const aviso = "Tu navegador no soporta reconocimiento de voz (usa Chrome o Edge).";
        estado(aviso);
        botones.forEach((b) => {
            b.disabled = true;
            b.title = aviso;
        });
        return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = "es-ES";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    let listening = false;

    function marcar(escuchando) {
        listening = escuchando;
        botones.forEach((b) => b.classList.toggle("listening", escuchando));
    }

    botones.forEach((btn) => btn.addEventListener("click", () => {
        if (listening) return;
        marcar(true);
        estado("Escuchando...");
        // Sin el <span> de estado (botón flotante) el toast es el único aviso
        // de que está grabando.
        if (!statusEl) window.appToast("Escuchando...", "info");
        recognition.start();
    }));

    recognition.addEventListener("result", async (event) => {
        const text = event.results[0][0].transcript;
        estado('Procesando: "' + text + '"...');
        try {
            const resp = await fetch("/transacciones/voz", {
                method: "POST",
                headers: window.csrfHeader({ "Content-Type": "application/json" }),
                body: JSON.stringify({ text: text }),
            });
            const data = await resp.json();
            if (data.ok) {
                estado("");
                // Deja el toast preparado y recarga para ver la pendiente nueva
                window.appFlash("Pendiente de aprobar: " + data.summary, "success");
                setTimeout(() => window.location.reload(), 350);
            } else {
                estado("");
                window.appToast(data.error || "No se pudo procesar el texto.", "error");
            }
        } catch (err) {
            estado("");
            window.appToast("Error de conexión al enviar el texto transcrito.", "error");
        }
    });

    recognition.addEventListener("end", () => {
        marcar(false);
        if (statusEl && statusEl.textContent === "Escuchando...") estado("");
    });
    recognition.addEventListener("error", (event) => {
        marcar(false);
        estado("");
        const msg = event.error === "no-speech" ? "No se ha oído nada. Prueba otra vez." : "Error de reconocimiento: " + event.error;
        window.appToast(msg, "error");
    });
})();
