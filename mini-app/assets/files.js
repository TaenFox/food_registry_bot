"use strict";

function createFilesView({ request, handleAccessError, expire, refreshDiary }) {
    const root = document.querySelector('#settings [data-section="files"]');
    const list = root.querySelector(".file-list");
    const status = root.querySelector(".file-status");
    const picker = root.querySelector('input[type="file"]');
    const upload = root.querySelector(".file-upload");
    const create = root.querySelector(".file-export");
    const refresh = root.querySelector(".files-refresh");
    const fileTypes = {
        food_registry_csv_v1: "Еда и вода",
        food_registry_csv_v1_partial: "Еда и вода · неполные данные",
        workout_csv_v1: "Тренировки",
    };
    const fileStatuses = { ready: "Готов", processed: "Обработан", error: "Ошибка" };
    const errors = {
        invalid_filename: "Выберите файл CSV.",
        file_too_large: "Файл больше 1 МБ.",
        file_missing: "Файл недоступен. Обновите список.",
    };
    let pending;

    function setBusy(busy) {
        for (const control of root.querySelectorAll("button, input")) control.disabled = busy;
    }

    function render(files) {
        list.replaceChildren();
        if (!files.length) list.append(element("li", "Файлов пока нет.", "settings-note"));
        for (const file of files) {
            const item = element("li", undefined, "file-item");
            item.append(element("strong", file.filename));
            const type = fileTypes[file.type] || file.type;
            const direction = file.direction === "import" ? "Импорт" : "Экспорт";
            item.append(element("p", `#${file.id} · ${direction} · ${type} · ${fileStatuses[file.status]}`, "settings-note"));
            if (file.direction === "import") {
                const dateRange = file.date_from && file.date_to
                    ? ` · ${file.date_from} — ${file.date_to}` : "";
                const counts = file.type === "workout_csv_v1" ? `Тренировок: ${file.rows}`
                    : `Еда: ${file.food_count}, вода: ${file.water_count}`;
                item.append(element("p", counts + dateRange, "settings-note"));
            }
            if (file.message) item.append(element("p", file.message, "settings-note"));
            const actions = element("div", undefined, "file-actions");
            if (file.direction === "export") {
                actions.append(actionButton("Скачать", () => run({
                    command: { action: "download", file_id: file.id }, download: true,
                }, file.filename)));
            } else if (file.status !== "processed") {
                actions.append(actionButton("Импортировать", () => run({
                    command: { action: "import", file_id: file.id },
                })));
            }
            actions.append(actionButton("Удалить файл", () => {
                if (window.confirm(`Удалить файл «${file.filename}»?`)) {
                    run({ command: { action: "delete", file_id: file.id } });
                }
            }));
            item.append(actions);
            list.append(item);
        }
    }

    function downloadCsv(blob, name) {
        const url = URL.createObjectURL(blob);
        const link = element("a");
        link.href = url;
        link.download = name;
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
    }

    async function run(options = {}, filename) {
        if (pending || expire()) return;
        const controller = new AbortController();
        pending = controller;
        setBusy(true);
        status.textContent = "Загрузка…";
        try {
            let result = await request({ ...options, signal: controller.signal });
            if (pending !== controller || expire() || handleAccessError(result)) return;
            if (options.download && result.response.ok && result.payload instanceof Blob) {
                downloadCsv(result.payload, filename);
                status.textContent = "Файл передан на скачивание.";
                result = await request({ signal: controller.signal });
                if (pending !== controller || expire() || handleAccessError(result)) return;
                if (result.response.ok) render(result.payload.files);
                return;
            }
            if (result.payload.files) render(result.payload.files);
            const error = result.response.status === 413 ? "file_too_large" : result.payload.error;
            status.textContent = result.payload.message || errors[error]
                || (result.response.ok && !options.download ? "" : "Не удалось выполнить действие.");
            if (result.response.ok && options.command?.action === "import") await refreshDiary();
        } catch {
            if (pending === controller) status.textContent = "Не удалось выполнить действие. Обновите список.";
        } finally {
            if (pending === controller) {
                pending = undefined;
                setBusy(false);
            }
        }
    }

    root.addEventListener("toggle", () => { if (root.open) run(); });
    refresh.addEventListener("click", () => run());
    upload.addEventListener("click", () => picker.click());
    picker.addEventListener("change", () => {
        const file = picker.files[0];
        picker.value = "";
        if (file) run({ file });
    });
    create.addEventListener("click", () => run({ command: { action: "export" } }));

    function clear() {
        pending?.abort();
        pending = undefined;
        root.open = false;
        picker.value = "";
        list.replaceChildren();
        status.textContent = "";
        setBusy(false);
    }

    return { clear };
}
