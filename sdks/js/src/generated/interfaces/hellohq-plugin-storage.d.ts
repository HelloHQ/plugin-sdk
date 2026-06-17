/// <reference path="./hellohq-plugin-types.d.ts" />
declare module 'hellohq:plugin/storage@0.1.0' {
  export function get(key: string): Uint8Array | undefined;
  export function set(key: string, value: Uint8Array): void;
  export { _delete as delete };
  function _delete(key: string): void;
  export function clear(): void;
  export function listKeys(): Array<string>;
  export type ApiError = import('hellohq:plugin/types@0.1.0').ApiError;
}
