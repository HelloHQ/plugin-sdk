declare module 'hellohq:plugin/log@0.1.0' {
  export function write(level: Level, message: string): void;
  /**
   * # Variants
   * 
   * ## `"trace"`
   * 
   * ## `"debug"`
   * 
   * ## `"info"`
   * 
   * ## `"warn"`
   * 
   * ## `"error"`
   */
  export type Level = 'trace' | 'debug' | 'info' | 'warn' | 'error';
}
