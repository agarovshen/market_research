const form = document.getElementById("import-form");
const fileInput = document.getElementById("csv-file");
const fileName = document.getElementById("file-name");
const status = document.getElementById("status");
const button = form.querySelector("button[type='submit']");
fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    fileName.textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB` : "MT5 tab-separated export supported";
});
form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = fileInput.files[0];
    if (!file) {
        status.className = "rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-sm text-amber-400";
        status.textContent = "Select a CSV dataset first.";
        return;
    }
    button.disabled = true;
    button.textContent = "Processing...";
    status.className = "rounded-xl border border-cyan-500/20 bg-cyan-500/5 px-4 py-3 text-sm text-cyan-400";
    status.textContent = "Checking database and importing dataset...";
    try {
        const formData = new FormData();
        formData.append("csv_file", file);
        const response = await fetch("/import-csv", { method: "POST", body: formData });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || data.message || "Import failed.");
        status.className = data.imported === false ? "rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-sm text-amber-400" : "rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-4 py-3 text-sm text-emerald-400";
        status.textContent = data.message;
    } catch (error) {
        status.className = "rounded-xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-400";
        status.textContent = error.message;
    } finally {
        button.disabled = false;
        button.textContent = "Initialize Data Import";
    }
});