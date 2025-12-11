/**
 * Pixel War API Module
 * Handles all API communication with the backend
 */

import { PixelWarConfig } from '../config/pixel-war-config.js';

export class PixelWarAPI {
    constructor(baseUrl = '') {
        this.baseUrl = this.normalizeBaseUrl(baseUrl);
        this.csrfToken = this.getCookie('csrftoken');
    }

    normalizeBaseUrl(url) {
        // Fix: Properly handle language prefixes
        const path = window.location.pathname;
        const langPattern = /^\/[a-z]{2}\//;
        
        if (langPattern.test(path)) {
            // Remove language prefix for API calls
            return url || '';
        }
        return url || '';
    }

    getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) {
            return parts.pop().split(';').shift();
        }
        return null;
    }

    async request(url, options = {}) {
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.csrfToken
            }
        };

        const mergedOptions = {
            ...defaultOptions,
            ...options,
            headers: {
                ...defaultOptions.headers,
                ...options.headers
            }
        };

        try {
            const response = await fetch(this.baseUrl + url, mergedOptions);
            const data = await response.json();
            
            if (!response.ok) {
                throw new APIError(data.error || 'Request failed', response.status, data);
            }
            
            return data;
        } catch (error) {
            if (error instanceof APIError) {
                throw error;
            }
            throw new APIError('Network error', 0, null);
        }
    }

    async placePixel(x, y, color, canvasId) {
        return this.request(PixelWarConfig.api.endpoints.placePixel, {
            method: 'POST',
            body: JSON.stringify({ x, y, color, canvas_id: canvasId })
        });
    }

    async getCanvasState(canvasId) {
        return this.request(`${PixelWarConfig.api.endpoints.canvasState}${canvasId}/`);
    }

    async getPixelHistory(canvasId, limit = 20) {
        return this.request(`${PixelWarConfig.api.endpoints.pixelHistory}?canvas_id=${canvasId}&limit=${limit}`);
    }
}

export class APIError extends Error {
    constructor(message, status, data) {
        super(message);
        this.status = status;
        this.data = data;
    }
}