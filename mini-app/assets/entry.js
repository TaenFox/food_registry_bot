"use strict";

function createEntryView({ request, handleAccessError, expire, defaults, onSaved, onHistory }) {
    const dialog = document.querySelector("#entry-composer");
    const form = dialog.querySelector("form");
    const close = dialog.querySelector(".entry-close");
    const status = form.querySelector(".entry-status");
    const text = form.elements.meal;
    const date = form.elements.local_date;
    const time = form.elements.local_time;
    const errors = {
        invalid_entry: "Проверьте описание, дату и время.",
        invalid_entry_time: "Такого местного времени нет. Выберите другое.",
        entry_request_conflict: "Описание изменилось. Нажмите «Записать» ещё раз.",
        not_a_food_record: "Опишите, что съели или выпили.",
        entry_not_recognized: "Не удалось распознать запись. Попробуйте уточнить описание.",
        entry_nutrition_failed: "Не удалось рассчитать состав записи. Попробуйте ещё раз.",
        entry_provider_unavailable: "Провайдер недоступен. Проверьте настройки через /provider в боте.",
    };
    let submitting = false;
    let attempt;

    function open() {
        if (expire() || dialog.open) return;
        if (!text.value.trim() && !attempt) {
            const initial = defaults();
            date.value = initial.date;
            time.value = initial.time;
        }
        dialog.showModal();
    }

    function clear() {
        if (dialog.open) dialog.close();
        form.reset();
        text.setCustomValidity("");
        attempt = undefined;
        status.textContent = "";
    }

    close.addEventListener("click", () => {
        if (!submitting) dialog.close();
    });
    form.querySelector(".entry-history").addEventListener("click", () => {
        if (submitting || expire()) return;
        dialog.close();
        onHistory({ date: date.value, time: time.value });
    });
    dialog.addEventListener("cancel", event => {
        if (submitting) event.preventDefault();
    });
    text.addEventListener("input", () => text.setCustomValidity(""));
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (submitting || expire()) return;
        const meal = text.value.trim();
        if (!meal) {
            text.setCustomValidity("Введите описание записи.");
            text.reportValidity();
            return;
        }

        submitting = true;
        const controls = [close, ...form.elements];
        controls.forEach(control => { control.disabled = true; });
        status.textContent = "Распознаю и сохраняю…";
        try {
            const command = { text: meal, local_date: date.value, local_time: time.value };
            const signature = JSON.stringify(command);
            if (attempt?.signature !== signature) {
                attempt = { signature, id: crypto.randomUUID() };
            }
            const result = await request({ ...command, request_id: attempt.id });
            if (expire() || handleAccessError(result)) return;
            if (result.response.ok) {
                await onSaved(result.payload.summary_date);
                clear();
            } else {
                if (result.payload.error === "entry_request_conflict") attempt = undefined;
                status.textContent = errors[result.payload.error] || "Не удалось подтвердить запись. Попробуйте ещё раз.";
            }
        } catch {
            if (dialog.open) status.textContent = "Не удалось подтвердить запись. Попробуйте ещё раз.";
        } finally {
            submitting = false;
            controls.forEach(control => { control.disabled = false; });
        }
    });

    return { open, clear };
}
