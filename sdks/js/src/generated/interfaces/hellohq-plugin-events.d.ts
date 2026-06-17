/// <reference path="./hellohq-plugin-types.d.ts" />
declare module 'hellohq:plugin/events@0.1.0' {
  export function emit(event: PluginEvent): void;
  export type ApiError = import('hellohq:plugin/types@0.1.0').ApiError;
  export interface PluginEvent {
    kind: string,
    payload: Uint8Array,
  }
}
