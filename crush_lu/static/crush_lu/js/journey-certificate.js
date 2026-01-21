document.addEventListener("DOMContentLoaded", () => {
    const printButton = document.querySelector("[data-certificate-print]");
    if (!printButton) {
        return;
    }

    printButton.addEventListener("click", () => {
        window.print();
    });
});
