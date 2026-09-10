"""
SENECIO ORACLE — ACT XXVII: Research subpackage
================================================

Institutional research-grade validation + observability layer built
additively on top of the existing oracle pipeline.

ACT-XXVII modules (all STRICT_ADDITIVE — none touch prediction_model /
feature_engineering / signal_generation / verifier):

  Priority 1 — Purged Walk-Forward Validation
    from .purged_cv import PurgedKFold, CombinatorialPurgedCV, run_purged_kfold, run_cpcv

  Priority 2 — Probability Calibration
    from .calibration import (
        PlattCalibrator, IsotonicCalibrator, BetaCalibrator, IdentityCalibrator,
        fit_and_evaluate, brier_score, expected_calibration_error,
        reliability_curve, maximum_calibration_error,
    )

  Priority 3 — Drift Detection
    from .drift_detector import (
        DriftMonitor, PSIDetector, KSDriftDetector,
        PageHinkleyDetector, ADWINDetector, psi_score,
    )

  Priority 4 — Research Metrics
    from .research_metrics import (
        information_coefficient, rolling_information_coefficient,
        feature_stability, prediction_stability,
        rolling_sharpe, rolling_profit_factor, rolling_max_drawdown,
        compute_research_metrics,
    )

  Priority 5 — Explainability
    from .explainability import (
        Explainer, fit_explainer, Attribution, PredictionExplanation,
    )

  Priority 6 — Observability
    from .observability import MetricsRegistry, get_registry, timed

  Coordinator (ties everything together)
    from .coordinator import ResearchCoordinator, ResearchPassReport

Public API:
    coord = ResearchCoordinator()
    coord.load_predictions()
    report = coord.run_full_pass()
"""
from .purged_cv import (
    PurgedKFold,
    PurgedFold,
    CombinatorialPurgedCV,
    CPCVPath,
    FoldResult,
    ValidationReport,
    run_purged_kfold,
    run_cpcv,
)
from .calibration import (
    Calibrator,
    IdentityCalibrator,
    PlattCalibrator,
    IsotonicCalibrator,
    BetaCalibrator,
    CalibrationReport,
    brier_score,
    reliability_curve,
    expected_calibration_error,
    maximum_calibration_error,
    fit_and_evaluate,
)
from .drift_detector import (
    DriftMonitor,
    DriftWarning,
    PSIDetector,
    KSDriftDetector,
    PageHinkleyDetector,
    ADWINDetector,
    psi_score,
)
from .research_metrics import (
    information_coefficient,
    rolling_information_coefficient,
    feature_stability,
    prediction_stability,
    rolling_sharpe,
    rolling_profit_factor,
    rolling_max_drawdown,
    ResearchMetricsReport,
    compute_research_metrics,
)
from .explainability import (
    Explainer,
    fit_explainer,
    Attribution,
    PredictionExplanation,
)
from .observability import (
    MetricSpec,
    DEFAULT_METRIC_SPECS,
    MetricsRegistry,
    get_registry,
    timed,
)
from .coordinator import (
    ResearchCoordinator,
    ResearchPassReport,
)
# ACT-XXVIII: institutional validation modules (STRICT_ADDITIVE)
from .walk_forward_optimizer import (
    WalkForwardWindow,
    WindowResult,
    WalkForwardReport,
    ParameterStabilityReport,
    generate_windows,
    default_score_fn,
    run_walk_forward,
    parameter_sweep,
)
from .monte_carlo_validation import (
    MonteCarloReport,
    run_monte_carlo,
    bootstrap_ci,
)
from .statistical_validation import (
    DeflatedSharpeReport,
    ProbabilisticSharpeReport,
    PBOTReport,
    RealityCheckReport,
    MultipleHypothesisReport,
    StatisticalValidationReport,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    pbo,
    white_reality_check,
    superior_predictive_ability,
    benjamini_hochberg,
    holm_bonferroni,
    multiple_hypothesis_correction,
    run_statistical_battery,
)
from .capacity_model import (
    ADVEstimate,
    MarketImpactEstimate,
    ScalabilityPoint,
    CapacityReport,
    estimate_adv,
    almgren_chriss_impact,
    kissell_linear_impact,
    estimate_market_impact,
    estimate_capacity,
)
from .stress_testing import (
    StressScenarioResult,
    StressReport,
    volatility_shock,
    spread_shock,
    latency_shock,
    exchange_outage,
    funding_shock,
    gap_simulation,
    black_swan,
    run_stress_battery,
)
from .institutional_report import (
    RobustnessScorecard,
    DeploymentReadinessScorecard,
    LiveGateExplanation,
    InstitutionalReport,
    build_robustness_scorecard,
    build_readiness_scorecard,
    explain_live_gate,
    build_institutional_report,
)

