# vastra-ml-p2 (Week 2, S4)

P2 Best-seller Stock-out Risk: the rule and a decision tree, compared fairly on the last 8 weeks, per store tier,
with an acceptance gate, three plain-word reasons per row, a one-page model card and a planner-approved daily list.

```
pip install -r requirements.txt             # installs ../vastra-data-platform too
python -m vastra_p2 contest                 # rule vs model, fairness table, gate, model card
python -m vastra_p2 sign --by "<name>"      # the owner signs the card; re-run contest
python -m vastra_p2 daily-list              # writes both lists; says which is in force and why
python -m vastra_p2 explain <row_id>        # three reasons for one row
jupyter notebook notebooks/P2_rule_vs_model.ipynb
python -m pytest -v                         # 8 tests
```

The Week 1 API (v1.1) serves the list to planners: `GET /v1/planner/risk-list`, approve / edit / reject with a
reason, weekly override review, and the kill switch `PUT /v1/admin/p2-mode`.
