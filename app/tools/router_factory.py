from __future__ import annotations

from ..services.overview import make_insight_tool
from .anomaly_tool import AnomalyTool
from .dataset_tool import DatasetSchemaTool, SearchMetadataTool
from .forecast_tool import ForecastTool
from .pandas_tool import PandasAnalysisTool
from .quality_tool import DataQualityTool
from .report_tool import ReportTool
from .base import ToolRouter
from .sql_tool import RunSQLTool
from .viz_tool import ChartTool


def build_router() -> ToolRouter:
    router = ToolRouter()
    for tool in (DatasetSchemaTool(), SearchMetadataTool(), DataQualityTool(), RunSQLTool(), PandasAnalysisTool(),
                 ChartTool(), AnomalyTool(), ForecastTool(), make_insight_tool(), ReportTool()):
        router.register(tool)
    return router
