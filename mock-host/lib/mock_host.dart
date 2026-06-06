/// In-process mock of the HelloHQ host ABI for local plugin testing.
///
/// Lets authors exercise a plugin's host calls without running the full app:
/// seed fixture data and a permission allow-list, then resolve the same
/// permission-gated reads the real host exposes.
///
/// Status: skeleton (Phase 4 — begin). Mirrors the host's permission-gate
/// semantics (deny > grant) so tests fail the same way production does.
library;

class MockApiException implements Exception {
  final String code; // e.g. "permission-denied"
  final String detail;
  const MockApiException(this.code, this.detail);
  @override
  String toString() => 'MockApiException($code: $detail)';
}

class PortfolioName {
  final String id;
  final String name;
  const PortfolioName(this.id, this.name);
}

/// Fixture data + granted permissions for a mock run.
class MockHost {
  final Set<String> granted;
  final List<PortfolioName> portfolios;
  final Map<String, double> currencyRates; // "USD>EUR" -> rate

  MockHost({
    Iterable<String> granted = const [],
    this.portfolios = const [],
    this.currencyRates = const {},
  }) : granted = granted.toSet();

  void _require(String permission) {
    if (!granted.contains(permission)) {
      throw MockApiException('permission-denied', permission);
    }
  }

  List<PortfolioName> readPortfolioNames() {
    _require('read:portfolio_names');
    return portfolios;
  }

  double readCurrencyRate(String from, String to) {
    _require('read:currency_rates');
    final key = '$from>$to';
    final rate = currencyRates[key];
    if (rate == null) throw MockApiException('not-found', key);
    return rate;
  }

  // TODO(phase4): read-sheet-structure, read-asset-count,
  // read-aggregated-values (scoped), write-external-file, emit-event.
}
