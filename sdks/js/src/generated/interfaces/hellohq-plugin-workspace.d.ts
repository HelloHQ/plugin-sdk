/// <reference path="./hellohq-plugin-types.d.ts" />
declare module 'hellohq:plugin/workspace@0.1.0' {
  export function readPortfolioNames(): Array<PortfolioName>;
  export function readSheetStructure(portfolioId: string): SheetSummary;
  export function readAssetCount(portfolioId: string): AssetCount;
  export function readCurrencyRates(): Array<CurrencyRate>;
  export function readAggregatedValues(portfolioId: string): AggregatedSummary;
  /**
   * write:external_output — RESERVED (no Tier-2 wiring yet); shape reserved so
   * adding it later is non-breaking.
   */
  export function writeExternalFile(filename: string, content: Uint8Array): void;
  export type ApiError = import('hellohq:plugin/types@0.1.0').ApiError;
  export type PortfolioName = import('hellohq:plugin/types@0.1.0').PortfolioName;
  export type SheetSummary = import('hellohq:plugin/types@0.1.0').SheetSummary;
  export type AssetCount = import('hellohq:plugin/types@0.1.0').AssetCount;
  export type CurrencyRate = import('hellohq:plugin/types@0.1.0').CurrencyRate;
  export type AggregatedSummary = import('hellohq:plugin/types@0.1.0').AggregatedSummary;
}
