/**
 * Power-Up Pixel War — self-contained ES module.
 * Uses Pointer Events (1-finger tap/drag, 2-finger pinch), full keyboard control,
 * crisp offscreen bitmap rendering, and DOM CSRF token extraction for HttpOnly cookie prod.
 */

const RECENT_COLORS_KEY = "powerupPixelWarRecentColors";
const POLL_INTERVAL_MS = 5000;
const DRAG_THRESHOLD_PX = 6;

function readConfig() {
    const el = document.getElementById("pixel-war-config");
    if (!el) return null;
    try {
        return JSON.parse(el.textContent || "{}");
    } catch {
        return null;
    }
}

function getCookie(name) {
    const prefix = `${name}=`;
    const parts = document.cookie.split(";");
    for (const part of parts) {
        const trimmed = part.trim();
        if (trimmed.startsWith(prefix)) {
            return decodeURIComponent(trimmed.slice(prefix.length));
        }
    }
    return "";
}

function getCsrfToken() {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input && input.value) {
        return input.value;
    }
    return getCookie("csrftoken");
}

function formatMessage(template, vars = {}) {
    return Object.entries(vars).reduce(
        (acc, [key, value]) => acc.replace(`%(${key})s`, String(value)),
        template,
    );
}

class PixelWarClient {
    constructor(config) {
        this.config = config;
        this.gridWidth = Number(config.width) || 100;
        this.gridHeight = Number(config.height) || 100;
        this.canvas = document.getElementById("pw-canvas");
        this.stage = document.getElementById("pw-stage");
        if (!this.canvas || !this.stage) return;

        this.ctx = this.canvas.getContext("2d");
        this.offscreen = document.createElement("canvas");
        this.offscreen.width = this.gridWidth;
        this.offscreen.height = this.gridHeight;
        this.offCtx = this.offscreen.getContext("2d");

        this.pixels = new Map();
        this.selectedColor = "#FE4901";
        this.showGrid = true;
        this.confirmMode = window.matchMedia("(pointer: coarse)").matches;
        this.selectedCell = null;
        this.hoveredCell = null;
        this.keyboardCursor = null;

        this.scale = 5;
        this.baseScale = 5;
        this.minScale = 1;
        this.maxScale = 48;
        this.offsetX = 0;
        this.offsetY = 0;
        this.cssWidth = 0;
        this.cssHeight = 0;

        this.activePointers = new Map();
        this.isDragging = false;
        this.isPinching = false;
        this.dragDistance = 0;
        this.lastPointerPos = null;
        this.lastPinchDist = 0;
        this.lastPinchMid = null;
        this.renderPending = false;
        this.isPlacing = false;

        this.cooldownRemaining = 0;
        this.pixelsRemaining = Number(config.pixelsPerMinute) || 6;
        this.cooldownTimerId = null;
        this.pollTimerId = null;
        this.alertTimerId = null;
        this.recentColors = this.loadRecentColors();

        this.cacheElements();
        this.initOffscreen();
        this.bindUI();
        this.bindCanvasEvents();
        this.setupResizeObserver();
        this.updateModeUI();
        this.updateSelectedColorUI();
        this.renderRecentColors();
        this.updatePlaceButtonState();
        this.startPolling();
        this.refreshAll();
    }

    cacheElements() {
        this.zoomOutBtn = document.getElementById("pw-zoom-out");
        this.zoomInBtn = document.getElementById("pw-zoom-in");
        this.zoomResetBtn = document.getElementById("pw-zoom-reset");
        this.zoomLevelEl = document.getElementById("pw-zoom-level");
        this.gridToggleBtn = document.getElementById("pw-grid-toggle");
        this.modeToggleBtn = document.getElementById("pw-mode-toggle");
        this.fullscreenBtn = document.getElementById("pw-fullscreen-toggle");
        this.inspectorSwatch = document.getElementById("pw-inspector-swatch");
        this.inspectorText = document.getElementById("pw-inspector-text");
        this.mobileBar = document.getElementById("pw-mobile-bar");
        this.barSwatch = document.getElementById("pw-bar-swatch");
        this.selectedCoordsEl = document.getElementById("pw-selected-coords");
        this.placeBtn = document.getElementById("pw-place-btn");
        this.customColorInput = document.getElementById("pw-custom-color");
        this.recentSection = document.getElementById("pw-recent-section");
        this.recentColorsEl = document.getElementById("pw-recent-colors");
        this.selectedSwatchEl = document.getElementById("pw-selected-swatch");
        this.selectedHexEl = document.getElementById("pw-selected-hex");
        this.cooldownStatusEl = document.getElementById("pw-cooldown-status");
        this.pixelsRemainingEl = document.getElementById("pw-pixels-remaining");
        this.userTotalEl = document.getElementById("pw-user-total");
        this.pixelCountEl = document.getElementById("pw-pixel-count");
        this.activityListEl = document.getElementById("pw-activity-list");
        this.statusLiveEl = document.getElementById("pw-status-live");
        this.alertLiveEl = document.getElementById("pw-alert-live");
    }

