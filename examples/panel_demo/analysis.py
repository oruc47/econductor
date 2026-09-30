"""An optional runnable baseline for the synthetic panel demo."""

from __future__ import annotations

import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import statsmodels.formula.api as smf

project = Path(os.environ["PROJECT_ROOT"])
output = Path(os.environ["OUTPUT_DIR"])
data = pd.read_csv(project / "examples/panel_demo/panel.csv")
model = smf.ols("earnings ~ training + C(person) + C(year)", data=data).fit(cov_type="HC1")
estimate = float(model.params["training"])
se = float(model.bse["training"])
print(
    json.dumps(
        {
            "training_coefficient": estimate,
            "robust_standard_error": se,
            "observations": int(model.nobs),
        }
    )
)

fig, ax = plt.subplots(figsize=(6, 4))
data.groupby("training")["earnings"].mean().plot.bar(ax=ax, color=["#7b9acc", "#cf7a58"])
ax.set_xticks([0, 1], ["No training", "Training"], rotation=0)
ax.set_ylabel("Mean earnings")
fig.tight_layout()
fig.savefig(output / "mean_earnings_by_training.png", dpi=160)
