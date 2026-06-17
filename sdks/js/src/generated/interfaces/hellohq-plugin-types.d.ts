declare module 'hellohq:plugin/types@0.1.0' {
  /**
   * Gate denial, validation failure, or downstream error. Carries no secret,
   * raw prompt/response, credential id, or request id (the AI-harness boundary
   * rules). `code` is a stable machine token; `message` is safe to show the user.
   */
  export interface ApiError {
    code: string,
    /**
     * "permission-denied" | "origin-blocked" |
     * "address-blocked" | "rate-limited" | "not-found"
     */
    message: string,
  }
  export interface PortfolioName {
    id: string,
    name: string,
  }
  export interface CurrencyRate {
    id: string,
    /**
     * ISO 4217, e.g. "USD"
     */
    name: string,
    symbol: string,
    rate: number,
  }
  export interface SheetInfo {
    name: string,
    sections: Array<string>,
  }
  export interface SheetSummary {
    portfolioId: string,
    sheets: Array<SheetInfo>,
  }
  export interface CategoryCount {
    category: string,
    count: number,
  }
  export interface AssetCount {
    portfolioId: string,
    countByCategory: Array<CategoryCount>,
  }
  export interface CategoryTotal {
    category: string,
    total: number,
  }
  export interface AggregatedSummary {
    portfolioId: string,
    totals: Array<CategoryTotal>,
  }
}