    initOffscreen() {
        this.offCtx.fillStyle = "#FFFFFF";
        this.offCtx.fillRect(0, 0, this.gridWidth, this.gridHeight);
    }

    setupResizeObserver() {
        const updateSize = () => {
            const rect = this.canvas.getBoundingClientRect();
            if (!rect.width || !rect.height) return;
            const dpr = window.devicePixelRatio || 1;
            const wasUninitialized = this.cssWidth === 0;
            this.cssWidth = rect.width;
            this.cssHeight = rect.height;
            this.canvas.width = Math.max(1, Math.round(rect.width * dpr));
            this.canvas.height = Math.max(1, Math.round(rect.height * dpr));
            this.minScale = Math.max(
                0.5,
                Math.min(rect.width / this.gridWidth, rect.height / this.gridHeight) * 0.6,
            );
            this.maxScale = Math.max(32, this.minScale * 24);
            if (wasUninitialized) {
                this.fitToCanvas();
            } else {
                this.clampViewport();
                this.scheduleRender();
            }
        };

        if (typeof ResizeObserver !== "undefined") {
            new ResizeObserver(updateSize).observe(this.canvas);
        } else {
            window.addEventListener("resize", updateSize);
        }
        updateSize();
    }

    fitToCanvas() {
        if (!this.cssWidth || !this.cssHeight) return;
        const padding = 20;
        const availW = Math.max(40, this.cssWidth - padding * 2);
        const availH = Math.max(40, this.cssHeight - padding * 2);
        this.scale = Math.max(
            this.minScale,
            Math.min(availW / this.gridWidth, availH / this.gridHeight),
        );
        this.baseScale = this.scale;
        this.offsetX = (this.cssWidth - this.gridWidth * this.scale) / 2;
        this.offsetY = (this.cssHeight - this.gridHeight * this.scale) / 2;
        this.updateZoomReadout();
        this.scheduleRender();
    }

    clampViewport() {
        const worldW = this.gridWidth * this.scale;
        const worldH = this.gridHeight * this.scale;
        const margin = 48;

        if (worldW <= this.cssWidth) {
            this.offsetX = Math.max(-margin, Math.min(this.cssWidth - worldW + margin, this.offsetX));
        } else {
            this.offsetX = Math.max(this.cssWidth - worldW - margin, Math.min(margin, this.offsetX));
        }

        if (worldH <= this.cssHeight) {
            this.offsetY = Math.max(-margin, Math.min(this.cssHeight - worldH + margin, this.offsetY));
        } else {
            this.offsetY = Math.max(this.cssHeight - worldH - margin, Math.min(margin, this.offsetY));
        }
    }

    zoomAround(factor, clientX, clientY) {
        const rect = this.canvas.getBoundingClientRect();
        const localX = clientX !== undefined ? clientX - rect.left : this.cssWidth / 2;
        const localY = clientY !== undefined ? clientY - rect.top : this.cssHeight / 2;
        const worldX = (localX - this.offsetX) / this.scale;
        const worldY = (localY - this.offsetY) / this.scale;

        const nextScale = Math.max(this.minScale, Math.min(this.maxScale, this.scale * factor));
        if (nextScale === this.scale) return;

        this.scale = nextScale;
        this.offsetX = localX - worldX * this.scale;
        this.offsetY = localY - worldY * this.scale;
        this.clampViewport();
        this.updateZoomReadout();
        this.scheduleRender();
    }

    clientToCell(clientX, clientY) {
        const rect = this.canvas.getBoundingClientRect();
        const localX = clientX - rect.left;
        const localY = clientY - rect.top;
        const gx = Math.floor((localX - this.offsetX) / this.scale);
        const gy = Math.floor((localY - this.offsetY) / this.scale);
        if (gx < 0 || gx >= this.gridWidth || gy < 0 || gy >= this.gridHeight) {
            return null;
        }
        return { x: gx, y: gy };
    }

