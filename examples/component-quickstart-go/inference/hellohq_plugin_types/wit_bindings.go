package hellohq_plugin_types

import (
        
)


// Gate denial, validation failure, or downstream error. Carries no secret,
// raw prompt/response, credential id, or request id (the AI-harness boundary
// rules). `code` is a stable machine token; `message` is safe to show the user.
type ApiError struct {
        Code string
// "permission-denied" | "origin-blocked" |
// "address-blocked" | "rate-limited" | "not-found"
Message string 
}

type PortfolioName struct {
        Id string
Name string 
}

type CurrencyRate struct {
        Id string
// ISO 4217, e.g. "USD"
Name string
Symbol string
Rate float64 
}

type SheetInfo struct {
        Name string
Sections []string 
}

type SheetSummary struct {
        PortfolioId string
Sheets []SheetInfo 
}

type CategoryCount struct {
        Category string
Count uint32 
}

type AssetCount struct {
        PortfolioId string
CountByCategory []CategoryCount 
}

type CategoryTotal struct {
        Category string
Total float64 
}

type AggregatedSummary struct {
        PortfolioId string
Totals []CategoryTotal 
}
