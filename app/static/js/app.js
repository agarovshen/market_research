const form = document.getElementById("import-form");
const fileInput = document.getElementById("csv-file");
const statusElement = document.getElementById("status");

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = fileInput.files[0];
    if (!file) {
        statusElement.textContent = "Please select a CSV file.";
        return;
    }
    const formData = new FormData();
    formData.append("csv_file", file);
    statusElement.textContent = "Uploading...";
    const response = await fetch("/import-csv", {
        method: "POST",
        body: formData
    });
    const data = await response.json();
    statusElement.textContent = data.message;
});