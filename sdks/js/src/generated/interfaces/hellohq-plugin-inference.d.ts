/// <reference path="./hellohq-plugin-types.d.ts" />
declare module 'hellohq:plugin/inference@0.1.0' {
  export function complete(messages: Array<ChatMessage>, opts: InferenceOpts): ReadableStream<string>;
  export type ApiError = import('hellohq:plugin/types@0.1.0').ApiError;
  export interface ChatMessage {
    role: string,
    content: string,
  }
  /**
   * role: system|user|assistant
   */
  export interface InferenceOpts {
    maxTokens: number,
    temperature?: number,
  }
}