    ensureCellVisible(cell) {
        const px = this.offsetX + cell.x * this.scale;
        const py = this.offsetY + cell.y * this.scale;
        const pad = 24;
        if (px < pad) this.offsetX += pad - px;
        if (py < pad) this.offsetY += pad - py;
        if (px + this.scale > this.cssWidth - pad) {
            this.offsetX -= px + this.scale - (this.cssWidth - pad);
        }
        if (py + this.scale > this.cssHeight - pad) {
            this.offsetY -= py + this.scale - (this.cssHeight - pad);
        }
        this.clampViewport();
    }

    bindUI() {
        document.querySelectorAll("#pw-palette .pw-swatch").forEach((btn) => {
            btn.addEventListener("click", () => {
                this.selectColor(btn.dataset.color);
            });
        });

        if (this.customColorInput) {
            this.customColorInput.addEventListener("input", (e) => {
                this.selectColor(e.target.value);
            });
        }

        if (this.zoomInBtn) {
            this.zoomInBtn.addEventListener("click", () => this.zoomAround(1.25));
        }
        if (this.zoomOutBtn) {
            this.zoomOutBtn.addEventListener("click", () => this.zoomAround(0.8));
        }
        if (this.zoomResetBtn) {
            this.zoomResetBtn.addEventListener("click", () => this.fitToCanvas());
        }

        if (this.gridToggleBtn) {
            this.gridToggleBtn.addEventListener("click", () => {
                this.showGrid = !this.showGrid;
                this.gridToggleBtn.setAttribute("aria-pressed", String(this.showGrid));
                this.gridToggleBtn.textContent = this.showGrid
                    ? this.config.i18n.gridOn
                    : this.config.i18n.gridOff;
                this.scheduleRender();
            });
        }

        if (this.modeToggleBtn) {
            this.modeToggleBtn.addEventListener("click", () => {
                this.confirmMode = !this.confirmMode;
                this.updateModeUI();
                this.scheduleRender();
            });
        }

        if (this.fullscreenBtn) {
            this.fullscreenBtn.addEventListener("click", () => this.toggleFullscreen());
            document.addEventListener("fullscreenchange", () => {
                const isFs = document.fullscreenElement === this.stage;
                this.fullscreenBtn.setAttribute("aria-pressed", String(isFs));
                this.fullscreenBtn.textContent = isFs
                    ? this.config.i18n.exitFullscreen
                    : this.config.i18n.fullscreen;
            });
        }

        if (this.placeBtn) {
            this.placeBtn.addEventListener("click", () => {
                if (this.placeBtn.getAttribute("aria-disabled") === "true") {
                    if (!this.selectedCell) {
                        this.announceStatus(this.config.i18n.selectCell);
                    } else if (this.cooldownRemaining > 0) {
                        this.showAlert(
                            formatMessage(this.config.i18n.limitReached, {
                                seconds: this.cooldownRemaining,
                            }),
                        );
                    }
                    return;
                }
                if (this.selectedCell) {
                    this.placePixel(this.selectedCell.x, this.selectedCell.y);
                }
            });
        }
    }

