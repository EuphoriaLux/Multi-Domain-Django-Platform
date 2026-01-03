/**
 * Simple Logger Utility
 * Provides consistent logging across the application
 */

import { Logger } from '../types/index.js';

export class ConsoleLogger implements Logger {
  constructor(
    private readonly prefix: string = 'PixelWar',
    private readonly level: 'debug' | 'info' | 'warn' | 'error' = 'info'
  ) {}

  debug(message: string, data?: any): void {
    if (this.shouldLog('debug')) {
      console.debug(`🐛 [${this.prefix}] ${message}`, data || '');
    }
  }

  info(message: string, data?: any): void {
    if (this.shouldLog('info')) {
      console.info(`ℹ️ [${this.prefix}] ${message}`, data || '');
    }
  }

  warn(message: string, data?: any): void {
    if (this.shouldLog('warn')) {
      console.warn(`⚠️ [${this.prefix}] ${message}`, data || '');
    }
  }

  error(message: string, data?: any): void {
    if (this.shouldLog('error')) {
      console.error(`❌ [${this.prefix}] ${message}`, data || '');
    }
  }

  private shouldLog(messageLevel: 'debug' | 'info' | 'warn' | 'error'): boolean {
    const levels = ['debug', 'info', 'warn', 'error'];
    const currentLevelIndex = levels.indexOf(this.level);
    const messageLevelIndex = levels.indexOf(messageLevel);
    
    return messageLevelIndex >= currentLevelIndex;
  }
}