__all__ = [
    # Priority 1
    "PurgedKFold",
    "PurgedFold",
    "CombinatorialPurgedCV",
    "CPCVPath",
    "FoldResult",
    "ValidationReport",
    "run_purged_kfold",
    "run_cpcv",
    # Priority 2
    "Calibrator",
    "IdentityCalibrator",
    "PlattCalibrator",
    "IsotonicCalibrator",
    "BetaCalibrator",
    "CalibrationReport",
    "brier_score",
    "reliability_curve",
    "expected_calibration_error",
    "maximum_calibration_error",
    "fit_and_evaluate",
    # Priority 3
    "DriftMonitor",
    "DriftWarning",
    "PSIDetector",
    "KSDriftDetector",
    "PageHinkleyDetector",
    "ADWINDetector",
    "psi_score",
    # Priority 4
    "information_coefficient",
    "rolling_information_coefficient",
    "feature_stability",
    "prediction_stability",
    "rolling_sharpe",
    "rolling_profit_factor",
    "rolling_max_drawdown",
    "ResearchMetricsReport",
    "compute_research_metrics",
    # Priority 5
    "Explainer",
    "fit_explainer",
    "Attribution",
    "PredictionExplanation",
    # Priority 6
    "MetricSpec",
    "DEFAULT_METRIC_SPECS",
    "MetricsRegistry",
    "get_registry",
    "timed",
    # Coordinator
    "ResearchCoordinator",
    "ResearchPassReport",
    # ACT-XXVIII — Module 1: walk-forward optimizer
    "WalkForwardWindow",
    "WindowResult",
    "WalkForwardReport",
    "ParameterStabilityReport",
    "generate_windows",
    "default_score_fn",
    "run_walk_forward",
    "parameter_sweep",
    # ACT-XXVIII — Module 2: monte carlo validation
    "MonteCarloReport",
    "run_monte_carlo",
    "bootstrap_ci",
    # ACT-XXVIII — Module 3: statistical validation
    "DeflatedSharpeReport",
    "ProbabilisticSharpeReport",
    "PBOTReport",
    "RealityCheckReport",
    "MultipleHypothesisReport",
    "StatisticalValidationReport",
    "deflated_sharpe_ratio",
    "probabilistic_sharpe_ratio",
    "pbo",
    "white_reality_check",
    "superior_predictive_ability",
    "benjamini_hochberg",
    "holm_bonferroni",
    "multiple_hypothesis_correction",
    "run_statistical_battery",
    # ACT-XXVIII — Module 4: capacity model
    "ADVEstimate",
    "MarketImpactEstimate",
    "ScalabilityPoint",
    "CapacityReport",
    "estimate_adv",
    "almgren_chriss_impact",
    "kissell_linear_impact",
    "estimate_market_impact",
    "estimate_capacity",
    # ACT-XXVIII — Module 5: stress testing
    "StressScenarioResult",
    "StressReport",
    "volatility_shock",
    "spread_shock",
    "latency_shock",
    "exchange_outage",
    "funding_shock",
    "gap_simulation",
    "black_swan",
    "run_stress_battery",
    # ACT-XXVIII — Module 6: institutional report
    "RobustnessScorecard",
    "DeploymentReadinessScorecard",
    "LiveGateExplanation",
    "InstitutionalReport",
    "build_robustness_scorecard",
    "build_readiness_scorecard",
    "explain_live_gate",
    "build_institutional_report",
]

VERSION = "ACT-XXVIII-institutional-validation"