    bindCanvasEvents() {
        this.canvas.addEventListener("pointerdown", (e) => {
            if (e.pointerType === "mouse" && e.button !== 0) return;
            try {
                this.canvas.setPointerCapture(e.pointerId);
            } catch {
                // Synthetic test pointers may not support capture
            }
            this.activePointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

            if (this.activePointers.size === 1) {
                this.isDragging = false;
                this.isPinching = false;
                this.dragDistance = 0;
                this.lastPointerPos = { x: e.clientX, y: e.clientY };
            } else if (this.activePointers.size === 2) {
                this.isPinching = true;
                this.isDragging = true;
                const pts = Array.from(this.activePointers.values());
                this.lastPinchDist = Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
                this.lastPinchMid = {
                    x: (pts[0].x + pts[1].x) / 2,
                    y: (pts[0].y + pts[1].y) / 2,
                };
            }
        });

        this.canvas.addEventListener("pointermove", (e) => {
            if (!this.activePointers.has(e.pointerId)) {
                if (e.pointerType === "mouse") {
                    const cell = this.clientToCell(e.clientX, e.clientY);
                    this.hoveredCell = cell;
                    this.updateInspector(cell);
                    this.scheduleRender();
                }
                return;
            }

            this.activePointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

            if (this.activePointers.size === 1 && this.lastPointerPos) {
                const dx = e.clientX - this.lastPointerPos.x;
                const dy = e.clientY - this.lastPointerPos.y;
                this.dragDistance += Math.hypot(dx, dy);
                this.lastPointerPos = { x: e.clientX, y: e.clientY };

                if (this.dragDistance > DRAG_THRESHOLD_PX) {
                    this.isDragging = true;
                    this.offsetX += dx;
                    this.offsetY += dy;
                    this.clampViewport();
                    this.scheduleRender();
                }
            } else if (this.activePointers.size === 2) {
                const pts = Array.from(this.activePointers.values());
                const dist = Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
                const mid = {
                    x: (pts[0].x + pts[1].x) / 2,
                    y: (pts[0].y + pts[1].y) / 2,
                };
                if (this.lastPinchMid) {
                    this.offsetX += mid.x - this.lastPinchMid.x;
                    this.offsetY += mid.y - this.lastPinchMid.y;
                }
                if (this.lastPinchDist > 0 && dist > 0) {
                    this.zoomAround(dist / this.lastPinchDist, mid.x, mid.y);
                }
                this.lastPinchDist = dist;
                this.lastPinchMid = mid;
                this.clampViewport();
                this.scheduleRender();
            }
        });

        const endPointer = (e) => {
            const wasClick =
                e.type === "pointerup" &&
                !this.isDragging &&
                !this.isPinching &&
                this.activePointers.size === 1;
            this.activePointers.delete(e.pointerId);

            if (wasClick) {
                const cell = this.clientToCell(e.clientX, e.clientY);
                if (cell) {
                    this.selectedCell = cell;
                    this.keyboardCursor = cell;
                    this.updateInspector(cell);
                    this.updateSelectedCoordsUI();
                    this.updatePlaceButtonState();
                    if (this.confirmMode) {
                        this.scheduleRender();
                    } else {
                        this.placePixel(cell.x, cell.y);
                    }
                }
            }

            if (this.activePointers.size === 0) {
                this.isDragging = false;
                this.isPinching = false;
                this.lastPointerPos = null;
                this.lastPinchMid = null;
            }
        };

        this.canvas.addEventListener("pointerup", endPointer);
        this.canvas.addEventListener("pointercancel", endPointer);
        this.canvas.addEventListener("pointerleave", () => {
            this.hoveredCell = null;
            this.updateInspector(this.selectedCell);
            this.scheduleRender();
        });

        this.canvas.addEventListener(
            "wheel",
            (e) => {
                e.preventDefault();
                const factor = e.deltaY < 0 ? 1.15 : 0.87;
                this.zoomAround(factor, e.clientX, e.clientY);
            },
            { passive: false },
        );

        this.canvas.addEventListener("keydown", (e) => {
            const stepPan = 40;
            if (e.shiftKey && e.key.startsWith("Arrow")) {
                e.preventDefault();
                if (e.key === "ArrowUp") this.offsetY += stepPan;
                if (e.key === "ArrowDown") this.offsetY -= stepPan;
                if (e.key === "ArrowLeft") this.offsetX += stepPan;
                if (e.key === "ArrowRight") this.offsetX -= stepPan;
                this.clampViewport();
                this.scheduleRender();
                return;
            }

            if (e.key.startsWith("Arrow")) {
                e.preventDefault();
                if (!this.keyboardCursor) {
                    this.keyboardCursor = {
                        x: Math.floor(this.gridWidth / 2),
                        y: Math.floor(this.gridHeight / 2),
                    };
                }
                const next = { ...this.keyboardCursor };
                if (e.key === "ArrowUp") next.y = Math.max(0, next.y - 1);
                if (e.key === "ArrowDown") next.y = Math.min(this.gridHeight - 1, next.y + 1);
                if (e.key === "ArrowLeft") next.x = Math.max(0, next.x - 1);
                if (e.key === "ArrowRight") next.x = Math.min(this.gridWidth - 1, next.x + 1);
                this.keyboardCursor = next;
                this.selectedCell = next;
                this.ensureCellVisible(next);
                this.updateInspector(next);
                this.updateSelectedCoordsUI();
                this.updatePlaceButtonState();
                this.scheduleRender();
                return;
            }

            if (e.key === "+" || e.key === "=") {
                e.preventDefault();
                this.zoomAround(1.25);
            } else if (e.key === "-") {
                e.preventDefault();
                this.zoomAround(0.8);
            } else if (e.key === "0") {
                e.preventDefault();
                this.fitToCanvas();
            } else if (e.key === "g" || e.key === "G") {
                this.gridToggleBtn?.click();
            } else if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                const target = this.keyboardCursor || this.selectedCell;
                if (target) {
                    this.placePixel(target.x, target.y);
                }
            }
        });
    }

    toggleFullscreen() {
        if (!document.fullscreenEnabled) return;
        if (document.fullscreenElement === this.stage) {
            document.exitFullscreen().catch(() => {});
        } else {
            this.stage.requestFullscreen().catch(() => {});
        }
    }

    selectColor(hex) {
        if (!/^#[0-9A-Fa-f]{6}$/.test(hex || "")) return;
        this.selectedColor = hex.toUpperCase();
        if (this.customColorInput) {
            this.customColorInput.value = this.selectedColor;
        }
        document.querySelectorAll(".pw-swatch").forEach((btn) => {
            const match = (btn.dataset.color || "").toUpperCase() === this.selectedColor;
            btn.classList.toggle("is-selected", match);
            btn.setAttribute("aria-pressed", String(match));
        });
        this.updateSelectedColorUI();
        this.scheduleRender();
    }

    updateSelectedColorUI() {
        if (this.selectedSwatchEl) {
            this.selectedSwatchEl.style.backgroundColor = this.selectedColor;
        }
        if (this.barSwatch) {
            this.barSwatch.style.backgroundColor = this.selectedColor;
        }
        if (this.selectedHexEl) {
            this.selectedHexEl.textContent = this.selectedColor;
        }
    }

    updateModeUI() {
        if (this.modeToggleBtn) {
            this.modeToggleBtn.setAttribute("aria-pressed", String(this.confirmMode));
            this.modeToggleBtn.textContent = this.confirmMode
                ? this.config.i18n.confirmMode
                : this.config.i18n.tapMode;
        }
        if (this.mobileBar) {
            this.mobileBar.hidden = !this.confirmMode;
        }
    }

    updateZoomReadout() {
        if (!this.zoomLevelEl) return;
        const refScale = this.baseScale || 5;
        const pct = Math.round((this.scale / refScale) * 100);
        this.zoomLevelEl.textContent = `${pct}%`;
    }

    updateSelectedCoordsUI() {
        if (!this.selectedCoordsEl) return;
        if (this.selectedCell) {
            this.selectedCoordsEl.textContent = `(${this.selectedCell.x}, ${this.selectedCell.y})`;
        } else {
            this.selectedCoordsEl.textContent = this.config.i18n.selectCell;
        }
    }

    updateInspector(cell) {
        if (!this.inspectorText || !this.inspectorSwatch) return;
        if (!cell) {
            this.inspectorSwatch.style.backgroundColor = "transparent";
            return;
        }
        const entry = this.pixels.get(`${cell.x},${cell.y}`);
        if (entry) {
            this.inspectorSwatch.style.backgroundColor = entry.color;
            this.inspectorText.textContent = `(${cell.x}, ${cell.y}) · ${entry.color} · ${entry.placed_by}`;
        } else {
            this.inspectorSwatch.style.backgroundColor = "#FFFFFF";
            this.inspectorText.textContent = `(${cell.x}, ${cell.y}) · #FFFFFF`;
        }
    }

    updatePlaceButtonState() {
        if (!this.placeBtn) return;
        const disabled =
            !this.config.isActive ||
            this.isPlacing ||
            !this.selectedCell ||
            this.cooldownRemaining > 0 ||
            this.pixelsRemaining <= 0;
        this.placeBtn.setAttribute("aria-disabled", String(disabled));
    }

    startCooldown(seconds) {
        this.cooldownRemaining = Math.max(0, Math.ceil(seconds));
        if (this.cooldownTimerId) {
            clearInterval(this.cooldownTimerId);
            this.cooldownTimerId = null;
        }
        const renderCooldown = () => {
            if (this.cooldownStatusEl) {
                if (this.cooldownRemaining > 0) {
                    this.cooldownStatusEl.textContent = formatMessage(
                        this.config.i18n.waitSeconds,
                        { seconds: this.cooldownRemaining },
                    );
                    this.cooldownStatusEl.className = "pw-cooldown-wait";
                } else {
                    this.cooldownStatusEl.textContent = this.config.i18n.ready;
                    this.cooldownStatusEl.className = "pw-cooldown-ready";
                }
            }
            this.updatePlaceButtonState();
        };

        renderCooldown();
        if (this.cooldownRemaining > 0) {
            this.cooldownTimerId = setInterval(() => {
                this.cooldownRemaining = Math.max(0, this.cooldownRemaining - 1);
                renderCooldown();
                if (this.cooldownRemaining === 0 && this.cooldownTimerId) {
                    clearInterval(this.cooldownTimerId);
                    this.cooldownTimerId = null;
                    if (this.pixelsRemaining === 0) {
                        this.pixelsRemaining = Number(this.config.pixelsPerMinute) || 6;
                        if (this.pixelsRemainingEl) {
                            this.pixelsRemainingEl.textContent = String(this.pixelsRemaining);
                        }
                    }
                    this.updatePlaceButtonState();
                }
            }, 1000);
        }
    }

    loadRecentColors() {
        try {
            const raw = localStorage.getItem(RECENT_COLORS_KEY);
            const parsed = raw ? JSON.parse(raw) : [];
            if (Array.isArray(parsed)) {
                return parsed.filter((c) => /^#[0-9A-Fa-f]{6}$/.test(c)).slice(0, 6);
            }
        } catch {
            // Storage unavailable
        }
        return [];
    }

    recordRecentColor(color) {
        const next = [color, ...this.recentColors.filter((c) => c !== color)].slice(0, 6);
        this.recentColors = next;
        try {
            localStorage.setItem(RECENT_COLORS_KEY, JSON.stringify(next));
        } catch {
            // Storage unavailable
        }
        this.renderRecentColors();
    }

    renderRecentColors() {
        if (!this.recentSection || !this.recentColorsEl) return;
        if (!this.recentColors.length) {
            this.recentSection.hidden = true;
            return;
        }
        this.recentSection.hidden = false;
        this.recentColorsEl.textContent = "";
        for (const color of this.recentColors) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "pw-swatch";
            btn.dataset.color = color;
            btn.style.backgroundColor = color;
            btn.setAttribute("aria-label", color);
            btn.setAttribute("aria-pressed", String(color === this.selectedColor));
            if (color === this.selectedColor) btn.classList.add("is-selected");
            btn.addEventListener("click", () => this.selectColor(color));
            this.recentColorsEl.appendChild(btn);
        }
    }

    announceStatus(message) {
        if (this.statusLiveEl) {
            this.statusLiveEl.textContent = message;
        }
    }

    showAlert(message) {
        if (!this.alertLiveEl) return;
        this.alertLiveEl.textContent = message;
        this.alertLiveEl.hidden = false;
        if (this.alertTimerId) clearTimeout(this.alertTimerId);
        this.alertTimerId = setTimeout(() => {
            this.alertLiveEl.hidden = true;
        }, 4000);
    }

    drawCellOffscreen(x, y, color) {
        this.offCtx.fillStyle = color;
        this.offCtx.fillRect(x, y, 1, 1);
    }

    scheduleRender() {
        if (this.renderPending) return;
        this.renderPending = true;
        requestAnimationFrame(() => {
            this.renderPending = false;
            this.render();
        });
    }

    render() {
        if (!this.cssWidth || !this.cssHeight) return;
        const dpr = window.devicePixelRatio || 1;
        this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

        this.ctx.fillStyle = "#0f111d";
        this.ctx.fillRect(0, 0, this.cssWidth, this.cssHeight);

        const drawW = this.gridWidth * this.scale;
        const drawH = this.gridHeight * this.scale;

        this.ctx.imageSmoothingEnabled = false;
        this.ctx.drawImage(this.offscreen, this.offsetX, this.offsetY, drawW, drawH);

        this.ctx.strokeStyle = "rgba(255, 255, 255, 0.35)";
        this.ctx.lineWidth = 1.5;
        this.ctx.strokeRect(this.offsetX, this.offsetY, drawW, drawH);

        if (this.showGrid && this.scale >= 6) {
            const startX = Math.max(0, Math.floor(-this.offsetX / this.scale));
            const endX = Math.min(this.gridWidth, Math.ceil((this.cssWidth - this.offsetX) / this.scale));
            const startY = Math.max(0, Math.floor(-this.offsetY / this.scale));
            const endY = Math.min(this.gridHeight, Math.ceil((this.cssHeight - this.offsetY) / this.scale));

            this.ctx.strokeStyle = "rgba(15, 23, 42, 0.14)";
            this.ctx.lineWidth = 1;
            this.ctx.beginPath();
            for (let x = startX; x <= endX; x++) {
                const sx = Math.round(this.offsetX + x * this.scale) + 0.5;
                this.ctx.moveTo(sx, Math.max(this.offsetY, 0));
                this.ctx.lineTo(sx, Math.min(this.offsetY + drawH, this.cssHeight));
            }
            for (let y = startY; y <= endY; y++) {
                const sy = Math.round(this.offsetY + y * this.scale) + 0.5;
                this.ctx.moveTo(Math.max(this.offsetX, 0), sy);
                this.ctx.lineTo(Math.min(this.offsetX + drawW, this.cssWidth), sy);
            }
            this.ctx.stroke();
        }

        if (this.hoveredCell) {
            this.drawHighlight(this.hoveredCell.x, this.hoveredCell.y, false);
        }
        const activeCell = this.selectedCell || this.keyboardCursor;
        if (activeCell) {
            this.drawHighlight(activeCell.x, activeCell.y, true);
        }
    }

    drawHighlight(gx, gy, isSelected) {
        const sx = this.offsetX + gx * this.scale;
        const sy = this.offsetY + gy * this.scale;
        const size = this.scale;

        if (isSelected && this.confirmMode) {
            this.ctx.fillStyle = this.selectedColor;
            this.ctx.globalAlpha = 0.65;
            this.ctx.fillRect(sx, sy, size, size);
            this.ctx.globalAlpha = 1;
        }

        this.ctx.strokeStyle = "#FFFFFF";
        this.ctx.lineWidth = isSelected ? 3 : 2;
        this.ctx.strokeRect(sx, sy, size, size);

        this.ctx.strokeStyle = isSelected ? "#FE4901" : "#151625";
        this.ctx.lineWidth = isSelected ? 1.5 : 1;
        this.ctx.strokeRect(sx, sy, size, size);
    }

    async fetchCanvasState() {
        const response = await fetch(this.config.apiUrls.canvasState, {
            credentials: "same-origin",
        });
        if (!response.ok) return;
        const data = await response.json();
        if (!data.success || !data.pixels) return;

        this.pixels.clear();
        this.initOffscreen();
        for (const [key, pixel] of Object.entries(data.pixels)) {
            const [xStr, yStr] = key.split(",");
            const x = Number(xStr);
            const y = Number(yStr);
            if (!Number.isNaN(x) && !Number.isNaN(y)) {
                this.pixels.set(key, pixel);
                this.drawCellOffscreen(x, y, pixel.color);
            }
        }
        if (this.pixelCountEl) {
            this.pixelCountEl.textContent = String(
                data.pixel_count !== undefined ? data.pixel_count : this.pixels.size,
            );
        }
        this.updateInspector(this.hoveredCell || this.selectedCell);
        this.scheduleRender();
    }

    async fetchHistory() {
        if (!this.activityListEl) return;
        const response = await fetch(this.config.apiUrls.pixelHistory, {
            credentials: "same-origin",
        });
        if (!response.ok) return;
        const data = await response.json();
        if (!data.success || !Array.isArray(data.history)) return;

        this.activityListEl.textContent = "";
        if (data.history.length === 0) {
            const empty = document.createElement("li");
            empty.className = "pw-activity-empty";
            empty.textContent = this.config.i18n.noActivity;
            this.activityListEl.appendChild(empty);
            return;
        }

        for (const item of data.history) {
            const li = document.createElement("li");
            li.className = "pw-activity-item";

            const swatch = document.createElement("span");
            swatch.className = "pw-activity-swatch";
            swatch.style.backgroundColor = item.color;
            swatch.setAttribute("aria-hidden", "true");

            const meta = document.createElement("div");
            meta.className = "pw-activity-meta";

            const who = document.createElement("span");
            who.className = "pw-activity-who";
            who.textContent = item.placed_by || this.config.i18n.anonymous;

            const coords = document.createElement("span");
            coords.className = "pw-activity-coords";
            coords.textContent = `(${item.x}, ${item.y}) · ${item.color}`;

            meta.appendChild(who);
            meta.appendChild(coords);
            li.appendChild(swatch);
            li.appendChild(meta);
            this.activityListEl.appendChild(li);
        }
    }

    async refreshAll() {
        try {
            await Promise.all([this.fetchCanvasState(), this.fetchHistory()]);
        } catch {
            // Transient poll failures recover on next cycle
        }
    }

    startPolling() {
        const tick = () => {
            if (!document.hidden) {
                this.refreshAll();
            }
        };
        this.pollTimerId = setInterval(tick, POLL_INTERVAL_MS);
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden) {
                this.refreshAll();
            }
        });
    }

    async placePixel(x, y) {
        if (!this.config.isActive) {
            this.showAlert(this.config.i18n.canvasInactive);
            return;
        }
        if (this.cooldownRemaining > 0) {
            this.showAlert(
                formatMessage(this.config.i18n.limitReached, {
                    seconds: this.cooldownRemaining,
                }),
            );
            return;
        }
        if (this.isPlacing) return;

        const key = `${x},${y}`;
        const prevPixel = this.pixels.get(key) || null;
        const color = this.selectedColor;

        this.isPlacing = true;
        this.updatePlaceButtonState();

        // Optimistic paint
        this.pixels.set(key, {
            color,
            placed_by: this.config.i18n.anonymous,
            placed_at: new Date().toISOString(),
        });
        this.drawCellOffscreen(x, y, color);
        this.scheduleRender();

        try {
            const response = await fetch(this.config.apiUrls.placePixel, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCsrfToken(),
                },
                body: JSON.stringify({
                    canvas_id: this.config.canvasId,
                    x,
                    y,
                    color,
                }),
            });

            let data = {};
            try {
                data = await response.json();
            } catch {
                data = {};
            }

            if (!response.ok || !data.success) {
                if (prevPixel) {
                    this.pixels.set(key, prevPixel);
                    this.drawCellOffscreen(x, y, prevPixel.color);
                } else {
                    this.pixels.delete(key);
                    this.drawCellOffscreen(x, y, "#FFFFFF");
                }
                this.scheduleRender();

                if (response.status === 429 && data.cooldown_remaining) {
                    if (
                        data.limit_info &&
                        typeof data.limit_info.max_per_minute === "number" &&
                        typeof data.limit_info.placed_this_minute === "number"
                    ) {
                        this.pixelsRemaining = Math.max(
                            0,
                            data.limit_info.max_per_minute - data.limit_info.placed_this_minute,
                        );
                        if (this.pixelsRemainingEl) {
                            this.pixelsRemainingEl.textContent = String(this.pixelsRemaining);
                        }
                    }
                    this.startCooldown(data.cooldown_remaining);
                    this.showAlert(
                        formatMessage(this.config.i18n.limitReached, {
                            seconds: data.cooldown_remaining,
                        }),
                    );
                } else {
                    this.showAlert(data.error || this.config.i18n.networkError);
                }
                return;
            }

            this.pixels.set(key, {
                color: data.pixel.color,
                placed_by: data.pixel.placed_by,
                placed_at: data.pixel.placed_at,
            });
            this.drawCellOffscreen(x, y, data.pixel.color);
            this.recordRecentColor(data.pixel.color);

            if (this.pixelCountEl && !prevPixel) {
                this.pixelCountEl.textContent = String(this.pixels.size);
            }
            if (this.userTotalEl) {
                const currentTotal = Number(this.userTotalEl.textContent || "0");
                if (!Number.isNaN(currentTotal)) {
                    this.userTotalEl.textContent = String(currentTotal + 1);
                }
            }

            if (data.cooldown_info) {
                this.pixelsRemaining = Number(data.cooldown_info.pixels_remaining);
                if (this.pixelsRemainingEl) {
                    this.pixelsRemainingEl.textContent = String(this.pixelsRemaining);
                }
                if (data.cooldown_info.cooldown_seconds > 0) {
                    this.startCooldown(data.cooldown_info.cooldown_seconds);
                } else if (this.pixelsRemaining <= 0) {
                    this.startCooldown(60);
                }
            }

            this.updateInspector({ x, y });
            this.announceStatus(formatMessage(this.config.i18n.placed, { x, y }));
            this.scheduleRender();
            this.fetchHistory();
        } catch {
            if (prevPixel) {
                this.pixels.set(key, prevPixel);
                this.drawCellOffscreen(x, y, prevPixel.color);
            } else {
                this.pixels.delete(key);
                this.drawCellOffscreen(x, y, "#FFFFFF");
            }
            this.scheduleRender();
            this.showAlert(this.config.i18n.networkError);
        } finally {
            this.isPlacing = false;
            this.updatePlaceButtonState();
        }
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const config = readConfig();
    if (config) {
        new PixelWarClient(config);
    }
});
