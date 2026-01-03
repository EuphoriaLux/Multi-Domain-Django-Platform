/**
 * Rate Limiter Module
 * Handles rate limiting for pixel placement operations
 */

export class RateLimiter {
    constructor(maxPixelsPerMinute, cooldownSeconds) {
        this.maxPixelsPerMinute = maxPixelsPerMinute;
        this.cooldownSeconds = cooldownSeconds;
        this.pixelsRemaining = maxPixelsPerMinute;
        this.lastResetTime = Date.now();
        this.lastPlacementTime = 0;
        this.cooldownActive = false;
    }

    canPlace() {
        this.checkReset();
        return this.pixelsRemaining > 0;
    }

    recordPlacement() {
        this.checkReset();
        if (this.pixelsRemaining > 0) {
            this.pixelsRemaining--;
            this.lastPlacementTime = Date.now();
            this.cooldownActive = true;
            return true;
        }
        return false;
    }

    checkReset() {
        const now = Date.now();
        const timeSinceReset = (now - this.lastResetTime) / 1000;
        
        if (timeSinceReset >= 60) {
            this.pixelsRemaining = this.maxPixelsPerMinute;
            this.lastResetTime = now;
        }
    }

    getTimeUntilReset() {
        const now = Date.now();
        const timeSinceReset = (now - this.lastResetTime) / 1000;
        return Math.max(0, 60 - timeSinceReset);
    }

    updateFromServer(cooldownInfo) {
        if (cooldownInfo) {
            this.pixelsRemaining = cooldownInfo.pixels_remaining;
            // Update server-side cooldown status
            if (cooldownInfo.cooldown_remaining > 0) {
                this.cooldownActive = true;
                this.lastPlacementTime = Date.now() - ((this.cooldownSeconds - cooldownInfo.cooldown_remaining) * 1000);
            }
        }
    }

    // Get remaining cooldown time in seconds
    getCooldownRemaining() {
        if (!this.cooldownActive) return 0;
        
        const now = Date.now();
        const timeSincePlacement = (now - this.lastPlacementTime) / 1000;
        const remaining = this.cooldownSeconds - timeSincePlacement;
        
        if (remaining <= 0) {
            this.cooldownActive = false;
            return 0;
        }
        
        return remaining;
    }

    // Check if user can place a pixel (considering both per-minute limit and individual cooldown)
    canPlacePixel() {
        this.checkReset();
        return this.pixelsRemaining > 0 && this.getCooldownRemaining() === 0;
    }
}