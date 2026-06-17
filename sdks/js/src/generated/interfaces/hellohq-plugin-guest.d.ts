declare module 'hellohq:plugin/guest@0.1.0' {
  export function init(): void;
  export function run(input: Uint8Array): Uint8Array;
  export function metadata(): PluginMetadata;
  export interface PluginMetadata {
    id: string,
    version: string,
  }
}
